"""
按表更新时间规则做时间可用性校验，并在适用时生成表选择规则。

当前覆盖：
- 月报/快报表 dws_cgn_jq_zbval_month：数据 T 月在 T+1 月 12 日可查
- 年报/决算表 dws_cgn_jq_zbval_year：数据 Y 年在 Y+1 年 4 月 26 日可查
- 产权 dws_cqs_enterprise_query_view_full：数据 T 月在 T+1 月 2 日可查
- 税务 dws_tmis_tax_details_full：数据 T 月在 T+1 月 12 日可查
"""
import re
from datetime import datetime
from typing import Optional, Dict

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

# 产权相关关键词（按月时效）
CQS_KEYWORDS = [
    "产权",
    "法人户数",
    "法人数量",
]

# 税务相关关键词（按月时效）
TAX_KEYWORDS = [
    "税务",
    "纳税",
    "税额",
    "税费",
    "税金",
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


def _contains_any_keyword(question: str, keywords: list[str]) -> bool:
    q = (question or "").lower()
    for k in keywords:
        if k.lower() in q:
            return True
    return False


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _available_at_for_monthly_table(year: int, month: int, release_day_in_next_month: int) -> datetime:
    ny, nm = _next_month(year, month)
    # 规则口径：次月 X 号凌晨后，按次日可查（例如次月11号凌晨 -> 12号可查）
    return datetime(ny, nm, release_day_in_next_month + 1, 0, 0, 0)


def _available_at_for_yearly_table(year: int) -> datetime:
    # 规则口径：次年4月25号凌晨后，按4月26日可查
    return datetime(year + 1, 4, 26, 0, 0, 0)


def generate_table_selection_rule(
    question: str,
    current_time: Optional[str] = None,
    schema: Optional[str] = None,
) -> Optional[Dict[str, any]]:
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

    # 将「今年」「去年」「明年」等替换为具体年份后再提取时间，以支持「今年8月」等问法
    q_normalized = (
        question.replace("今年", f"{current_year}年")
        .replace("明年", f"{current_year + 1}年")
        .replace("去年", f"{current_year - 1}年")
    )
    year, month = _extract_year_month(q_normalized)
    if year is None:
        return None

    schema_l = (schema or "").lower()
    q_is_jq = _contains_target_indicator(question)
    q_is_cqs = _contains_any_keyword(question, CQS_KEYWORDS)
    q_is_tax = _contains_any_keyword(question, TAX_KEYWORDS)
    s_has_jq = "jq_zbval" in schema_l
    s_has_cqs = "dws_cqs_enterprise_query_view_full" in schema_l
    s_has_tax = "dws_tmis_tax_details_full" in schema_l

    # 领域判定优先级：
    # 1) 用户问句关键词（最可靠）
    # 2) schema 兜底（仅在问句无明显领域词时）
    if q_is_cqs:
        domain = "cqs"
    elif q_is_tax:
        domain = "tax"
    elif q_is_jq:
        domain = "jq"
    elif s_has_jq and not (s_has_cqs or s_has_tax):
        domain = "jq"
    elif s_has_cqs and not (s_has_jq or s_has_tax):
        domain = "cqs"
    elif s_has_tax and not (s_has_jq or s_has_cqs):
        domain = "tax"
    else:
        domain = None

    # 规则 A：月报/快报表（dws_cgn_jq_zbval_month）与税务/产权均按“月”做时效判断
    if month is not None and domain in ("jq", "cqs", "tax"):
        # jq/tax: 次月11号凌晨 -> 次月12号可查；cqs: 次月1号凌晨 -> 次月2号可查
        release_day = 11
        table_name = "dws_cgn_jq_zbval_month"
        table_label = "月报/快报"
        if domain == "cqs":
            release_day = 1
            table_name = "dws_cqs_enterprise_query_view_full"
            table_label = "产权"
        elif domain == "tax":
            release_day = 11
            table_name = "dws_tmis_tax_details_full"
            table_label = "税务"

        available_at = _available_at_for_monthly_table(year, month, release_day_in_next_month=release_day)
        if current_dt < available_at:
            return {
                "status": "no_data",
                "message": (
                    f"你查询的时间为 {year}年{month}月，当前尚未到该批次可查询时间。"
                    f"{table_label}表 `{table_name}` 需在 {available_at.year}年{available_at.month}月{available_at.day}日 00:00 之后才可查询。"
                ),
                "rule_text": None,
                "table_type": None,
                "table_name": None,
                "data_source_hint": None,
                "year": year,
                "month": month,
            }
        if domain == "jq":
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

        # 对 cqs/tax 仅做时效拒答，不改写业务规则
        return None

    # 规则 B：年报/决算表（dws_cgn_jq_zbval_year）按“年”做时效判断与选表
    # 仅在 jq 指标意图下生效，避免影响其它按天更新表
    if domain != "jq":
        return None

    if month is not None:
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

    available_at = _available_at_for_yearly_table(year)
    if current_dt < available_at:
        # 特殊规则：在该年度年报尚未可查前，按该年12月月报口径返回数据（不报 no_data）
        rule_text = (
            f"<rule>\n"
            f"用户查询的是 {year}年年度指标数据，但当前尚未到年报表 dws_cgn_jq_zbval_year 的可查询时点。"
            f"必须改用月报表 dws_cgn_jq_zbval_month，并固定查询 {year}年12月 数据"
            f"（例如：年份字段 = {year} AND 月份字段 = 12）。\n"
            f"</rule>"
        )
        return {
            "status": "rule",
            "rule_text": rule_text,
            "table_type": "month",
            "table_name": "dws_cgn_jq_zbval_month",
            "data_source_hint": (
                f"> [!TIP]\n> **数据来源提示**：当前尚未到 {year} 年年报可查询时点，"
                f"本次按月报快报表（`dws_cgn_jq_zbval_month`）中 {year}年12月 数据口径返回。"
            ),
            "year": year,
        }

    # 可查询时：使用年报表
    rule_text = (
        f"<rule>\n"
        f"用户查询的是 {year}年的指标数据，必须使用年报表 dws_cgn_jq_zbval_year 进行查询。"
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
