"""SQL 生成阶段的审核类规则提示：按用户问题（及可选 schema）命中后注入 custom_prompt。

新增规则：在 AUDIT_RULES 末尾追加一条，实现 match(question, schema_lower) -> bool 与 prompt 文案即可。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List

from common.utils.utils import _async_log_util

MatchFn = Callable[[str, str], bool]
# question: 当前用户问题（已含多轮增强后的表述）
# schema_lower: db_schema 小写串，可用来做表级命中


@dataclass(frozen=True)
class SqlGenerationAuditRule:
    """单条审核规则：命中则把 prompt 拼进 SQL 生成的 system 侧 custom_prompt。"""

    rule_id: str
    match: MatchFn
    """返回 True 表示本问适用该条审核说明。"""
    prompt: str
    """注入模型的一段 <rule> 或自然语言约束。"""


def _match_overseas_group_legal_entity(q: str, _schema: str) -> bool:
    """集团海外/境外法人户数等：COUNT 不得 +1。"""
    if not q:
        return False
    overseas = ("海外" in q) or ("境外" in q)
    if not overseas:
        return False
    if "法人户数" in q:
        return True
    if "户数" in q and ("集团" in q or "法人" in q):
        return True
    return False


# ---------------------------------------------------------------------------
# 在此列表末尾追加新规则即可扩展（按顺序评估，同一 rule_id 只注入一次）
# ---------------------------------------------------------------------------
AUDIT_RULES: List[SqlGenerationAuditRule] = [
    SqlGenerationAuditRule(
        rule_id="cqs_overseas_count_no_plus_one",
        match=_match_overseas_group_legal_entity,
        prompt=(
            "<rule>**审核-海外/境外法人户数 COUNT**：当前问题涉及集团海外或境外法人户数统计时，"
            "SELECT 中户数字段必须使用 **COUNT(DISTINCT name)**（或与术语一致的 DISTINCT 主体字段），"
            "**严禁**写成 COUNT(DISTINCT name) + 1 或任何额外 +1。"
            "境内个别省份在省口径下另有 +1 统计规则，**不适用于海外/境外**。</rule>"
        ),
    ),
    # 示例：后续可加第二条
    # SqlGenerationAuditRule(
    #     rule_id="xxx",
    #     match=lambda q, s: "某关键词" in q,
    #     prompt="<rule>...</rule>",
    # ),
]


def collect_sql_generation_audit_prompt(question: str, schema: str) -> str:
    """汇总本问命中的所有审核规则文案；无命中返回空串。"""
    q = (question or "").strip()
    schema_l = (schema or "").lower()
    seen: set[str] = set()
    parts: List[str] = []
    for rule in AUDIT_RULES:
        if rule.rule_id in seen:
            continue
        try:
            if not rule.match(q, schema_l):
                continue
        except Exception as e:
            _async_log_util.debug(f"[SQL审核规则] {rule.rule_id} match 异常: {e}")
            continue
        parts.append(rule.prompt)
        seen.add(rule.rule_id)
    if not parts:
        return ""
    header = (
        "\n\n### SQL生成审核校验（本问已命中以下规则，生成 SQL 时必须遵守）\n"
    )
    body = "\n".join(parts)
    _async_log_util.info(f"[SQL审核规则] 已注入 {len(parts)} 条: {sorted(seen)}")
    return header + body
