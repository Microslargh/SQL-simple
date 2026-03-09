"""
年报表与月报表选择规则工具函数
根据用户问题中的时间粒度（年+月 vs 年）和当前时间，智能选择使用 dws_cgn_jq_zbval_year 还是 dws_cgn_jq_zbval_month
"""
import re
from datetime import datetime
from typing import Optional, Dict, Literal


# 需要应用此规则的指标关键词
TARGET_INDICATORS = [
    "经营情况",
    "一利五率",
    "EVA",
    "国有资本保值增值率",
    "两金构成",
    "亏损",
    "负债",
    "利润",
    "收入",
    "担保",
]


def _extract_year_month(question: str) -> tuple[Optional[int], Optional[int]]:
    """
    从问题中提取年份和月份
    返回: (year, month)，如果未找到则返回 (None, None)
    """
    # 匹配 "2025年1月"、"2025年01月"、"2025-01"、"202501" 等格式
    patterns = [
        r'(\d{4})年(\d{1,2})月',  # 2025年1月
        r'(\d{4})-(\d{1,2})',     # 2025-01
        r'(\d{4})(\d{2})',        # 202501
    ]
    
    year = None
    month = None
    
    for pattern in patterns:
        match = re.search(pattern, question)
        if match:
            year = int(match.group(1))
            month = int(match.group(2))
            if 1 <= month <= 12:
                return year, month
    
    # 只匹配年份
    year_patterns = [
        r'(\d{4})年',  # 2025年
        r'(\d{4})',    # 2025（但需要确保不是月份的一部分）
    ]
    
    for pattern in year_patterns:
        match = re.search(pattern, question)
        if match:
            year = int(match.group(1))
            # 检查是否是合理的年份（例如 2000-2100）
            if 2000 <= year <= 2100:
                return year, None
    
    return None, None


def _contains_target_indicator(question: str) -> bool:
    """检查问题是否包含目标指标关键词"""
    question_lower = question.lower()
    for indicator in TARGET_INDICATORS:
        if indicator in question_lower:
            return True
    return False


def generate_table_selection_rule(question: str, current_time: Optional[str] = None) -> Optional[Dict[str, any]]:
    """
    根据用户问题和当前时间，生成表选择规则信息
    
    Args:
        question: 用户问题
        current_time: 当前时间字符串，格式如 "2026-01-15 10:30:00"，如果为 None 则使用系统当前时间
    
    Returns:
        包含以下字段的字典（如果适用），否则返回 None:
        - status: "no_data" | "rule" - "no_data" 表示当前暂无数据或查询时间超出数据范围，"rule" 表示需要生成规则
        - message: 可选，当 status="no_data" 时的用户可见提示（如「当前数据库仅有2026年2月及以前的数据」）
        - rule_text: 规则文本（当 status="rule" 时）
        - table_type: "year" | "month" - 使用的表类型（当 status="rule" 时）
        - table_name: 表名（当 status="rule" 时）
        - data_source_hint: 数据来源提示文本（用于分析时的温馨提示）
    """
    # 检查是否包含目标指标
    if not _contains_target_indicator(question):
        return None
    
    # 解析当前时间（先解析，便于将「今年」「去年」「明年」转为具体年份）
    if current_time:
        try:
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d"]:
                try:
                    current_dt = datetime.strptime(current_time.strip(), fmt)
                    break
                except ValueError:
                    continue
            else:
                current_dt = datetime.now()
        except Exception:
            current_dt = datetime.now()
    else:
        current_dt = datetime.now()
    current_year = current_dt.year
    current_month = current_dt.month

    # 将「今年」「去年」「明年」等替换为具体年份后再提取时间，以支持「今年8月」等问法
    q_normalized = (
        question.replace("今年", f"{current_year}年")
        .replace("明年", f"{current_year + 1}年")
        .replace("去年", f"{current_year - 1}年")
    )
    year, month = _extract_year_month(q_normalized)
    if year is None:
        return None
    # 数据库通常滞后约 1 个月：当前 2026 年 3 月时，仅有 2026 年 2 月及以前数据
    if current_month <= 1:
        latest_year, latest_month = current_year - 1, 12
    else:
        latest_year, latest_month = current_year, current_month - 1

    # 规则1: 如果问题明确包含"年+月"（例如 "2025年1月"、"今年8月"），先判断是否超出数据范围
    if month is not None:
        if (year, month) > (latest_year, latest_month):
            return {
                "status": "no_data",
                "message": f"当前数据库仅有{latest_year}年{latest_month}月及以前的数据，无法提供{year}年{month}月的数据。请缩小时间范围后重试。",
                "rule_text": None,
                "table_type": None,
                "table_name": None,
                "data_source_hint": None,
                "year": year,
                "month": month,
            }
        rule_text = (
            f"<rule>\n"
            f"用户查询的是 {year}年{month}月的指标数据，必须使用月报表 dws_cgn_jq_zbval_month 进行查询。"
            f"查询条件应匹配年份和月份（例如：年份字段 = {year} AND 月份字段 = {month:02d} 或类似格式）。\n"
            f"</rule>"
        )
        return {
            "status": "rule",
            "rule_text": rule_text,
            "table_type": "month",
            "table_name": "dws_cgn_jq_zbval_month",
            "data_source_hint": "> [!TIP]\n> **数据来源提示**：当前数据来源于月报快报表（`dws_cgn_jq_zbval_month`）。"
        }
    
    # 规则2: 如果问题只包含年份
    # 情况2.1: 查询的是当前年份或未来年份，当前暂无数据
    if year >= current_year:
        return {
            "status": "no_data",
            "message": f"当前数据库仅有{latest_year}年{latest_month}月及以前的数据，无法提供{year}年的数据。请缩小时间范围后重试。",
            "rule_text": None,
            "table_type": None,
            "table_name": None,
            "data_source_hint": None,
            "year": year,
        }
    
    # 情况2.2: 查询的是上一年
    if year == current_year - 1:
        # 如果当前时间未超过3月，使用月报表的12月数据
        if current_month < 3:
            rule_text = (
                f"<rule>\n"
                f"用户查询的是 {year}年（上一年）的指标数据，当前时间为 {current_year}年{current_month}月（未超过3月），"
                f"必须使用月报表 dws_cgn_jq_zbval_month 进行查询，查询条件应匹配年份和月份（年份字段 = {year} AND 月份字段 = 12）。\n"
                f"</rule>"
            )
            return {
                "status": "rule",
                "rule_text": rule_text,
                "table_type": "month",
                "table_name": "dws_cgn_jq_zbval_month",
                "data_source_hint": f"> [!TIP]\n> **数据来源提示**：当前数据来源于月报快报表（`dws_cgn_jq_zbval_month`）中 {year}年12月的数据。"
            }
        else:
            # 当前时间超过3月，使用年报表的Q4数据（202504表示2025年Q4）
            q4_period = f"{year}04"  # 例如 202504 表示 2025年Q4
            rule_text = (
                f"<rule>\n"
                f"用户查询的是 {year}年（上一年）的指标数据，当前时间为 {current_year}年{current_month}月（已超过3月），"
                f"必须使用年报表 dws_cgn_jq_zbval_year 进行查询，查询条件应匹配年份和季度（例如：年份季度字段 = '{q4_period}' 或类似格式，表示 {year}年第四季度）。\n"
                f"</rule>"
            )
            return {
                "status": "rule",
                "rule_text": rule_text,
                "table_type": "year",
                "table_name": "dws_cgn_jq_zbval_year",
                "data_source_hint": f"> [!TIP]\n> **数据来源提示**：当前数据来源于年报决算表（`dws_cgn_jq_zbval_year`）中 {year}年第四季度的数据。"
            }
    
    # 情况2.3: 查询的是更早的年份（非上一年），使用年报表
    rule_text = (
        f"<rule>\n"
        f"用户查询的是 {year}年（更早年份）的指标数据，必须使用年报表 dws_cgn_jq_zbval_year 进行查询。"
        f"查询条件应匹配年份（例如：年份字段 = {year} 或类似格式）。\n"
        f"</rule>"
    )
    return {
        "status": "rule",
        "rule_text": rule_text,
        "table_type": "year",
        "table_name": "dws_cgn_jq_zbval_year",
        "data_source_hint": f"> [!TIP]\n> **数据来源提示**：当前数据来源于年报决算表（`dws_cgn_jq_zbval_year`）中 {year}年的数据。"
    }
