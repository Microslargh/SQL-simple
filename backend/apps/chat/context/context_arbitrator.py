"""语义仲裁者 - 多轮对话上下文冲突检测

解决「子集过滤」与「全量分布」意图冲突，例如：
- 轮1: 2025年并表法人户数 -> is_consolidated=1
- 轮2: 各产业法人户数 -> 应丢弃 is_consolidated，否则分布失真
"""

import json
import re
from typing import Any, Dict, Optional

import requests

from common.core.config import settings
from common.utils.utils import _async_log_util

ARBITRATOR_SYSTEM_PROMPT = """你是一个智能问数系统的「上下文仲裁专家」。你的任务是判断用户在多轮对话中，当前的提问是否应该继承上一轮的筛选条件。

【输入信息】
历史问题：{history_question}
历史SQL中的关键过滤条件 (JSON)：{history_filters}
当前问题：{current_question}

【判断逻辑】
请分析当前问题是否隐含了「全量统计」、「分组分布」或「维度切换」的意图：
- 分布意图识别：包含「各...」、「分...」、「所有...」、「...的分布」、「...的构成」等词汇。
- 语义互斥检测：
  - 如果用户想看「各产业的分布」，那么上一轮的「并表」、「特定状态」等子集过滤条件通常应该被丢弃，因为看分布需要全量数据。
  - 如果用户只是换了同类值（如「深圳」换「广州」），则替换旧值。
  - 时间范围（如年份）通常应保留，除非用户明确更改。

【输出要求】
请仅输出一个标准的 JSON 对象，不要包含 markdown 标记或解释文字。格式如下：
{{
    "intent_type": "distribution" | "filter_refinement" | "dimension_switch" | "new_topic",
    "slots_to_keep": ["year"],
    "slots_to_discard": ["is_consolidated"],
    "slots_to_override": {{}},
    "reasoning": "简短的一句话理由"
}}
"""


def call_arbitrator(
    history_question: str,
    history_filters: Dict[str, str],
    current_question: str,
    temperature: float = 0.1,
    timeout: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """调用 Qwen3.5-9B 语义仲裁者

    Returns:
        解析后的 JSON 决策，失败返回 None
    """
    api_url = (getattr(settings, "CONTEXT_ARBITRATOR_API_URL", "") or "").strip().rstrip("/")
    if not api_url:
        _async_log_util.debug("[仲裁者] API 未配置，跳过")
        return None

    model = getattr(settings, "CONTEXT_ARBITRATOR_MODEL", "Qwen3.5-9B")
    timeout = timeout or getattr(settings, "CONTEXT_ARBITRATOR_TIMEOUT", 5.0)

    system_content = ARBITRATOR_SYSTEM_PROMPT.format(
        history_question=history_question or "",
        history_filters=json.dumps(history_filters, ensure_ascii=False),
        current_question=current_question or "",
    )
    user_content = json.dumps(
        {
            "history_question": history_question,
            "history_filters": history_filters,
            "current_question": current_question,
        },
        ensure_ascii=False,
    )

    url = f"{api_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "stream": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return None
        content = (choices[0].get("message", {}).get("content") or "").strip()
        if not content:
            return None

        # 清理可能的 markdown 代码块
        clean = re.sub(r"```json\s*", "", content)
        clean = re.sub(r"```\s*", "", clean)
        clean = clean.strip()
        decision = json.loads(clean)
        _async_log_util.info(f"[仲裁者] 决策: intent={decision.get('intent_type')}, discard={decision.get('slots_to_discard')}")
        return decision
    except requests.exceptions.Timeout:
        _async_log_util.warning(f"[仲裁者] 调用超时 ({timeout}s)")
        return None
    except requests.exceptions.RequestException as e:
        _async_log_util.warning(f"[仲裁者] 请求失败: {e}")
        return None
    except json.JSONDecodeError as e:
        _async_log_util.warning(f"[仲裁者] JSON 解析失败: {e}")
        return None
