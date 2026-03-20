"""问题增强 LLM - 多轮追问语义补全（规则未命中时 API 接入，与仲裁者同方式）

将 history_context（最近 3-5 轮对话）和 current_question 输入给 LLM，
输出补全后的独立、完整、无指代的查询语句。与规则增强形成双路召回。
"""

import re
from typing import Any, List, Optional

import requests

from common.core.config import settings
from common.utils.utils import _async_log_util

# 产业名称映射表
INDUSTRY_NAMES = {
    '新能源', '资本控股', '能源国际', '核技术', '核电', '集团/股份', 
    '环保/节能', '核燃料', '核服', '英国核电', '数字化产业', '司库'
}

# 产业名称关键词映射
INDUSTRY_KEYWORDS = {
    '集团股份': '集团/股份',
    '环保': '环保/节能'
}

QUESTION_ENHANCE_SYSTEM_PROMPT = """你是一个智能问数系统的「追问补全专家」。你的任务是：仅对用户当前问题进行指代补全，使其成为一句可独立理解的完整查询语句，不得改变原始查询意图。

【核心原则（最高优先级）】
1️⃣ 指标锁定（强约束）

当前问句中的核心指标词必须原样保留，不得替换、扩展或改写

核心指标包括但不限于：

担保情况 / 法人户数 / 营收 / 利润 / 资产 等

❗禁止行为：

担保情况 → 法人户数（❌）

法人户数 → 企业数量（❌）

👉 规则：指标词必须100%出现在输出中，且语义完全一致

2️⃣ 零语义漂移（强约束）

补全前后语义必须完全一致

若补全导致语义变化，则放弃补全，直接返回原句

3️⃣ 禁止新增条件（强约束）

不得添加任何用户未明确提及的限定词，包括但不限于：

存续 / 注销 / 并表 / 压减 / 全资 / 控股 / 实际控制

4️⃣ 最小改写原则

仅补全“缺失信息”，不得优化或改写表达

不做同义替换，不做表达升级

【补全行为边界】
✅ 允许的操作：

补时间（如：7月 → 2024年7月）

补地区（如：广东 → 广东省）

补主体（如：补上“集团”上下文）

明确指代（如：明细 → XX指标明细）

❌ 严禁的操作：

替换指标（最重要）

新增业务条件

引入新概念（历史中未出现）

改写业务含义

【集团处理规则】

若用户使用“集团”，必须原样保留

严禁替换为具体公司名称

【信息继承规则】

优先继承最近一轮相关上下文

若当前问句已包含完整信息 → 禁止继承

若继承可能引入歧义 → 放弃继承

【失败兜底机制（非常关键）】

当出现以下任一情况时：

无法确定补全内容

补全可能改变指标或语义

存在多个可能解释

👉 直接输出原问句，不做任何补全

【输出要求】

只输出最终查询语句

不要解释，不要前缀，不要引号

【自检机制（必须执行）】

在输出前，必须检查：

是否保留了原始指标词（如“担保情况”）？

是否新增了任何用户未提及的词？

是否改变了查询意图？

👉 任一不满足 → 输出原句"""


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
        
        # 验证并纠正产业名称
        corrected_content = validate_and_correct_industry(content)
        if corrected_content != content:
            _async_log_util.info(f"[问题增强-LLM] 产业名称纠正: {content[:80]} → {corrected_content[:80]}")
            content = corrected_content
        
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


def validate_and_correct_industry(content: str) -> str:
    """验证并纠正产业名称，确保使用正确的产业名称"""
    # 首先检查是否包含正确的产业名称
    for industry in INDUSTRY_NAMES:
        if industry in content:
            return content
    
    # 检查是否包含产业关键词，替换为正确的产业名称
    for keyword, correct_industry in INDUSTRY_KEYWORDS.items():
        if keyword in content:
            # 替换关键词为正确的产业名称
            content = content.replace(keyword, correct_industry)
            return content
    
    # 检查是否包含错误的产业名称组合，如"集团数字化产业"
    if "集团数字化产业" in content:
        content = content.replace("集团数字化产业", "集团/股份")
        return content
    
    return content
