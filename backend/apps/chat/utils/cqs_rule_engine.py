"""
CQS（产权）规则引擎（P1）。

目标：
1) 对“集团法人户数”问题注入强约束提示；
2) 在 SQL 落库前做安全的后处理（基线条件补齐、+1 口径修正）；
3) 明细 +1（UNION ALL）先通过提示约束与检测告警，避免盲目改写列结构。
"""

from __future__ import annotations

import re

from common.utils.utils import _async_log_util

GROUP_BASELINE_PREDICATES = (
    ("gjczqyname", "gjczqyname = '中国广核集团有限公司'"),
    ("register_status", "register_status = '0'"),
    ("ygjczqygx", "ygjczqygx IN ('1','2','3')"),
    ("partner_czrpx", "partner_czrpx = '内1'"),
    ("is_exit_press_reduce", "is_exit_press_reduce = '是'"),
)

GROUP_SHARE_ALIASES = ("集团/股份", "股份产业", "股份板块", "集团股份", "集团股份产业")

# 法人户数去重计数：name / c.name / c."name"（模型常见带表别名，旧正则会漏匹配导致 +1 无法去除）
_COUNT_DISTINCT_NAME = (
    r"COUNT\s*\(\s*DISTINCT\s+(?:(?:[a-zA-Z_]\w*)\.)?(?:\"name\"|'name'|`name`|name)\s*\)"
)


def _is_cqs_sql(sql: str) -> bool:
    sl = (sql or "").lower()
    return "dws_cqs" in sl or "enterprise_query_view" in sl


def _is_group_legal_count_intent(question: str) -> bool:
    q = (question or "")
    return ("集团" in q) and ("法人户数" in q or ("户数" in q and "法人" in q))


def _is_detail_intent(question: str) -> bool:
    q = (question or "")
    return "明细" in q or "列表" in q


def _is_overseas_intent(question: str) -> bool:
    q = (question or "")
    return ("境外" in q) or ("海外" in q)


def _has_non_guangdong_province(question: str) -> bool:
    """是否明确问到广东省以外的省级范围（用于 +1 与地域口径）。"""
    prov = _extract_question_province(question)
    if prov is None:
        return False
    return prov != "广东"


def _extract_question_province(question: str) -> str | None:
    q = (question or "")
    # 先匹配已知省名，避免「集团广东省」被 `XX省` 正则收成「集团广东」
    for p in (
        "黑龙江",
        "内蒙古",
        "北京", "天津", "上海", "重庆",
        "河北", "山西", "辽宁", "吉林",
        "江苏", "浙江", "安徽", "福建", "江西", "山东",
        "河南", "湖北", "湖南", "广东", "海南",
        "四川", "贵州", "云南", "陕西", "甘肃", "青海",
        "台湾",
    ):
        if p in q:
            return p
    m = re.search(r"([\u4e00-\u9fff]{2,6})省", q)
    if m:
        return m.group(1)
    return None


def _mentions_industry(question: str) -> bool:
    q = (question or "")
    return ("产业" in q) or ("板块" in q)


def _is_industry_distribution_intent(question: str) -> bool:
    q = (question or "")
    return ("各产业" in q) or ("各板块" in q) or (("按产业" in q or "按板块" in q) and ("户数" in q))


def _is_group_share_industry(question: str) -> bool:
    q = (question or "")
    if "集团/股份" in q:
        return True
    return any(a in q for a in GROUP_SHARE_ALIASES)


def should_apply_group_plus_one(question: str) -> bool:
    """
    +1 规则口径：
    - 仅集团法人户数；
    - 境外口径不 +1；
    - 指定非广东省不 +1；
    - 指定产业且非集团/股份不 +1；
    - 其余（含基础问法、并表、境内、广东、集团/股份）+1。
    """
    if not _is_group_legal_count_intent(question):
        return False
    if _is_overseas_intent(question):
        return False
    if _has_non_guangdong_province(question):
        return False
    if _is_industry_distribution_intent(question):
        return False
    if _mentions_industry(question) and not _is_group_share_industry(question):
        return False
    return True


def build_cqs_group_rule_prompt(question: str, schema: str) -> str:
    if "dws_cqs" not in (schema or "").lower() and "enterprise_query_view" not in (schema or "").lower():
        return ""
    if not _is_group_legal_count_intent(question):
        return ""

    plus_one = should_apply_group_plus_one(question)
    plus_one_text = (
        "本问命中“集团公司需额外+1”口径：聚合 SQL 应在 COUNT(DISTINCT name) 基础上 +1。"
        if plus_one
        else "本问不命中“集团公司额外+1”口径：不得额外 +1。"
    )
    detail_hint = "若用户问明细且命中 +1 口径，需在明细结果上 UNION ALL 手动补一行“中国广核集团有限公司”记录。"
    geo_hint = (
        "地区字段语义：name_1=省份/境外国家，name_2=市级，name_3=区级；"
        "若筛选“XX省”，必须使用 name_1。"
    )
    dist_hint = (
        "若问“各产业法人户数/各板块法人户数”，应按产业分组统计，且仅“集团/股份”这一组额外 +1。"
    )
    return (
        "<rule>"
        "**产权-集团法人户数基线口径**：查询 dws_cqs_enterprise_query_view_full 的集团法人户数时，"
        "必须包含以下过滤条件："
        "gjczqyname='中国广核集团有限公司'、register_status='0'、"
        "ygjczqygx IN ('1','2','3')、partner_czrpx='内1'、is_exit_press_reduce='是'。"
        f"{plus_one_text}"
        f"{detail_hint}"
        f"{geo_hint}"
        f"{dist_hint}"
        "</rule>"
    )


def _append_missing_baseline_predicates(sql: str) -> str:
    if not _is_cqs_sql(sql):
        return sql
    sl = sql.lower()
    missing = [expr for key, expr in GROUP_BASELINE_PREDICATES if key.lower() not in sl]
    if not missing:
        return sql
    m = re.search(r"\bwhere\b", sql, flags=re.IGNORECASE)
    if not m:
        return sql
    inject = " AND " + " AND ".join(missing) + " "
    replaced = re.sub(
        r"(\bwhere\b[\s\S]*?)(\bgroup\s+by\b|\border\s+by\b|\bunion\s+all\b|$)",
        lambda mm: mm.group(1).rstrip() + inject + mm.group(2),
        sql,
        count=1,
        flags=re.IGNORECASE,
    )
    if replaced != sql:
        _async_log_util.info("[cqs_rule] 已补齐集团法人户数基线过滤条件")
    return replaced


def _rewrite_count_plus_one(sql: str, question: str) -> str:
    if not _is_cqs_sql(sql):
        return sql
    # 允许“追问省略主体”场景：若 SQL 已明确集团口径，也进入 +1 修正规则
    is_group_sql = (
        bool(re.search(r'(?:["\']?gjczqyname["\']?)\s*=\s*[\'"]中国广核集团有限公司[\'"]', sql, flags=re.IGNORECASE))
        and bool(re.search(_COUNT_DISTINCT_NAME, sql, flags=re.IGNORECASE))
    )
    if not (_is_group_legal_count_intent(question) or is_group_sql):
        return sql

    should_plus = should_apply_group_plus_one(question) if _is_group_legal_count_intent(question) else True
    # 当前问句一旦明确到“非广东省”，无条件不 +1（优先级最高，防上下文污染）
    q_prov = _extract_question_province(question)
    if q_prov and q_prov != "广东":
        should_plus = False
    # 二次判定：以 SQL 实际条件收敛，防止同会话语义污染
    prov_m = re.search(r'(?:["\']?name_1["\']?)\s*=\s*[\'"]([^\'"]+)[\'"]', sql, flags=re.IGNORECASE)
    if prov_m:
        prov = prov_m.group(1)
        if prov and prov not in ("广东", "广东省"):
            should_plus = False
    plate_m = re.search(r'(?:["\']?(?:plate|bk)["\']?)\s*=\s*[\'"]([^\'"]+)[\'"]', sql, flags=re.IGNORECASE)
    if plate_m:
        plate = plate_m.group(1)
        if plate not in GROUP_SHARE_ALIASES and plate != "集团/股份":
            should_plus = False
    has_plus = bool(re.search(rf"{_COUNT_DISTINCT_NAME}\s*\+\s*1", sql, flags=re.IGNORECASE))
    if should_plus and not has_plus:
        new_sql = re.sub(
            rf"({_COUNT_DISTINCT_NAME})",
            r"\1 + 1",
            sql,
            count=1,
            flags=re.IGNORECASE,
        )
        if new_sql != sql:
            _async_log_util.info("[cqs_rule] 已应用集团法人户数 +1 口径")
        return new_sql
    if (not should_plus) and has_plus:
        new_sql = re.sub(
            rf"({_COUNT_DISTINCT_NAME})\s*\+\s*1",
            r"\1",
            sql,
            flags=re.IGNORECASE,
        )
        if new_sql != sql:
            _async_log_util.info("[cqs_rule] 已去除不应出现的 +1 口径")
        return new_sql
    return sql


def _normalize_group_legal_count_uses_name_not_zzjgdm(sql: str, question: str) -> str:
    """
    集团法人户数业务口径以主体名称去重计数；模型常误写 COUNT(zzjgdm)。
    在后续 +1 / 基线规则生效前，统一改为 COUNT(DISTINCT name)。
    """
    if not _is_cqs_sql(sql):
        return sql
    if not re.search(r"zzjgdm", sql, flags=re.IGNORECASE):
        return sql
    if not re.search(
        r"COUNT\s*\(\s*(?:DISTINCT\s+)?[`\"']?zzjgdm[`\"']?\s*\)",
        sql,
        flags=re.IGNORECASE,
    ):
        return sql
    group_intent = _is_group_legal_count_intent(question)
    group_sql = bool(
        re.search(
            r'gjczqyname\s*=\s*[\'"]中国广核集团有限公司[\'"]',
            sql,
            flags=re.IGNORECASE,
        )
    )
    if not (group_intent or group_sql):
        return sql
    new_sql = re.sub(
        r"COUNT\s*\(\s*(?:DISTINCT\s+)?[`\"']?zzjgdm[`\"']?\s*\)",
        "COUNT(DISTINCT name)",
        sql,
        flags=re.IGNORECASE,
    )
    if new_sql != sql:
        _async_log_util.info("[cqs_rule] 集团法人户数口径：已将 COUNT(zzjgdm) 规范为 COUNT(DISTINCT name)")
    return new_sql


def _rewrite_province_level_filter_field(sql: str) -> str:
    """
    产权地域字段语义：
    - name_1: 省份/境外国家
    - name_2: 市级
    - name_3: 区级
    若 SQL 把“XX省”写在 name_2/name_3 上，自动改为 name_1。
    """
    if not _is_cqs_sql(sql):
        return sql

    def repl(m: re.Match) -> str:
        col = m.group("col")
        q = m.group("q")
        val = m.group("val")
        if not val.endswith("省"):
            return m.group(0)
        if col.lower() in ("name_2", "name_3"):
            return f'name_1 = {q}{val}{q}'
        return m.group(0)

    new_sql = re.sub(
        r'(?P<col>name_[123])\s*=\s*(?P<q>[\'"])(?P<val>[^\'"]+)(?P=q)',
        repl,
        sql,
        flags=re.IGNORECASE,
    )
    if new_sql != sql:
        _async_log_util.info("[cqs_rule] 已将省份筛选字段纠偏为 name_1")
    return new_sql


def _rewrite_industry_distribution_plus_one(sql: str, question: str) -> str:
    """
    问“各产业/各板块法人户数”时，仅集团/股份分组应额外 +1。
    将 COUNT(DISTINCT name) 改写为：
      COUNT(DISTINCT name) + if(plate='集团/股份' OR bk='集团/股份', 1, 0)
    """
    if not _is_cqs_sql(sql):
        return sql
    if not _is_industry_distribution_intent(question):
        return sql
    if "group by" not in sql.lower():
        return sql
    if re.search(rf"{_COUNT_DISTINCT_NAME}\s*\+\s*if\(", sql, flags=re.IGNORECASE):
        return sql

    new_sql = re.sub(
        _COUNT_DISTINCT_NAME,
        "COUNT(DISTINCT name) + if(plate = '集团/股份' OR bk = '集团/股份', 1, 0)",
        sql,
        count=1,
        flags=re.IGNORECASE,
    )
    if new_sql != sql:
        _async_log_util.info("[cqs_rule] 已应用各产业分组场景的集团/股份行级 +1 口径")
    return new_sql


def _extract_explicit_yyyymm_from_question(question: str) -> int | None:
    q = question or ""
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", q)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    if 2000 <= y <= 2099 and 1 <= mo <= 12:
        return y * 100 + mo
    return None


def _append_default_version_code_if_missing(sql: str, question: str, latest_yyyymm: int | None) -> str:
    """
    产权口径：问题未明确提时间时，默认以最新 version_code 批次查询。
    """
    if not _is_cqs_sql(sql) or latest_yyyymm is None:
        return sql
    if _extract_explicit_yyyymm_from_question(question) is not None:
        return sql
    sl = sql.lower()
    if "version_code" in sl or "create_date" in sl or "sys_datatime" in sl:
        return sql
    if "where" not in sl:
        return sql
    version_expr = f"version_code = '{latest_yyyymm:06d}'"
    new_sql = re.sub(
        r"(\bwhere\b[\s\S]*?)(\bgroup\s+by\b|\border\s+by\b|\bunion\s+all\b|$)",
        lambda mm: mm.group(1).rstrip() + f" AND {version_expr} " + mm.group(2),
        sql,
        count=1,
        flags=re.IGNORECASE,
    )
    if new_sql != sql:
        _async_log_util.info(f"[cqs_rule] 未提时间，已补默认最新 version_code={latest_yyyymm:06d}")
    return new_sql


def _hard_remove_plus_one_for_non_guangdong(sql: str) -> str:
    """
    最终硬守门：若 SQL 明确筛选到非广东省，必须去掉 COUNT(DISTINCT name)+1。
    """
    if not _is_cqs_sql(sql):
        return sql
    prov_m = re.search(r'(?:["\']?name_1["\']?)\s*=\s*[\'"]([^\'"]+)[\'"]', sql, flags=re.IGNORECASE)
    if not prov_m:
        return sql
    prov = prov_m.group(1)
    if prov in ("广东", "广东省"):
        return sql
    new_sql = re.sub(
        rf"({_COUNT_DISTINCT_NAME})\s*\+\s*1",
        r"\1",
        sql,
        flags=re.IGNORECASE,
    )
    if new_sql != sql:
        _async_log_util.info(f"[cqs_rule] 非广东省({prov})场景，已硬性去除 +1")
    return new_sql


def _hard_remove_plus_one_when_question_non_guangdong_province(sql: str, question: str) -> str:
    """
    短追问等场景下问句可能不含「集团」「法人户数」，但能从问句识别省份；
    此时 SQL 可能仍带上一轮 +1 且尚未写出 name_1，仅靠 SQL 无法去 +1。
    若当前问句明确指向非广东省，则无条件去掉 COUNT(DISTINCT name)+1。
    """
    if not _is_cqs_sql(sql):
        return sql
    prov = _extract_question_province(question or "")
    if not prov or prov == "广东":
        return sql
    new_sql = re.sub(
        rf"({_COUNT_DISTINCT_NAME})\s*\+\s*1",
        r"\1",
        sql,
        flags=re.IGNORECASE,
    )
    if new_sql != sql:
        _async_log_util.info(f"[cqs_rule] 问句省份为{prov}（非广东），已硬性去除 +1（防短追问/无 name_1 条件时污染）")
    return new_sql


def apply_cqs_sql_rules(sql: str, question: str, latest_yyyymm: int | None = None) -> str:
    if not sql or not _is_cqs_sql(sql):
        return sql
    s = _normalize_group_legal_count_uses_name_not_zzjgdm(sql, question)
    s = _rewrite_province_level_filter_field(s)
    s = _append_default_version_code_if_missing(s, question, latest_yyyymm)
    s = _rewrite_industry_distribution_plus_one(s, question)
    s = _hard_remove_plus_one_for_non_guangdong(s)
    s = _hard_remove_plus_one_when_question_non_guangdong_province(s, question)
    if not _is_group_legal_count_intent(question):
        return s
    s = _append_missing_baseline_predicates(s)
    s = _rewrite_count_plus_one(s, question)
    if _is_detail_intent(question) and should_apply_group_plus_one(question) and "union all" not in s.lower():
        _async_log_util.warning("[cqs_rule] 命中明细 +1 口径但 SQL 未包含 UNION ALL 补集团公司明细")
    return s

