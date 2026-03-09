"""上下文清洗器 - 根据语义仲裁者决策执行槽位清洗"""

from typing import Any, Dict, List, Optional

from apps.chat.context.context_arbitrator import call_arbitrator
from apps.chat.context.extractors import FilterSlotExtractor
from common.utils.utils import _async_log_util

# 时间相关槽位，降级时仅保留这些
SAFE_SLOTS = ("year", "month", "start_date", "end_date", "create_date")


def clean_context_slots(
    current_question: str,
    history_question: str,
    history_slots: Dict[str, str],
) -> Dict[str, str]:
    """根据仲裁者决策清洗历史槽位

    Args:
        current_question: 用户当前问题
        history_question: 上一轮问题
        history_slots: 上一轮提取的过滤条件

    Returns:
        清洗后的 slots dict
    """
    if not history_slots:
        return {}

    decision = call_arbitrator(
        history_question=history_question,
        history_filters=history_slots,
        current_question=current_question,
    )

    if not decision:
        # 降级：只保留时间相关槽位
        fallback = {k: v for k, v in history_slots.items() if k in SAFE_SLOTS}
        _async_log_util.info(f"[上下文清洗] 仲裁者不可用，降级保留时间槽位: {fallback}")
        return fallback

    # 执行清洗：先复制历史，再删除 discard，再应用 override
    final_slots = dict(history_slots)

    for key in decision.get("slots_to_discard", []):
        if key in final_slots:
            del final_slots[key]
            _async_log_util.info(f"[上下文清洗] 丢弃槽位: {key}")

    for key, value in decision.get("slots_to_override", {}).items():
        final_slots[key] = value
        _async_log_util.info(f"[上下文清洗] 覆盖槽位: {key}={value}")

    # 若 intent 为 distribution 且未显式 discard，可额外丢弃常见子集过滤字段
    intent = decision.get("intent_type", "")
    if intent == "distribution":
        for sub_key in ("is_consolidated", "register_status"):
            if sub_key in final_slots and sub_key not in decision.get("slots_to_keep", []):
                del final_slots[sub_key]
                _async_log_util.info(f"[上下文清洗] 分布意图，额外丢弃: {sub_key}")

    return final_slots


def get_slots_to_discard_hint(
    current_question: str,
    history_question: str,
    history_sql: str,
) -> List[str]:
    """获取应丢弃的槽位列表，用于注入 prompt 提示

    Returns:
        应丢弃的槽位 key 列表，如 ["is_consolidated"]
    """
    if not history_sql or not history_sql.strip():
        return []

    slots = FilterSlotExtractor.extract_slots(history_sql)
    if not slots:
        return []

    cleaned = clean_context_slots(
        current_question=current_question,
        history_question=history_question or "",
        history_slots=slots,
    )

    # 找出被丢弃的槽位
    discarded = [k for k in slots if k not in cleaned]
    return discarded
