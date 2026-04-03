"""
jq_zbval 年报/月报表的 zb_title 映射工具。

用途：
- 在同一 SQL 混用 dws_cgn_jq_zbval_year 与 dws_cgn_jq_zbval_month 时，
  根据指标语义核心返回各表可命中的 zb_title 候选，避免跨表复用同一 literal 导致漏数。
"""

from __future__ import annotations

import re
from typing import Dict, List

# 月报（dws_cgn_jq_zbval_month）已知指标标题全集
MONTH_TITLES: List[str] = [
    "资产总计_期末余额",
    "其他应收款_期末余额",
    "应收账款_期末余额",
    "存货_期末余额",
    "合同资产_期末余额",
    "长期应收款_期末余额",
    "四、利润总额（亏损总额以“-”号填列）_本年累计数",
    "一、营业总收入_本年累计数",
    "所有者权益（或股东权益）合计_期末余额",
    "其他应收款_年初余额",
    "应收账款_年初余额",
    "长期应收款_年初余额",
    "存货_年初余额",
    "合同资产_年初余额",
    "合    计_本年累计已交",
    "三、经济增加值（EVA）_本年累计数",
    "全员劳动生产率（万元/人）_本期数",
    "净资产收益率（含少数股东权益）（%）_本期数",
    "资产负债率（%）_本期数",
    "带息负债平均成本率_本期累计",
    "研发经费投入强度_本期数",
    "营业收现率_本期数",
]

# 年报（dws_cgn_jq_zbval_year）已知指标标题全集
YEAR_TITLES: List[str] = [
    "资产总计_本年初余额",
    "其他应收款_期末余额",
    "资产总计_期末余额",
    "其他应收款_本年初余额",
    "所有者权益（或股东权益）合计_期末余额",
    "应收账款_期末余额",
    "预付款项_期末余额",
    "长期应收款_期末余额",
    "存货_期末余额",
    "应收账款_本年初余额",
    "预付款项_本年初余额",
    "合同资产_期末余额",
    "存货_本年初余额",
    "长期应收款_本年初余额",
    "四、利润总额（亏损总额以“-”号填列）_本年数",
    "合同资产_本年初余额",
    "一、营业总收入_本年数",
    "1.资产负债率（%）_本年数",
    "1.净资产收益率（含少数股东权益）（%）_本年数",
    "4.国有资本保值增值率（%）_本年数",
    "（六）全员劳动生产率（元/人）_本年数",
    "经济增加值-本年数",
    "9.营业收现率（%）_本年数",
    "8.技术投入比率（%）_本年数",
]


def _normalize_for_core(text: str) -> str:
    """统一标题文本，便于做跨表语义核心匹配。"""
    t = (text or "").strip()
    if not t:
        return ""
    # 去空白
    t = re.sub(r"\s+", "", t)
    # 去掉前缀序号（如：1. / 9. / 四、 / （六））
    t = re.sub(r"^[0-9]+[\.、]", "", t)
    t = re.sub(r"^[一二三四五六七八九十]+、", "", t)
    t = re.sub(r"^（[一二三四五六七八九十]+）", "", t)
    # 去掉常见尾部口径后缀（用于提取语义核心）
    t = re.sub(
        r"[_\-]?(本年累计已交|本年累计数|本期累计数|本年数|本期数|本期累计|累计数|当期数|期末余额|年初余额|本年初余额)$",
        "",
        t,
    )
    # 去掉标点，保留中文、字母、数字
    t = re.sub(r"[^\w\u4e00-\u9fff]", "", t)
    return t.lower()


def _build_core_map(titles: List[str]) -> Dict[str, List[str]]:
    core_map: Dict[str, List[str]] = {}
    for title in titles:
        core = _normalize_for_core(title)
        if not core:
            continue
        core_map.setdefault(core, []).append(title)
    return core_map


MONTH_CORE_MAP = _build_core_map(MONTH_TITLES)
YEAR_CORE_MAP = _build_core_map(YEAR_TITLES)

# 月报中存在“同 title 不同口径 code”的特殊指标。
# 默认口径：若问题未明确提“境外/海外”，使用 domestic。
SPECIAL_MONTH_INDICATORS = {
    "revenue": {
        "aliases": ["营业收入", "营业总收入", "营收"],
        "domestic_code": "GH000ZB020042G",
        "overseas_code": "JWGH000ZB020042G",
    },
    "assets_total": {
        "aliases": ["资产总计", "总资产"],
        "domestic_code": "GH000ZB010043G",
        "overseas_code": "JWGH000ZB010043G",
    },
    "profit_total": {
        "aliases": ["利润总额", "亏损企业", "亏损总额"],
        "domestic_code": "GH000ZB020074G",
        "overseas_code": "JWGH000ZB020074G",
    },
}

SPECIAL_MONTH_ALL_CODES = {
    cfg["domestic_code"]
    for cfg in SPECIAL_MONTH_INDICATORS.values()
} | {
    cfg["overseas_code"]
    for cfg in SPECIAL_MONTH_INDICATORS.values()
}


def is_overseas_query(question: str) -> bool:
    q = (question or "").lower()
    return any(k in q for k in ("境外", "海外", "国际"))


def infer_special_month_indicator_key(text: str) -> str | None:
    q = (text or "").lower()
    for key, cfg in SPECIAL_MONTH_INDICATORS.items():
        if any(alias.lower() in q for alias in cfg["aliases"]):
            return key
    return None


def get_special_month_code_by_question(question: str) -> str | None:
    key = infer_special_month_indicator_key(question)
    if not key:
        return None
    cfg = SPECIAL_MONTH_INDICATORS[key]
    return cfg["overseas_code"] if is_overseas_query(question) else cfg["domestic_code"]


def get_special_month_code(question: str, zb_title: str | None = None) -> str | None:
    """
    获取月报特殊指标 code。
    优先依据 SQL 里的 zb_title 判定指标，避免“净资产”被误判成“资产总计”。
    若无法从 zb_title 判定，再回退到 question。
    """
    key = infer_special_month_indicator_key(zb_title or "")
    if not key:
        key = infer_special_month_indicator_key(question or "")
    if not key:
        return None
    cfg = SPECIAL_MONTH_INDICATORS[key]
    return cfg["overseas_code"] if is_overseas_query(question) else cfg["domestic_code"]


# 利润总额指标名中，库内为中文弯引号「"」「"」；模型常误写为 ASCII 直引号 "，导致 WHERE 不命中。
_PROFIT_TITLE_ASCII_INNER = '以"-"号填列'
_PROFIT_TITLE_CN_INNER = f"以\u201c-\u201d号填列"


def normalize_jq_zb_title_profit_quotes(sql: str) -> str:
    """
    将 zb_title 字符串里利润总额一段的 ASCII 双引号改为库内使用的中文弯引号。
    仅处理包含 dws_cgn_jq_zbval 的 SQL，避免误伤其他语句。
    """
    if not sql or "dws_cgn_jq_zbval" not in sql.lower():
        return sql
    if _PROFIT_TITLE_ASCII_INNER not in sql:
        return sql
    return sql.replace(_PROFIT_TITLE_ASCII_INNER, _PROFIT_TITLE_CN_INNER)


def get_jq_zb_title_candidates(raw_title: str, table_type: str) -> List[str]:
    """
    根据输入标题和目标表类型返回候选标题。

    参数:
    - raw_title: SQL 中原始 zb_title 值
    - table_type: "year" 或 "month"
    """
    raw = (raw_title or "").strip()
    if not raw:
        return []
    if _PROFIT_TITLE_ASCII_INNER in raw:
        raw = raw.replace(_PROFIT_TITLE_ASCII_INNER, _PROFIT_TITLE_CN_INNER)

    titles = YEAR_TITLES if table_type == "year" else MONTH_TITLES
    core_map = YEAR_CORE_MAP if table_type == "year" else MONTH_CORE_MAP

    # 1) 精确命中（最高优先级）
    if raw in titles:
        return [raw]

    # 2) 语义核心命中（同一指标不同口径后缀/前缀序号）
    core = _normalize_for_core(raw)
    if core and core in core_map:
        return list(dict.fromkeys(core_map[core]))

    # 3) 弱匹配：核心包含关系（处理轻微文案差异）
    if core:
        fuzzy: List[str] = []
        for k, vals in core_map.items():
            if core in k or k in core:
                fuzzy.extend(vals)
        if fuzzy:
            return list(dict.fromkeys(fuzzy))

    return []
