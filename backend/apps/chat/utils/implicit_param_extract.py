"""隐式参数提取 - 模板中硬编码实体与用户问题实体对齐

当模板 SQL 中写死「广东省」而用户问「四川省」时，通过 LLM 识别并对齐，
输出替换列表，在生成最终 SQL 前做一次隐式替换，无需改模板库即可复用。
"""

import json
import re
from typing import Any, List, Optional

import requests

from common.core.config import settings
from common.utils.utils import _async_log_util

IMPLICIT_EXTRACT_SYSTEM = """你是 SQL 模板的「隐式参数提取」专家。
输入：模板 SQL（可能含硬编码的省/市、时间、公司名等）、模板原始问句、当前用户问句。
任务：识别模板 SQL 中与「模板问句」对应的硬编码实体值，并根据「用户问句」推断应对齐成的值，输出替换对。
要求：
1. 只输出替换对，不要解释。old 必须是模板 SQL 中出现的**原样字符串**（含引号时带引号，如 '广东省' 或 \"广东省\"）；new 为用户意图对应的值，格式与 old 一致（如 '四川省'）。
2. 仅处理同类实体替换：省/市/地区、时间（YYYYMM 等）、公司/主体名。不要改写 SQL 结构或关键字。
3. 若某处已是 [[PROVINCE]] 等占位符，无需输出；只对**硬编码**的实体做替换对。
4. 输出为 JSON 数组，格式：[{"old": "原样字符串", "new": "替换后字符串"}]。若无需替换则输出 []。"""


def extract_implicit_replacements(
    template_sql: str,
    template_question: str,
    user_question: str,
    timeout: Optional[float] = None,
) -> List[dict]:
    """调用 LLM 提取隐式参数替换对。

    Returns:
        [{"old": "exact_str_in_sql", "new": "replacement"}, ...]，失败或未配置时返回 []
    """
    api_url = (getattr(settings, "IMPLICIT_PARAM_EXTRACT_API_URL", "") or "").strip().rstrip("/")
    if not api_url:
        return []

    timeout = timeout or getattr(settings, "IMPLICIT_PARAM_EXTRACT_TIMEOUT", 8.0)
    model = getattr(settings, "IMPLICIT_PARAM_EXTRACT_MODEL", "Qwen3.5-9B")

    user_content = f"""【模板 SQL】
{template_sql[:4000]}

【模板问句】
{template_question}

【用户问句】
{user_question}

请输出 JSON 数组，每项为 {{"old": "模板SQL中的原样字符串", "new": "根据用户问句应对齐成的值"}}。无需替换则输出 []。"""

    url = f"{api_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": IMPLICIT_EXTRACT_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "stream": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return []
        content = (choices[0].get("message", {}).get("content") or "").strip()
        if not content:
            return []

        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        content = content.strip()
        out = json.loads(content)
        if not isinstance(out, list):
            return []
        replacements = [x for x in out if isinstance(x, dict) and "old" in x and "new" in x]
        if replacements:
            _async_log_util.info(
                f"[隐式参数提取] 模板问: {template_question[:40]}, 用户问: {user_question[:40]}, 替换数: {len(replacements)}"
            )
        return replacements
    except requests.exceptions.Timeout:
        _async_log_util.warning(f"[隐式参数提取] 调用超时 ({timeout}s)")
        return []
    except (requests.exceptions.RequestException, json.JSONDecodeError, Exception) as e:
        _async_log_util.warning(f"[隐式参数提取] 失败: {e}")
        return []


def apply_implicit_replacements(sql: str, replacements: List[dict]) -> str:
    """对 SQL 按顺序应用隐式替换，每项只替换第一次出现。"""
    if not replacements:
        return sql
    for item in replacements:
        old_val = item.get("old")
        new_val = item.get("new")
        if old_val is None or new_val is None or old_val == new_val:
            continue
        if old_val not in sql:
            continue
        sql = sql.replace(old_val, new_val, 1)
    return sql
