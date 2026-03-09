"""问题增强器 - 自动补充历史问题中的关键信息"""

import re
from datetime import datetime
from typing import Optional, Dict, List
from common.utils.utils import _async_log_util


# 追问句式长度上限，超过则视为完整问句
_FOLLOW_UP_MAX_LEN = 25

def is_time_follow_up(question: str) -> bool:
    """判断是否为「时间追问」：只换了时间、其余延续上一问。如「2024年的呢？」「去年的呢？」「7月的呢」"""
    if not question or len(question.strip()) > _FOLLOW_UP_MAX_LEN:
        return False
    q = question.strip()
    if re.match(r"^(\d{4}年)(的)?呢？?$", q):
        return True
    if re.match(r"^(去年|今年|前年|明年)(的)?呢？?$", q):
        return True
    if re.match(r"^(\d{4}年)(的)?(呢)?[？?]?$", q):
        return True
    if len(q) <= 12 and "年" in q and "呢" in q:
        return True
    # 「X月的呢」：仅月份追问，年份从历史问题继承
    if re.match(r"^\d{1,2}月的呢？?$", q):
        return True
    return False


def is_region_follow_up(question: str) -> bool:
    """判断是否为「地区追问」：只换了地区、其余延续上一问。如「广东的呢？」「北京的呢？」"""
    if not question or len(question.strip()) > _FOLLOW_UP_MAX_LEN:
        return False
    q = question.strip()
    # XX省/市/区 + 的呢？ 或 XX省/市呢？
    if re.match(r"^(.+?)(省|市|区|县)(的)?呢？?$", q):
        return True
    # 短问且含地区词+呢
    region_keywords = ['省', '市', '区', '县', '广西', '广东', '北京', '上海', '深圳', '广州']
    if len(q) <= 15 and "呢" in q and any(k in q for k in region_keywords):
        return True
    return False


def is_metric_follow_up(question: str) -> bool:
    """判断是否为「指标追问」：只换了指标、其余延续上一问。如「营业收入呢？」「利润呢？」"""
    if not question or len(question.strip()) > _FOLLOW_UP_MAX_LEN:
        return False
    q = question.strip()
    # 以「呢？」结尾的短问，且含常见指标词
    metric_keywords = [
        '收入', '利润', '纳税', '营收', '同比', '环比', '金额', '税额',
        '成本', '费用', '毛利', '净利', '增长率', '占比', '数量'
    ]
    if not (q.endswith('呢？') or q.endswith('呢?')):
        return False
    if any(k in q for k in metric_keywords):
        return True
    # 短问「XX呢？」（2-10字+呢？）也视为可能的指标追问
    if re.match(r"^.{2,10}呢？?$", q):
        return True
    return False


def is_company_follow_up(question: str) -> bool:
    """判断是否为「公司主体追问」：只换了主体、其余延续上一问。如「集团的呢？」「子公司的呢？」"""
    if not question or len(question.strip()) > _FOLLOW_UP_MAX_LEN:
        return False
    q = question.strip()
    company_keywords = ['集团', '子公司', '分公司', '母公司', '总公司', '公司', '企业']
    if not ("呢" in q):
        return False
    if any(k in q for k in company_keywords):
        return True
    return False


def is_any_follow_up(question: str) -> bool:
    """判断是否为任意类型的追问（时间/地区/指标/公司主体），需结合历史问题做术语与上下文增强。"""
    return (
        is_time_follow_up(question)
        or is_region_follow_up(question)
        or is_metric_follow_up(question)
        or is_company_follow_up(question)
    )


class QuestionEnhancer:
    """问题增强器 - 从历史问题中提取关键信息并补充到当前问题"""
    
    def extract_key_info_from_question(self, question: str) -> Dict[str, Optional[str]]:
        """从问题中提取关键信息（时间、地区、其他筛选条件）
        
        Args:
            question: 用户问题
            
        Returns:
            关键信息字典，包含 time, city, province, district 等
        """
        key_info = {
            'time': None,
            'city': None,
            'province': None,
            'district': None,
            'other_filters': []
        }
        
        if not question:
            return key_info
        
        # 1. 提取时间信息
        # 匹配"2025年底"、"2025年12月"、"2025年"等格式
        time_patterns = [
            r'(\d{4})年(?:底|末)',
            r'(\d{4})年(\d{1,2})月',
            r'(\d{4})年',
            r'(\d{4})年(\d{1,2})月(\d{1,2})日',
        ]
        
        for pattern in time_patterns:
            match = re.search(pattern, question)
            if match:
                if len(match.groups()) == 1:
                    # 只有年份
                    key_info['time'] = f"{match.group(1)}年底"
                elif len(match.groups()) == 2:
                    # 年月
                    year = match.group(1)
                    month = match.group(2).zfill(2)
                    key_info['time'] = f"{year}年{month}月"
                elif len(match.groups()) == 3:
                    # 年月日
                    year = match.group(1)
                    month = match.group(2).zfill(2)
                    day = match.group(3).zfill(2)
                    key_info['time'] = f"{year}年{month}月{day}日"
                break
        
        # 2. 提取地区信息
        # 匹配"深圳市"、"福田区"、"广东省"等格式
        city_pattern = r'([^，,。.]+?)(?:市|城市)'
        match = re.search(city_pattern, question)
        if match:
            city = match.group(1)
            # 排除一些常见误匹配
            if city not in ['注册', '企业', '公司', '集团']:
                key_info['city'] = f"{city}市"
        
        # 匹配"XX区"、"XX县"等
        district_pattern = r'([^，,。.]+?)(?:区|县)'
        match = re.search(district_pattern, question)
        if match:
            district = match.group(1)
            if district not in ['注册', '企业', '公司', '集团']:
                key_info['district'] = f"{district}区"
        
        # 匹配"XX省"
        province_pattern = r'([^，,。.]+?)(?:省|省份)'
        match = re.search(province_pattern, question)
        if match:
            province = match.group(1)
            if province not in ['注册', '企业', '公司', '集团']:
                key_info['province'] = f"{province}省"
        
        # 3. 提取其他筛选条件（如"注册的"、"上市的"等）
        filter_keywords = ['注册', '上市', '国有', '民营', '外资']
        for keyword in filter_keywords:
            if keyword in question:
                key_info['other_filters'].append(keyword)
        
        _async_log_util.info(f"[问题增强] 从问题中提取的关键信息: {key_info}")
        return key_info
    
    def enhance_question_for_time_follow_up(self, current_question: str, history_question: str) -> Optional[str]:
        """针对「时间追问」做规则补充：用当前问里的时间替换历史问里的时间，得到完整问句。"""
        if not current_question or not history_question:
            return None
        q = current_question.strip()
        new_year = None
        new_month = None
        year_match = re.search(r"(\d{4})年", q)
        month_only_match = re.match(r"^(\d{1,2})月的呢？?$", q)
        if year_match:
            new_year = year_match.group(1)
        elif month_only_match:
            new_month = month_only_match.group(1).zfill(2)
            year_in_history = re.search(r"(\d{4})年", history_question)
            if year_in_history:
                new_year = year_in_history.group(1)
            else:
                new_year = str(datetime.now().year)
        elif "去年" in q:
            new_year = str(datetime.now().year - 1)
        elif "前年" in q:
            new_year = str(datetime.now().year - 2)
        elif "明年" in q:
            new_year = str(datetime.now().year + 1)
        elif "今年" in q:
            new_year = str(datetime.now().year)
        else:
            return None
        if new_month:
            # 「7月的呢」：用历史中的年份 + 当前月份替换历史中的年月；支持多种历史时间格式
            enhanced = re.sub(r"(\d{4})年\d{1,2}月", f"{new_year}年{new_month}月", history_question, count=1)
            if enhanced == history_question:
                # 历史为「YYYYMM月份」时：保留年份，只换月份，如 202602月份 -> 202607月份
                enhanced = re.sub(r"(\d{4})(\d{2})月份", lambda m: f"{m.group(1)}{new_month}月份", history_question, count=1)
            if enhanced == history_question:
                # 历史为「YYYYMM月」（无“份”）时同理
                enhanced = re.sub(r"(\d{4})(\d{2})月(?!份)", lambda m: f"{m.group(1)}{new_month}月", history_question, count=1)
        else:
            def replace_year(m):
                return new_year + m.group(2)
            enhanced = re.sub(r"(\d{4})(年(?:底|末)?|年\d{1,2}月?)", replace_year, history_question, count=1)
        if enhanced != history_question:
            _async_log_util.info(f"[问题增强-时间追问] 原始: {current_question}, 补充后: {enhanced}")
            return enhanced
        return None

    def enhance_question_for_region_follow_up(self, current_question: str, history_question: str) -> Optional[str]:
        """针对「地区追问」：用当前问里的地区替换历史问里的地区，得到完整问句。"""
        if not current_question or not history_question:
            return None
        current_info = self.extract_key_info_from_question(current_question)
        history_info = self.extract_key_info_from_question(history_question)
        new_region = current_info.get('province') or current_info.get('city') or current_info.get('district')
        if not new_region:
            return None
        enhanced = history_question
        # 历史中的地区（可能带省/市/区，也可能只有地名）
        old_region = history_info.get('province') or history_info.get('city') or history_info.get('district')
        if old_region:
            enhanced = enhanced.replace(old_region, new_region, 1)
        # 若历史里只有地名（如「在广西的」），再尝试只替换地名核心
        if enhanced == history_question and old_region:
            old_core = re.sub(r'(省|市|区|县)$', '', old_region)
            new_core = re.sub(r'(省|市|区|县)$', '', new_region)
            if old_core and new_core and old_core in history_question:
                enhanced = history_question.replace(old_core, new_core, 1)
        if enhanced != history_question:
            _async_log_util.info(f"[问题增强-地区追问] 原始: {current_question}, 补充后: {enhanced}")
            return enhanced
        return None

    def enhance_question_for_metric_follow_up(self, current_question: str, history_question: str) -> Optional[str]:
        """针对「指标追问」：用当前问里的指标替换历史问里的指标，得到完整问句。"""
        if not current_question or not history_question:
            return None
        q = current_question.strip()
        # 去掉「呢？」得到指标词
        metric_candidate = re.sub(r"(的)?呢？?$", "", q).strip()
        if not metric_candidate or len(metric_candidate) > 15:
            return None
        # 在历史问中找常见指标词并替换（取第一个匹配）
        metric_patterns = [
            r'纳税[^，。？?]*?(?:金额|总额|多少)?',
            r'营业[^，。？?]*?收入[^，。？?]*?',
            r'利润[^，。？?]*?',
            r'同比[^，。？?]*?',
            r'环比[^，。？?]*?',
        ]
        for pat in metric_patterns:
            m = re.search(pat, history_question)
            if m:
                enhanced = history_question[:m.start()] + metric_candidate + history_question[m.end():]
                if enhanced != history_question:
                    _async_log_util.info(f"[问题增强-指标追问] 原始: {current_question}, 补充后: {enhanced}")
                    return enhanced
                break
        return None

    def enhance_question_for_company_follow_up(self, current_question: str, history_question: str) -> Optional[str]:
        """针对「公司主体追问」：用当前问里的主体替换历史问里的主体，得到完整问句。"""
        if not current_question or not history_question:
            return None
        q = current_question.strip()
        company_terms = ['集团', '子公司', '分公司', '母公司', '总公司', '公司', '企业']
        new_entity = None
        for term in company_terms:
            if term in q:
                new_entity = term
                break
        if not new_entity:
            return None
        for term in company_terms:
            if term in history_question:
                enhanced = history_question.replace(term, new_entity, 1)
                if enhanced != history_question:
                    _async_log_util.info(f"[问题增强-公司主体追问] 原始: {current_question}, 补充后: {enhanced}")
                    return enhanced
                break
        return None

    def enhance_question(self, current_question: str, history_question: str) -> str:
        """增强当前问题，补充历史问题中的关键信息
        
        Args:
            current_question: 当前用户问题（可能包含指代词）
            history_question: 历史用户问题
            
        Returns:
            增强后的问题
        """
        if not current_question or not history_question:
            return current_question
        
        # 先处理「时间追问」
        if is_time_follow_up(current_question):
            expanded = self.enhance_question_for_time_follow_up(current_question, history_question)
            if expanded:
                return expanded
        # 再处理「地区追问」
        if is_region_follow_up(current_question):
            expanded = self.enhance_question_for_region_follow_up(current_question, history_question)
            if expanded:
                return expanded
        # 再处理「公司主体追问」（先于指标，避免「集团呢？」被当成指标）
        if is_company_follow_up(current_question):
            expanded = self.enhance_question_for_company_follow_up(current_question, history_question)
            if expanded:
                return expanded
        # 再处理「指标追问」
        if is_metric_follow_up(current_question):
            expanded = self.enhance_question_for_metric_follow_up(current_question, history_question)
            if expanded:
                return expanded

        # 检查是否包含指代词
        reference_words = ['这些', '它们', '上述', '上面', '刚才', '之前', '上一轮', '刚才的', '那些']
        has_reference = any(word in current_question for word in reference_words)
        
        if not has_reference:
            return current_question
        
        # 从历史问题中提取关键信息
        key_info = self.extract_key_info_from_question(history_question)
        
        # 检查当前问题是否已经包含这些信息
        current_info = self.extract_key_info_from_question(current_question)
        
        # 构建补充信息
        supplement_parts = []
        
        # 补充时间信息（如果当前问题没有）
        if key_info['time'] and not current_info['time']:
            supplement_parts.append(key_info['time'])
        
        # 补充地区信息（如果当前问题没有）
        if key_info['district'] and not current_info['district']:
            supplement_parts.append(key_info['district'])
        elif key_info['city'] and not current_info['city']:
            supplement_parts.append(key_info['city'])
        elif key_info['province'] and not current_info['province']:
            supplement_parts.append(key_info['province'])
        
        # 补充其他筛选条件
        for filter_keyword in key_info['other_filters']:
            if filter_keyword not in current_question:
                supplement_parts.append(filter_keyword)
        
        if not supplement_parts:
            return current_question
        
        # 构建增强后的问题
        # 将指代词替换为具体的描述
        enhanced_question = current_question
        
        # 替换"这些公司" -> "这些公司" + 补充信息
        # 例如："这些公司营业收入分别是多少？" -> "2025年底，深圳市福田区注册的这些公司营业收入分别是多少？"
        
        # 在指代词前插入补充信息
        for ref_word in reference_words:
            if ref_word in enhanced_question:
                # 找到指代词的位置
                ref_index = enhanced_question.find(ref_word)
                # 在指代词前插入补充信息
                supplement_str = "，".join(supplement_parts) + "，"
                enhanced_question = enhanced_question[:ref_index] + supplement_str + enhanced_question[ref_index:]
                break
        
        # 如果指代词在问题开头，直接在开头添加补充信息
        if enhanced_question == current_question and supplement_parts:
            # 没有找到指代词，但有关键信息需要补充，在问题开头添加
            supplement_str = "，".join(supplement_parts) + "，"
            enhanced_question = supplement_str + enhanced_question
        
        _async_log_util.info(f"[问题增强] 原始问题: {current_question}")
        _async_log_util.info(f"[问题增强] 增强后问题: {enhanced_question}")
        
        return enhanced_question
