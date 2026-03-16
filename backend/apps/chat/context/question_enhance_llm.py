"""问题增强 LLM - 多轮追问语义补全（规则未命中时 API 接入，与仲裁者同方式）

将 history_context（最近 3-5 轮对话）和 current_question 输入给 LLM，
输出补全后的独立、完整、无指代的查询语句。与规则增强形成双路召回。
"""

import re
from typing import Any, List, Optional

import requests

from common.core.config import settings
from common.utils.utils import _async_log_util

QUESTION_ENHANCE_SYSTEM_PROMPT = """你是一个智能问数系统的「追问补全专家」。你的任务是根据历史对话，把用户当前的简短追问补全成一句独立、完整、无指代的查询语句。

【要求】
1. 补全后的句子必须能单独被理解，不依赖上下文（时间、地区、指标、主体等从历史中合理继承）。
2. 支持：时间追问（如「7月的呢」→ 补上年份）、地区/指标/主体追问、明细/详情追问（如「请给出明细」→ 补上「广东省法人户数明细」）、复合维度变更、口语化与相对时间（「去年」→ 具体年份）。
3. 只输出补全后的那一句话，不要解释、不要前缀、不要引号、不要「补全结果：」等任何多余文字。
4. 若当前问句已经完整、无需补全，则原样输出当前问句。
5. **严禁追加用户未提及的过滤条件**：仅做指代补全或时间/地区/指标替换，不得添加「存续」「压减」「全资」「控股」「实际控制」等用户未明确说出的限定词，避免语义篡改（例如用户只问「福建省法人户数明细」时，不得变成「福建省存续且与某集团存在控制关系的法人户数明细」）。
6. **保留「集团」不展开**：若用户问句中已使用「集团」（如「集团二月法人户数」「集团并表法人户数」），补全时只补时间、月份等指代，**不得**将「集团」替换为具体公司全称（如「中国广核集团有限公司」）；输出中仍保留「集团」一词。"""


def _format_history_turns(turns: List[dict]) -> str:
    """将历史轮次格式化为 prompt 文本。"""
    lines = []
    for i, t in enumerate(turns, 1):
        user = (t.get("user") or "").strip()
        if not user:
            continue
        assistant = (t.get("assistant") or "").strip()
        if assistant:
            # 可截断过长回复，仅作上下文
            brief = assistant[:200] + "..." if len(assistant) > 200 else assistant
            lines.append(f"第{i}轮 用户：{user}\n第{i}轮 助手：{brief}")
        else:
            lines.append(f"第{i}轮 用户：{user}")
    return "\n".join(lines) if lines else "（无）"


def rewrite_question_with_llm(
    history_turns: List[dict],
    current_question: str,
    temperature: float = 0.1,
    timeout: Optional[float] = None,
    reference_time_str: Optional[str] = None,
) -> Optional[str]:
    """调用问题增强 LLM，将当前追问补全为独立完整句。

    Args:
        history_turns: 最近 N 轮对话，每项为 {"user": "用户问题", "assistant": "助手回复或 None"}
        current_question: 当前用户追问（可能很短，如「请给出明细」「7月的呢」）
        temperature: 采样温度
        timeout: 超时秒数
        reference_time_str: 上一轮查询时间说明（如「2026年2月」），供模型补全「X月份」时使用正确年份

    Returns:
        补全后的单句字符串，失败或未配置时返回 None
    """
    api_url = (getattr(settings, "QUESTION_ENHANCE_API_URL", "") or "").strip().rstrip("/")
    if not api_url:
        _async_log_util.debug("[问题增强-LLM] API 未配置，跳过")
        return None

    model = getattr(settings, "QUESTION_ENHANCE_MODEL", "Qwen3.5-9B")
    timeout = timeout or getattr(settings, "QUESTION_ENHANCE_TIMEOUT", 8.0)

    history_text = _format_history_turns(history_turns)
    ref_block = ""
    if reference_time_str:
        ref_block = f"\n【参考】上一轮查询时间：{reference_time_str}。若当前追问仅提「X月份」未提年份，请用此时间中的年份补全，勿使用其他年份（如 2024）。\n"
    user_content = f"""【历史对话】
{history_text}
{ref_block}
【当前追问】
{current_question or ''}

请根据历史对话，将当前的简短追问补全为一个独立、完整、无指代的查询语句。不要回答，只输出补全后的句子。"""

    url = f"{api_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": QUESTION_ENHANCE_SYSTEM_PROMPT},
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

        # 去掉常见前缀与首尾引号，只保留一句
        content = re.sub(r"^(补全结果|补全后|结果|输出)[：:]\s*", "", content)
        content = re.sub(r"^[""'「」]\s*", "", content)
        content = re.sub(r"\s*[""'「」]$", "", content)
        content = content.strip()
        # 若模型返回多行，取第一行作为单句
        if "\n" in content:
            content = content.split("\n")[0].strip()
        if not content:
            return None
        _async_log_util.info(f"[问题增强-LLM] 原始: {current_question[:60]}, 补全: {content[:80]}")
        return content
    except requests.exceptions.Timeout:
        _async_log_util.warning(f"[问题增强-LLM] 调用超时 ({timeout}s)")
        return None
    except requests.exceptions.RequestException as e:
        _async_log_util.warning(f"[问题增强-LLM] 请求失败: {e}")
        return None
    except Exception as e:
        _async_log_util.warning(f"[问题增强-LLM] 异常: {e}")
        return None
