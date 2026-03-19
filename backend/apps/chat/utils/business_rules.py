"""动态业务规则：在最终 SQL 上根据条件应用特殊逻辑（如某省法人户数 COUNT +1）

规则在 check_save_sql 后、落库/执行前统一应用，无需改模板即可支持「黑龙江省/广东省 +1」等需求。
"""

import re
from typing import List

from common.utils.utils import _async_log_util

# 需在 SELECT 中对 COUNT(DISTINCT name) 做 +1 的省份（法人户数特殊统计规则）
# 说明：这里按你的业务口径配置；后续可迁移到配置文件/数据库规则表
PROVINCE_COUNT_PLUS_ONE = ("广东省")

# 规则列表：按顺序应用，每条为 {"condition": callable(sql)->bool, "action": callable(sql)->str}
def _condition_overseas_cqs_strip_count_plus_one(sql: str) -> bool:
    """海外/境外法人户数 SQL 不应带 COUNT(DISTINCT name)+1；若模型误写则去掉（先于省口径 +1）。"""
    sl = sql.lower()
    if "dws_cqs" not in sl and "enterprise_query_view" not in sl:
        return False
    if not any(k in sl for k in ("海外", "境外")):
        return False
    return bool(
        re.search(r"COUNT\s*\(\s*DISTINCT\s*\"?name\"?\s*\)\s*\+\s*1", sql, flags=re.IGNORECASE)
    )


def _action_strip_count_distinct_name_plus_one(sql: str) -> str:
    new_sql = re.sub(
        r"(COUNT\s*\(\s*DISTINCT\s*\"?name\"?\s*\))\s*\+\s*1",
        r"\1",
        sql,
        flags=re.IGNORECASE,
    )
    if new_sql != sql:
        _async_log_util.info("[业务规则] 已应用：海外/境外口径去除 COUNT(DISTINCT name)+1")
    return new_sql


def _condition_province_count_plus_one(sql: str) -> bool:
    """SQL 中是否包含指定省份的 name_1 条件（且为法人视图表）。"""
    if "dws_cqs_enterprise_query_view_full" not in sql and "enterprise_query_view" not in sql:
        return False
    # 海外/境外场景不再叠加省 +1
    sl = sql.lower()
    if any(k in sl for k in ("海外", "境外")):
        return False
    for province in PROVINCE_COUNT_PLUS_ONE:
        # 匹配 name_1 = '黑龙江省' 或 "name_1" = "黑龙江省" 等（带不带双引号都兼容）
        if re.search(r'("?name_1"?)\s*=\s*[\'"]' + re.escape(province) + r'[\'"]', sql, flags=re.IGNORECASE):
            return True
    return False


def _action_count_distinct_name_plus_one(sql: str) -> str:
    """将 COUNT(DISTINCT name) 改为 COUNT(DISTINCT name) + 1，若尚未加则只加一次。"""
    # 已包含 + 1 则不再替换
    if re.search(r'COUNT\s*\(\s*DISTINCT\s*"?name"?\s*\)\s*\+\s*1', sql, flags=re.IGNORECASE):
        return sql
    pattern = r'(COUNT\s*\(\s*DISTINCT\s*"?name"?\s*\))'
    new_sql = re.sub(pattern, r"\1 + 1", sql, count=1, flags=re.IGNORECASE)
    if new_sql != sql:
        _async_log_util.info("[业务规则] 已应用：法人户数 COUNT(DISTINCT name) + 1")
    return new_sql


# 注册的规则：(condition, action)，按顺序执行
_RULES: List[tuple] = [
    (_condition_overseas_cqs_strip_count_plus_one, _action_strip_count_distinct_name_plus_one),
    (_condition_province_count_plus_one, _action_count_distinct_name_plus_one),
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
