"""
久其（jq_zbval，字段常见为 bk）与产权（dws_cqs_*，产业/板块字段值）命名对照。

- canonical：以产权侧枚举为主键（与数据组「两套命名不一致」提醒一致）。
- SQL 落库前按目标表将「另一套」写法改写为当前表真实值，避免 WHERE 不命中。
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from common.utils.utils import _async_log_util

# 产权侧真实值（canonical 展示与 cqs SQL 目标值）
CQS_INDUSTRY_VALUES = (
    "新能源",
    "资本控股",
    "能源国际",
    "核技术",
    "核电",
    "集团/股份",
    "环保/节能",
    "核燃料",
    "核服",
    "英国核电",
    "数字化产业",
    "司库",
)

# 久其侧真实值（jq SQL 中 bk 目标值）
JQ_BK_VALUES = (
    "新能源",
    "非动力核技术",
    "核电",
    "能源国际",
    "科技型环保",
    "核燃料",
    "核服",
    "资本控股",
    "集团及直管公司",
    "数字化",
    "英国核电",
    "财务公司",
    "共享公司",
)

# 产权 canonical -> 久其 bk（一对一）
CANONICAL_TO_JQ_BK: Dict[str, str] = {
    "新能源": "新能源",
    "资本控股": "资本控股",
    "能源国际": "能源国际",
    "核技术": "非动力核技术",
    "核电": "核电",
    "集团/股份": "集团及直管公司",
    "环保/节能": "科技型环保",
    "核燃料": "核燃料",
    "核服": "核服",
    "英国核电": "英国核电",
    "数字化产业": "数字化",
    "司库": "财务公司",
}

# 久其 bk -> 产权 canonical
JQ_BK_TO_CANONICAL: Dict[str, str] = {v: k for k, v in CANONICAL_TO_JQ_BK.items()}

# 自然语言 / 历史别名 -> canonical（产权口径）
NL_ALIAS_TO_CANONICAL: Dict[str, str] = {
    # 旧版 question_enhance 兼容
    "集团股份产业": "集团/股份",
    "集团股份": "集团/股份",
    "股份产业": "集团/股份",
    "股份": "集团/股份",
    "环保节能": "环保/节能",
    "环保产业": "环保/节能",
    "节能环保": "环保/节能",
    "环保": "环保/节能",
    "数字产业": "数字化产业",
    "数字化": "数字化产业",
    # 久其口径 -> canonical
    "非动力核技术": "核技术",
    "科技型环保": "环保/节能",
    "集团及直管公司": "集团/股份",
    "财务公司": "司库",
}

# 供 question_enhance 使用的标准产业名列表（与 CQS 枚举一致）
INDUSTRY_CANONICAL_LIST = list(CQS_INDUSTRY_VALUES)


def canonical_from_any_phrase(text: str) -> Optional[str]:
    """从问句中识别产业 canonical（优先长词）。"""
    if not text:
        return None
    # 先匹配 canonical 本体
    for name in sorted(CQS_INDUSTRY_VALUES, key=len, reverse=True):
        if name in text:
            return name
    for alias, c in sorted(NL_ALIAS_TO_CANONICAL.items(), key=lambda x: len(x[0]), reverse=True):
        if alias in text:
            return c
    # 久其枚举原样命中
    for jq in sorted(JQ_BK_VALUES, key=len, reverse=True):
        if jq in text:
            return JQ_BK_TO_CANONICAL.get(jq)
    return None


def jq_bk_for_canonical(canonical: str) -> Optional[str]:
    return CANONICAL_TO_JQ_BK.get(canonical)


def cqs_plate_for_canonical(canonical: str) -> Optional[str]:
    if canonical in CQS_INDUSTRY_VALUES:
        return canonical
    return None


def _replace_quoted_after_col(sql: str, col_pattern: str, mapping: Dict[str, str], label: str) -> str:
    """将 col = 'old' 中的 old 按 mapping 替换（仅当 old 在 mapping 中）。"""
    if not sql or not mapping:
        return sql

    def repl(m: re.Match) -> str:
        col = m.group("col")
        q = m.group("q")
        val = m.group("val")
        new_val = mapping.get(val)
        if new_val is None or new_val == val:
            return m.group(0)
        return f'{col}={q}{new_val}{q}'

    # "bk" = 'x' 或 bk = 'x'
    pat = re.compile(
        rf'(?P<col>{col_pattern})\s*=\s*(?P<q>[\'"])(?P<val>[^\'"]*)(?P=q)',
        re.IGNORECASE,
    )
    new_sql = pat.sub(repl, sql)
    if new_sql != sql:
        _async_log_util.info(f"[产业映射] 已按{label}改写 bk/产业条件")
    return new_sql


def normalize_industry_literals_for_jq_zbval_sql(sql: str) -> str:
    """
    久其表：bk 等条件须用久其枚举。若模型写成产权口径，改为 jq bk。
    mapping: cqs 值 -> jq 值
    """
    if "dws_cgn_jq_zbval" not in sql.lower():
        return sql
    m: Dict[str, str] = {}
    for cqs_v, jq_v in CANONICAL_TO_JQ_BK.items():
        m[cqs_v] = jq_v
    # 若 SQL 里写产权「司库」-> 久其「财务公司」
    return _replace_quoted_after_col(sql, r'["\']?bk["\']?', m, "久其bk")


def normalize_industry_literals_for_cqs_sql(sql: str) -> str:
    """
    产权表：产业/板块字段须用产权枚举。若模型写成久其 bk，改为 cqs 值。
    mapping: jq 值 -> cqs 值
    """
    sl = sql.lower()
    if "dws_cqs" not in sl and "enterprise_query_view" not in sl:
        return sql
    m: Dict[str, str] = {}
    for jq_v, c in JQ_BK_TO_CANONICAL.items():
        # 目标写成产权侧存储值：canonical 即 cqs 行值
        cqs_literal = c if c in CQS_INDUSTRY_VALUES else None
        if cqs_literal and jq_v != cqs_literal:
            m[jq_v] = cqs_literal
    s = _replace_quoted_after_col(sql, r'["\']?bk["\']?', m, "产权bk")
    s = _replace_quoted_after_col(s, r'["\']?plate["\']?', m, "产权plate")
    return s


def normalize_cross_system_industry_in_sql(sql: str) -> str:
    """在落库前依次做 jq / cqs 侧归一（同一条 SQL 混表时两段各改各的）。"""
    s = normalize_industry_literals_for_jq_zbval_sql(sql)
    s = normalize_industry_literals_for_cqs_sql(s)
    return s
