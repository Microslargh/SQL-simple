"""动态业务规则：在最终 SQL 上按条件做少量修正。

注意：集团法人户数「是否 +1」已由 apps.chat.utils.cqs_rule_engine.apply_cqs_sql_rules
在落库前统一处理。此处不再重复做省份 +1，避免与 cqs 规则冲突（如黑龙江省被误 +1）。

仅保留：海外/境外场景下去除误写的 COUNT(DISTINCT …)+1。
"""

import re
from typing import List

from apps.chat.utils.cqs_rule_engine import _COUNT_DISTINCT_NAME
from common.utils.utils import _async_log_util


# 规则列表：按顺序应用，每条为 (condition, action)
def _condition_overseas_cqs_strip_count_plus_one(sql: str) -> bool:
    """海外/境外法人户数 SQL 不应带 COUNT(DISTINCT name)+1；若模型误写则去掉。"""
    sl = sql.lower()
    if "dws_cqs" not in sl and "enterprise_query_view" not in sl:
        return False
    if not any(k in sl for k in ("海外", "境外")):
        return False
    return bool(re.search(rf"{_COUNT_DISTINCT_NAME}\s*\+\s*1", sql, flags=re.IGNORECASE))


def _action_strip_count_distinct_name_plus_one(sql: str) -> str:
    new_sql = re.sub(
        rf"({_COUNT_DISTINCT_NAME})\s*\+\s*1",
        r"\1",
        sql,
        flags=re.IGNORECASE,
    )
    if new_sql != sql:
        _async_log_util.info("[业务规则] 已应用：海外/境外口径去除 COUNT(DISTINCT name)+1")
    return new_sql


# 注册的规则：(condition, action)，按顺序执行
_RULES: List[tuple] = [
    (_condition_overseas_cqs_strip_count_plus_one, _action_strip_count_distinct_name_plus_one),
]


def apply_business_rules(sql: str) -> str:
    """对最终 SQL 按顺序应用所有业务规则，返回可能被修改后的 SQL。"""
    if not sql or not sql.strip():
        return sql
    result = sql
    for condition, action in _RULES:
        try:
            if condition(result):
                result = action(result)
        except Exception as e:
            _async_log_util.warning(f"[业务规则] 应用规则异常: {e}")
    return result
