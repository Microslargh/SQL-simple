"""智能上下文提取器"""

import re
from typing import Dict, List, Optional, Any
import orjson
import sqlparse
from sqlalchemy.orm import Session

from apps.chat.models.chat_model import ChatLog, ChatRecord
from common.utils.utils import _async_log_util


class EntityReferenceExtractor:
    """提取实体引用上下文"""
    
    def __init__(self, session: Session):
        self.session = session
    
    def extract_companies(self, history_data: Dict[str, Any], max_count: int = 50) -> List[str]:
        """从历史数据中提取公司列表（仅公司名）
        
        Args:
            history_data: SQL执行结果数据字典
            max_count: 最大提取数量，避免列表过长
            
        Returns:
            公司名称列表
        """
        try:
            if not history_data or not isinstance(history_data, dict):
                return []
            
            fields = history_data.get('fields', [])
            data = history_data.get('data', [])
            
            if not fields or not data:
                return []
            
            # 识别公司名字段（常见字段名）
            company_field_names = ['company_name', 'company', 'companyname', '企业名称', '公司名称', '企业名', '公司名']
            
            # 获取字段名列表
            field_names = []
            if isinstance(fields[0], dict):
                field_names = [field.get('name', '') for field in fields]
            else:
                field_names = [str(field) for field in fields]
            
            # 查找公司名字段
            company_field = None
            for field_name in field_names:
                if field_name.lower() in [cf.lower() for cf in company_field_names]:
                    company_field = field_name
                    break
            
            if not company_field:
                return []
            
            # 提取公司名列表
            companies = []
            for row in data[:max_count]:
                if isinstance(row, dict):
                    company = row.get(company_field, '')
                    if company and company not in companies:
                        companies.append(str(company))
            
            _async_log_util.info(f"[上下文提取] 提取到 {len(companies)} 个公司名称")
            return companies
            
        except Exception as e:
            _async_log_util.debug(f"[上下文提取] 提取公司列表失败: {e}")
            return []
    
    def extract_time_range(self, history_sql: str) -> Optional[Dict[str, str]]:
        """从历史SQL中提取时间范围
        
        Args:
            history_sql: 历史SQL语句
            
        Returns:
            时间范围字典，如 {"start": "202501", "end": "202512"} 或 {"time": "202501"}
        """
        try:
            if not history_sql:
                return None
            
            # 1. 匹配 BETWEEN 'YYYYMM' AND 'YYYYMM' 格式（年月格式）
            between_pattern_ym = r"(?:year_month|yearmonth|ym)\s*BETWEEN\s+['\"]?(\d{6})['\"]?\s+AND\s+['\"]?(\d{6})['\"]?"
            match = re.search(between_pattern_ym, history_sql, re.IGNORECASE)
            if match:
                return {"start": match.group(1), "end": match.group(2), "format": "YYYYMM"}
            
            # 2. 匹配 BETWEEN 'YYYYMMDD' AND 'YYYYMMDD' 格式（日期格式）
            between_pattern_date = r"(?:date|create_time|update_time)\s*BETWEEN\s+['\"]?(\d{8})['\"]?\s+AND\s+['\"]?(\d{8})['\"]?"
            match = re.search(between_pattern_date, history_sql, re.IGNORECASE)
            if match:
                return {"start": match.group(1), "end": match.group(2), "format": "YYYYMMDD"}
            
            # 3. 匹配通用的 BETWEEN 格式（不指定字段名）
            between_pattern_generic = r"BETWEEN\s+['\"]?(\d{6,8})['\"]?\s+AND\s+['\"]?(\d{6,8})['\"]?"
            match = re.search(between_pattern_generic, history_sql, re.IGNORECASE)
            if match:
                start = match.group(1)
                end = match.group(2)
                # 根据长度判断格式
                if len(start) == 6 and len(end) == 6:
                    return {"start": start, "end": end, "format": "YYYYMM"}
                elif len(start) == 8 and len(end) == 8:
                    return {"start": start, "end": end, "format": "YYYYMMDD"}
            
            # 4. 匹配 >= 和 <= 组合（时间范围）
            gte_lte_pattern = r"(['\"]?\d{6,8}['\"]?)\s*>=\s*.*?(['\"]?\d{6,8}['\"]?)\s*<="
            match = re.search(gte_lte_pattern, history_sql, re.IGNORECASE)
            if match:
                start = match.group(1).strip("'\"")
                end = match.group(2).strip("'\"")
                if len(start) == len(end):
                    format_type = "YYYYMM" if len(start) == 6 else "YYYYMMDD"
                    return {"start": start, "end": end, "format": format_type}
            
            # 5. 匹配 = 'YYYYMM' 或 = 'YYYYMMDD' 格式（单个时间点）
            equal_pattern = r"=\s+['\"]?(\d{6,8})['\"]?"
            match = re.search(equal_pattern, history_sql, re.IGNORECASE)
            if match:
                time_value = match.group(1)
                format_type = "YYYYMM" if len(time_value) == 6 else "YYYYMMDD"
                return {"time": time_value, "format": format_type}
            
            # 6. 匹配 IN ('YYYYMM', 'YYYYMM', ...) 格式（多个时间点）
            in_pattern = r"IN\s*\(\s*['\"]?(\d{6,8})['\"]?(?:\s*,\s*['\"]?\d{6,8}['\"]?)*\s*\)"
            match = re.search(in_pattern, history_sql, re.IGNORECASE)
            if match:
                # 提取所有时间值
                time_values = re.findall(r"['\"]?(\d{6,8})['\"]?", match.group(0))
                if time_values:
                    # 如果只有一个值，返回单个时间
                    if len(time_values) == 1:
                        time_value = time_values[0]
                        format_type = "YYYYMM" if len(time_value) == 6 else "YYYYMMDD"
                        return {"time": time_value, "format": format_type}
                    # 如果有多个值，返回范围
                    else:
                        sorted_values = sorted(time_values)
                        start = sorted_values[0]
                        end = sorted_values[-1]
                        format_type = "YYYYMM" if len(start) == 6 else "YYYYMMDD"
                        return {"start": start, "end": end, "format": format_type}
            
            return None
            
        except Exception as e:
            _async_log_util.debug(f"[上下文提取] 提取时间范围失败: {e}")
            return None
    
    def extract_entities_from_data(self, history_data: Dict[str, Any], entity_type: str, max_count: int = 50) -> List[str]:
        """从历史数据中提取指定类型的实体列表
        
        Args:
            history_data: SQL执行结果数据字典
            entity_type: 实体类型（如 "company", "city", "province"）
            max_count: 最大提取数量
            
        Returns:
            实体值列表
        """
        try:
            if not history_data or not isinstance(history_data, dict):
                return []
            
            fields = history_data.get('fields', [])
            data = history_data.get('data', [])
            
            if not fields or not data:
                return []
            
            # 获取字段名列表
            field_names = []
            if isinstance(fields[0], dict):
                field_names = [field.get('name', '') for field in fields]
            else:
                field_names = [str(field) for field in fields]
            
            # 根据实体类型匹配字段名
            entity_field = None
            entity_keywords = {
                'company': ['company', '企业', '公司'],
                'city': ['city', '城市', '市'],
                'province': ['province', '省', '省份'],
                'country': ['country', '国家'],
            }
            
            keywords = entity_keywords.get(entity_type.lower(), [])
            for field_name in field_names:
                if any(keyword.lower() in field_name.lower() for keyword in keywords):
                    entity_field = field_name
                    break
            
            if not entity_field:
                return []
            
            # 提取实体值列表
            entities = []
            for row in data[:max_count]:
                if isinstance(row, dict):
                    entity_value = row.get(entity_field, '')
                    if entity_value and entity_value not in entities:
                        entities.append(str(entity_value))
            
            _async_log_util.info(f"[上下文提取] 提取到 {len(entities)} 个{entity_type}实体")
            return entities
            
        except Exception as e:
            _async_log_util.debug(f"[上下文提取] 提取{entity_type}实体失败: {e}")
            return []


class SQLPatternExtractor:
    """提取SQL结构模式"""
    
    def extract_pattern(self, sql: str) -> Optional[str]:
        """提取SQL结构模式，将具体值替换为占位符
        
        Args:
            sql: 原始SQL语句
            
        Returns:
            SQL结构模式（占位符形式），如 "SELECT {field} FROM {table} WHERE {filter_field} = {filter_value}"
        """
        try:
            if not sql or not sql.strip():
                return None
            
            # 解析SQL
            parsed = sqlparse.parse(sql)
            if not parsed:
                return None
            
            # 简单的模式提取：将字符串值替换为占位符
            pattern = sql
            
            # 替换字符串字面量
            pattern = re.sub(r"'[^']*'", "{value}", pattern)
            pattern = re.sub(r'"[^"]*"', "{value}", pattern)
            
            # 替换数字字面量（但保留运算符和函数）
            pattern = re.sub(r'\b\d+\b', "{number}", pattern)
            
            # 简化：移除过多的占位符细节，只保留结构
            # 例如：WHERE city = {value} AND time = {value} -> WHERE {filter_conditions}
            
            # 提取关键结构：SELECT ... FROM ... WHERE ...
            select_match = re.search(r'SELECT\s+(.+?)\s+FROM', pattern, re.IGNORECASE)
            from_match = re.search(r'FROM\s+([^\s]+)', pattern, re.IGNORECASE)
            where_match = re.search(r'WHERE\s+(.+?)(?:\s+GROUP|\s+ORDER|\s+LIMIT|$)', pattern, re.IGNORECASE)
            
            if select_match and from_match:
                # 构建简化模式
                select_part = select_match.group(1)
                from_part = from_match.group(1)
                where_part = where_match.group(1) if where_match else None
                
                # 简化SELECT部分
                if ',' in select_part:
                    select_part = "{fields}"
                else:
                    select_part = "{field}"
                
                # 简化FROM部分（移除schema前缀）
                from_part = re.sub(r'[^.]+\\.', '', from_part)
                from_part = "{table}"
                
                # 简化WHERE部分
                if where_part:
                    where_part = "{filter_conditions}"
                    pattern = f"SELECT {select_part} FROM {from_part} WHERE {where_part}"
                else:
                    pattern = f"SELECT {select_part} FROM {from_part}"
                
                _async_log_util.info(f"[上下文提取] SQL模式提取成功: {pattern[:100]}")
                return pattern
            
            return None
            
        except Exception as e:
            _async_log_util.debug(f"[上下文提取] SQL模式提取失败: {e}")
            return None


class IntentContinuityAnalyzer:
    """分析对话意图连续性"""
    
    def analyze_intent(self, current_question: str, history_question: str, history_sql: Optional[str] = None) -> Optional[str]:
        """分析当前问题与历史问题的意图关系
        
        Args:
            current_question: 当前用户问题
            history_question: 历史用户问题
            history_sql: 历史SQL语句（可选）
            
        Returns:
            意图摘要字符串，如"延续上一轮的公司查询，新增营业收入维度"
        """
        try:
            if not current_question or not history_question:
                return None
            
            # 识别指代词
            reference_words = ['这些', '它们', '上述', '上面', '刚才', '之前', '上一轮', '刚才的']
            has_reference = any(word in current_question for word in reference_words)
            
            if not has_reference:
                return None
            
            # 分析意图变化
            intent_parts = []
            
            # 检查是否延续了实体查询
            entity_keywords = {
                'company': ['公司', '企业'],
                'time': ['时间', '日期', '月份', '年份'],
                'city': ['城市', '市'],
                'province': ['省', '省份'],
            }
            
            # 识别新增的查询维度
            new_dimensions = []
            dimension_keywords = {
                'revenue': ['营业收入', '收入', '营收'],
                'profit': ['利润', '盈利'],
                'count': ['数量', '个数', '多少'],
            }
            
            for dim, keywords in dimension_keywords.items():
                if any(keyword in current_question for keyword in keywords):
                    if not any(keyword in history_question for keyword in keywords):
                        new_dimensions.append(dim)
            
            # 构建意图摘要
            if new_dimensions:
                dim_names = {
                    'revenue': '营业收入',
                    'profit': '利润',
                    'count': '数量',
                }
                dim_str = '、'.join([dim_names.get(d, d) for d in new_dimensions])
                intent_parts.append(f"延续上一轮查询，新增{dim_str}维度")
            else:
                intent_parts.append("延续上一轮查询")
            
            # 如果有SQL，可以进一步分析
            if history_sql:
                # 检查SQL结构
                if 'SELECT' in history_sql.upper():
                    intent_parts.append("参考上一轮SQL结构")
            
            intent_summary = "，".join(intent_parts)
            _async_log_util.info(f"[上下文提取] 意图分析结果: {intent_summary}")
            return intent_summary
            
        except Exception as e:
            _async_log_util.debug(f"[上下文提取] 意图分析失败: {e}")
            return None


class FilterSlotExtractor:
    """从历史 SQL 的 WHERE 子句提取过滤条件（槽位），供语义仲裁者使用"""

    FIELD_TO_SLOT = {
        "year": "year",
        "month": "month",
        "create_date": "create_date",
        "is_exit_press_reduce": "is_consolidated",
        "is_consolidated": "is_consolidated",
        "register_status": "register_status",
        "name_1": "region",
        "gjczqyname": "group_company",
    }

    @classmethod
    def extract_slots(cls, history_sql: str) -> Dict[str, str]:
        """从 SQL 提取过滤条件槽位"""
        if not history_sql or not history_sql.strip():
            return {}
        sql = history_sql.strip()
        slots: Dict[str, str] = {}

        like_match = re.search(
            r"create_date\s+LIKE\s+['\"](\d{4})[-]?\d{0,2}%['\"]",
            sql, re.IGNORECASE
        )
        if like_match:
            slots["year"] = like_match.group(1)

        eq_pattern = re.compile(
            r"(\w+)\s*=\s*(?:['\"]([^'\"]*)['\"]|(\d+))",
            re.IGNORECASE
        )
        for m in eq_pattern.finditer(sql):
            field, str_val, num_val = m.group(1), m.group(2), m.group(3)
            val = str_val if str_val is not None else (num_val or "")
            slot_key = cls.FIELD_TO_SLOT.get(field.lower(), field.lower())
            slots[slot_key] = val

        if "is_exit_press_reduce" in sql.lower() and "是" in sql:
            slots["is_consolidated"] = "1"

        _async_log_util.info(f"[上下文提取] 从 SQL 提取槽位: {slots}")
        return slots
