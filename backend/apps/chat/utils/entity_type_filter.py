"""快速模板匹配 - 实体类型一致性过滤

避免「地区」意图误匹配「公司/集团」维度模板（如：用户问海南省法人户数却匹配到集团各产业法人户数）。
在检索后、送入 LLM 前按实体类型过滤候选模板，类型冲突则剔除，保证只对兼容模板做匹配与填槽。
"""

import re
from typing import List, Optional

from common.utils.utils import _async_log_util

# 实体类型：地区维度 vs 公司/产业维度（互斥）
ENTITY_REGION = "region"
ENTITY_COMPANY_INDUSTRY = "company_industry"
ENTITY_UNKNOWN = "unknown"


def infer_entity_type(question: str) -> str:
    """从问句文本推断主导实体类型（地区 / 公司·产业 / 未知）。

    用于与模板问句类型做一致性校验，避免地区问句匹配到公司维度模板。
    """
    if not question or not question.strip():
        return ENTITY_UNKNOWN
    q = question.strip()

    # 地区维度：明确出现省/市/区/县或常见地区名
    region_patterns = [
        r"[^\s]{1,6}(?:省|市|区|县)(?![a-zA-Z])",  # XX省/XX市/XX区/XX县
        r"(?:海南|广东|广西|湖南|湖北|北京|上海|深圳|广州|浙江|江苏)(?:省|市|)?",
    ]
    for pat in region_patterns:
        if re.search(pat, q):
            return ENTITY_REGION

    # 公司/产业维度：集团、各产业、主体、按产业/按公司 等
    company_industry_keywords = ["集团", "各产业", "产业", "主体", "按产业", "按公司", "子公司", "分公司"]
    if any(k in q for k in company_industry_keywords):
        return ENTITY_COMPANY_INDUSTRY

    return ENTITY_UNKNOWN


def filter_training_data_by_entity_type(
    training_data: List[dict],
    user_question: str,
) -> List[dict]:
    """按用户问句实体类型过滤候选模板，剔除类型冲突项。

    - 用户为「地区」时，剔除「公司/产业」维度模板。
    - 用户为「公司/产业」时，剔除「地区」维度模板。
    - 未知类型与任何模板兼容，不剔除。

    Returns:
        过滤后的列表（可能为空）；空则调用方应走 NL2SQL 降级。
    """
    if not training_data:
        return []
    user_type = infer_entity_type(user_question or "")

    filtered = []
    for item in training_data:
        template_question = (item.get("question") or "").strip()
        template_type = infer_entity_type(template_question)

        if user_type == ENTITY_UNKNOWN or template_type == ENTITY_UNKNOWN:
            filtered.append(item)
            continue
        if user_type == template_type:
            filtered.append(item)
            continue
        # 互斥：地区 vs 公司/产业
        _async_log_util.info(
            f"[快速模板-实体过滤] 剔除类型不匹配 - 模板ID: {item.get('id')}, 模板问: {template_question[:50]}, "
            f"用户类型: {user_type}, 模板类型: {template_type}"
        )
    if len(filtered) < len(training_data):
        _async_log_util.info(
            f"[快速模板-实体过滤] 用户类型={user_type}, 过滤前={len(training_data)}, 过滤后={len(filtered)}"
        )
    return filtered
