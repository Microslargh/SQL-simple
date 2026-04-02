"""评测用 SQL 规范化与对比（宽松：空白与大小写）。"""

from __future__ import annotations

import re

import sqlparse


def normalize_sql(sql: str | None) -> str:
    if not sql or not str(sql).strip():
        return ""
    formatted = sqlparse.format(str(sql).strip(), reindent=True, keyword_case="lower")
    # 去掉全部空白，避免 id=1 与 id = 1 被判为不一致
    no_ws = re.sub(r"\s+", "", formatted)
    return no_ws.lower()


def sql_matches_golden(generated: str | None, golden: str | None) -> bool:
    """生成 SQL 与标准答案是否在规范化后一致。"""
    return normalize_sql(generated) == normalize_sql(golden)


def format_sql_display(sql: str | None) -> str:
    """供报告/控制台展示用的格式化 SQL（与 normalize 规则独立）。"""
    if not sql or not str(sql).strip():
        return ""
    try:
        return sqlparse.format(str(sql).strip(), reindent=True, keyword_case="upper")
    except Exception:
        return str(sql).strip()
