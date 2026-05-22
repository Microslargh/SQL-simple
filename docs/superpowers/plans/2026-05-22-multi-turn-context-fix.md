# Multi-Turn Context Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two bugs: (1) time context not inherited for comparison/long questions in multi-turn, (2) context window explosion from unbounded chart message accumulation.

**Architecture:** Rule-based time injection when question lacks time keywords + truncation/limits on accumulated context + token guard before LLM calls. No new LLM calls added.

**Tech Stack:** Python, FastAPI, LangChain, existing context management modules

---

### Task 1: Add human-readable time display to extractors

**Files:**
- Modify: `backend/apps/chat/context/extractors.py` — `EntityReferenceExtractor.extract_time_range()`

- [ ] **Step 1: Add `display` field to time_range return values**

Read `extract_time_range()` at `extractors.py:76-154`. Each return path returns a dict like `{"start": "202512", "end": "202512", "format": "YYYYMM"}`. Add a `display` key to every return dict with a human-readable form.

Open `backend/apps/chat/context/extractors.py` and modify `extract_time_range()`. After each dict construction, add a `display` field.

The conversion logic: if `format` is `YYYYMM`, convert `202512` → `2025年12月`; if `YYYYMMDD`, convert `20251201` → `2025年12月01日`; if single time value, same conversion.

Add a helper method `_format_time_display` to `EntityReferenceExtractor`:

```python
@staticmethod
def _format_time_display(time_str: str) -> str:
    """Convert raw time string to human-readable format.
    202512 -> 2025年12月, 20251201 -> 2025年12月01日
    """
    if not time_str or len(time_str) < 6:
        return str(time_str)
    s = str(time_str)
    if len(s) == 6:
        y, m = s[:4], s[4:6]
        return f"{y}年{m}月"
    elif len(s) == 8:
        y, m, d = s[:4], s[4:6], s[6:8]
        return f"{y}年{m}月{d}日"
    return s
```

Then update every return path in `extract_time_range()` to include `display`. For range results:
```python
return {"start": start, "end": end, "format": "YYYYMM",
        "display": f"{EntityReferenceExtractor._format_time_display(start)} 至 {EntityReferenceExtractor._format_time_display(end)}"}
```

For single time results:
```python
return {"time": time_value, "format": "YYYYMM",
        "display": EntityReferenceExtractor._format_time_display(time_value)}
```

- [ ] **Step 2: Verify the change**

Run the existing tests to ensure no regressions:
```bash
cd backend && python -m pytest tests/ -x -q --timeout=60 2>&1 | head -50
```

- [ ] **Step 3: Commit**

```bash
git add backend/apps/chat/context/extractors.py
git commit -m "feat(extractors): add human-readable display field to time_range extraction

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: Human-readable time format in context prompt + explicit time instruction

**Files:**
- Modify: `backend/apps/chat/context/prompt_builder.py` — `ContextPromptBuilder.build()`

- [ ] **Step 1: Update time display and add time inheritance instruction**

Open `backend/apps/chat/context/prompt_builder.py`. In `build()`, around lines 62-71, use the `display` field when available, falling back to raw format. Also, add a new optional parameter `current_question` to determine whether to inject the time instruction.

Change the time_range section from:
```python
if context.time_range:
    time_format = context.time_range.get('format', 'YYYYMM')
    if 'start' in context.time_range and 'end' in context.time_range:
        start = context.time_range['start']
        end = context.time_range['end']
        time_str = f"{start} 至 {end}"
        parts.append(f'<time-range start="{start}" end="{end}" format="{time_format}">{time_str}</time-range>')
    elif 'time' in context.time_range:
        time_value = context.time_range['time']
        parts.append(f'<time-range time="{time_value}" format="{time_format}">{time_value}</time-range>')
```

To:
```python
if context.time_range:
    time_format = context.time_range.get('format', 'YYYYMM')
    if 'start' in context.time_range and 'end' in context.time_range:
        start = context.time_range['start']
        end = context.time_range['end']
        # Use human-readable display if available
        display = context.time_range.get('display', f"{start} 至 {end}")
        time_str = display
        parts.append(f'<time-range start="{start}" end="{end}" format="{time_format}">{time_str}</time-range>')
    elif 'time' in context.time_range:
        time_value = context.time_range['time']
        display = context.time_range.get('display', str(time_value))
        time_str = display
        parts.append(f'<time-range time="{time_value}" format="{time_format}">{time_str}</time-range>')
```

Then, after all parts are assembled but before the final return, add the time inheritance instruction. Update the method signature to accept `current_question`:

```python
def build(self, context: StructuredContext, current_question: Optional[str] = None) -> Optional[str]:
```

After the `if not parts: return None` check, add:

```python
# When time_range exists and current question lacks time info, inject explicit instruction
if context.time_range and current_question:
    time_keywords = ['年', '月', '日', '时间', '日期', '期', '本月', '本年', '今年', '去年', '前年', '明年']
    has_time = any(kw in current_question for kw in time_keywords)
    if not has_time:
        display = context.time_range.get('display', '')
        if display:
            parts.append(f'<time-hint>注意：历史查询的时间范围为 {display}，当前问题未指定时间，请默认使用该时间范围。</time-hint>')
```

- [ ] **Step 2: Update callers of build() to pass current_question**

In `context_manager.py`, `build_context_prompt()` already has `current_question` parameter available (line 365). Update the call to `self.prompt_builder.build()` at line 405 to pass it:

```python
return self.prompt_builder.build(context, current_question)
```

- [ ] **Step 3: Verify**

```bash
cd backend && python -m pytest tests/ -x -q --timeout=60 2>&1 | head -50
```

- [ ] **Step 4: Commit**

```bash
git add backend/apps/chat/context/prompt_builder.py backend/apps/chat/context/context_manager.py
git commit -m "feat(context): human-readable time display and explicit time inheritance instruction

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: Rule-based time injection for questions lacking time keywords

**Files:**
- Modify: `backend/apps/chat/context/context_manager.py` — `enhance_question_with_history()`

- [ ] **Step 1: Add time keyword detection and injection logic**

Open `backend/apps/chat/context/context_manager.py`. In `enhance_question_with_history()`, after the existing early-return blocks and before the LLM/rule enhancement section (around line 94-97), add a new path.

After the existing "已含明确实体" return (line 95-97), add:

```python
# 当前问句不含时间信息时，尝试从历史 SQL 提取时间并注入
time_keywords = ['年', '月', '日', '时间', '日期', '期', '本月', '本年', '今年', '去年', '前年', '明年']
has_time_in_current = any(kw in q for kw in time_keywords)
if not has_time_in_current:
    latest_log = history_logs[-1]
    if latest_log and getattr(latest_log, "pid", None):
        record = self.session.get(ChatRecord, latest_log.pid)
        if record and getattr(record, "sql", None) and record.sql:
            try:
                time_range = self.entity_extractor.extract_time_range(record.sql)
                if time_range and time_range.get("display"):
                    time_display = time_range["display"]
                    # Inject time at the beginning of the question
                    enhanced = f"{time_display}，{current_question}"
                    _async_log_util.info(
                        f"[问题增强-时间注入] 当前问句无时间信息，从历史注入: "
                        f"原始: {q[:60]}, 增强后: {enhanced[:80]}"
                    )
                    return enhanced
            except Exception as e:
                _async_log_util.debug(f"[问题增强-时间注入] 从历史SQL提取时间失败: {e}")
```

This goes between the existing block at line 97 and the "仅 X月份" block at line 99.

- [ ] **Step 2: Verify**

```bash
cd backend && python -c "
from apps.chat.context.context_manager import ContextStateManager
from apps.chat.context.question_enhancer import is_any_follow_up

# Verify the time keyword detection works
time_keywords = ['年', '月', '日', '时间', '日期', '期', '本月', '本年', '今年', '去年', '前年', '明年']

# Should have time
assert any(kw in '2025年12月深圳纳税' for kw in time_keywords) == True
# Should NOT have time  
assert any(kw in '广东省比江西省的对比情况' for kw in time_keywords) == False
# Edge: '期' in '对比情况' would match — let's check
print('期' in '广东省比江西省的对比情况')  # False — good, '对比' != '期'
"
```

- [ ] **Step 3: Commit**

```bash
git add backend/apps/chat/context/context_manager.py
git commit -m "feat(context): rule-based time injection when question lacks time keywords

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: Remove historical chart message accumulation

**Files:**
- Modify: `backend/apps/chat/task/llm.py` — `init_messages()` (lines 1141-1167)

- [ ] **Step 1: Replace chart history accumulation with current-turn-only**

Open `backend/apps/chat/task/llm.py`. In `init_messages()`, replace lines 1141-1167:

From:
```python
        # 收集所有历史图表消息（从所有历史日志中）
        all_chart_messages: List[dict[str, Any]] = []
        if len(self.generate_chart_logs) > 0:
            for log in self.generate_chart_logs:
                if log.messages:
                    # 从每条日志的messages中提取human和ai消息（跳过system消息）
                    for msg in log.messages:
                        if msg.get('type') in ['human', 'ai']:
                            all_chart_messages.append(msg)

        self.chart_message = []
        # add sys prompt
        self.chart_message.append(SystemMessage(content=self.chat_question.chart_sys_question()))

        if all_chart_messages and len(all_chart_messages) > 0:
            # 图表消息通常不需要限制数量，因为每次对话通常只有一个图表
            _async_log_util.info(f"[多轮对话] 加载历史图表消息: 总共 {len(all_chart_messages)} 条")
            for chart_message in all_chart_messages:
                _msg: BaseMessage
                if chart_message.get('type') == 'human':
                    _msg = HumanMessage(content=chart_message.get('content'))
                    self.chart_message.append(_msg)
                elif chart_message.get('type') == 'ai':
                    _msg = AIMessage(content=chart_message.get('content'))
                    self.chart_message.append(_msg)
        else:
            _async_log_util.info(f"[多轮对话] 无历史图表消息")
```

To:
```python
        # 每个问题的图表是独立的，不累加历史图表消息，避免上下文爆炸
        self.chart_message = []
        # add sys prompt
        self.chart_message.append(SystemMessage(content=self.chat_question.chart_sys_question()))
        _async_log_util.info(f"[多轮对话] 图表消息仅保留当前轮 (不累加历史)")
```

- [ ] **Step 2: Verify no other code depends on chart message history accumulation**

Search for any code that might rely on historical chart messages being present:
```bash
grep -n "chart_message" backend/apps/chat/task/llm.py
```

Expected hit locations: line 117 (field declaration), line 1151-1153 (init_messages assignment), line 2978 (generate_chart append), line 3015 (generate_chart append). No other consumers should exist.

The `generate_chart()` method at line 2976 appends `HumanMessage` (line 2978) and `AIMessage` (line 3015) to `self.chart_message` — this is the current-turn accumulation and remains correct.

- [ ] **Step 3: Remove or comment out unused `generate_chart_logs` context usage**

Verify that `generate_chart_logs` is only used in `init_messages()` for the now-removed accumulation. Check other usages:

```bash
grep -n "generate_chart_logs" backend/apps/chat/task/llm.py
```

Expected: line 126 (field declaration), line 190 (assignment in __init__). The `generate_chart_logs` field is still needed for logging purposes in `generate_chart()` at lines 2988-3026 (start_log/end_log). No other downstream consumers. Keep the field and its initialization — they're used for logging, not context.

- [ ] **Step 4: Commit**

```bash
git add backend/apps/chat/task/llm.py
git commit -m "fix(llm): remove unbounded historical chart message accumulation

Each chart is independent per-turn. Accumulating all historical chart messages
caused context window explosion after 5+ turns of conversation.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: Enforce 7-turn conversation window limit

**Files:**
- Modify: `backend/common/core/config.py` — `QUESTION_ENHANCE_MAX_TURNS` default
- Modify: `backend/apps/chat/context/context_manager.py` — `enhance_question_with_history()` fallback
- Modify: `backend/apps/chat/task/llm.py` — slice `generate_sql_logs` in `__init__`

- [ ] **Step 1: Update config.py default from 5 to 7**

At `backend/common/core/config.py` line 143:
```python
QUESTION_ENHANCE_MAX_TURNS: int = Field(default=5, description="参与 LLM 补全的历史对话轮数（3-5 轮）")
```

Change to:
```python
QUESTION_ENHANCE_MAX_TURNS: int = Field(default=7, description="多轮对话历史窗口轮数（默认 7 轮）")
```

- [ ] **Step 2: Update getattr fallback in context_manager.py**

In `context_manager.py`, `enhance_question_with_history()`, line 81:
```python
max_turns = getattr(settings, "QUESTION_ENHANCE_MAX_TURNS", 5)
```

Change to:
```python
max_turns = getattr(settings, "QUESTION_ENHANCE_MAX_TURNS", 7)
```

- [ ] **Step 3: Slice generate_sql_logs in llm.py __init__**

`list_generate_sql_logs` (at `backend/apps/chat/curd/chat.py:315`) returns ALL logs without limit. Add slicing after the fetch at `llm.py` line 189:

```python
self.generate_sql_logs = list_generate_sql_logs(session=self.session, chart_id=chat_id, current_user=current_user)
# Enforce max conversation window to prevent context overflow
max_turns = getattr(settings, "QUESTION_ENHANCE_MAX_TURNS", 7)
if len(self.generate_sql_logs) > max_turns:
    self.generate_sql_logs = self.generate_sql_logs[-max_turns:]
```

- [ ] **Step 4: Verify**

```bash
cd backend && python -c "
from common.core.config import settings
max_turns = getattr(settings, 'QUESTION_ENHANCE_MAX_TURNS', 7)
print(f'Max turns: {max_turns}')
assert max_turns == 7, f'Expected 7, got {max_turns}'
print('OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add backend/common/core/config.py backend/apps/chat/context/context_manager.py backend/apps/chat/task/llm.py
git commit -m "feat(context): enforce 7-turn conversation window limit

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: Truncate history SQL in context to 500 chars

**Files:**
- Modify: `backend/apps/chat/context/context_manager.py` — `extract_context()`

- [ ] **Step 1: Improve SQL truncation to preserve SELECT...FROM clause**

In `context_manager.py`, `extract_context()`, lines 298-315 already have SQL truncation logic but it uses a fallback to raw 500-char truncation. The existing logic:

```python
if len(history_sql) > 500:
    import re
    select_match = re.search(r'(SELECT.*?FROM.*?)(?:WHERE|GROUP|ORDER|LIMIT|$)', history_sql, re.IGNORECASE | re.DOTALL)
    if select_match:
        history_sql = select_match.group(1) + "..."
    else:
        history_sql = history_sql[:500] + "..."
```

The `import re` inside a method body is unnecessary (re is already imported at module level in this file). Clean this up and also truncate the WHERE clause content if present:

```python
if len(history_sql) > 500:
    select_match = re.search(r'(SELECT.*?FROM.*?)(?:WHERE|GROUP|ORDER|LIMIT|$)', history_sql, re.IGNORECASE | re.DOTALL)
    if select_match:
        history_sql = select_match.group(1) + " WHERE ..."
    else:
        history_sql = history_sql[:500] + "..."
```

Also ensure the `import re` at the top of the file is present (it already is at line 3).

- [ ] **Step 2: Add SQL truncation in prompt_builder for history-sql**

In `prompt_builder.py`, `build()`, the `history_sql` block at lines 74-75:

```python
if context.history_sql:
    parts.append(f'<history-sql>{context.history_sql}</history-sql>')
```

Add an additional safeguard — if the history_sql somehow exceeds 600 chars (shouldn't after extract_context truncation, but belt-and-suspenders):

```python
if context.history_sql:
    sql_text = context.history_sql
    if len(sql_text) > 600:
        sql_text = sql_text[:600] + "..."
    parts.append(f'<history-sql>{sql_text}</history-sql>')
```

- [ ] **Step 3: Commit**

```bash
git add backend/apps/chat/context/context_manager.py backend/apps/chat/context/prompt_builder.py
git commit -m "fix(context): truncate history SQL to 500 chars, preserve SELECT..FROM

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7: Add token guard before LLM API calls

**Files:**
- Modify: `backend/apps/chat/task/llm.py` — `generate_chart()` and SQL generation paths
- Modify: `backend/common/core/config.py` — add `MODEL_MAX_TOKENS`

- [ ] **Step 1: Add token estimation utility**

At the top of `llm.py`, near the existing `base_message_count_limit = 6` (line 73), add:

```python
# Token estimation: Chinese ~1.5 chars/token, English ~4 chars/token
# Conservative estimate for mixed content using 2 chars/token
def _estimate_tokens(messages: list) -> int:
    """Estimate total tokens for a list of LangChain messages.
    Conservative estimate: 2 chars per token for mixed Chinese/English content.
    """
    total_chars = 0
    for msg in messages:
        content = getattr(msg, 'content', '') or ''
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            # Handle multimodal content lists
            total_chars += sum(len(part.get('text', '')) if isinstance(part, dict) else 0 for part in content)
    return total_chars // 2  # conservative: 2 chars per token
```

- [ ] **Step 2: Add guard in generate_chart()**

In `generate_chart()` at line 2976, before the API call, add a token check. Insert after line 2978 (`self.chart_message.append(HumanMessage(...))`):

```python
    # Token guard: prevent 400 error from context overflow
    model_max_tokens = getattr(settings, "MODEL_MAX_TOKENS", 65536)
    estimated = _estimate_tokens(self.chart_message)
    safety_limit = int(model_max_tokens * 0.8)  # 80% threshold
    if estimated > safety_limit:
        err_msg = f"上下文长度超出限制：当前输入约 {estimated} tokens（模型上限 {model_max_tokens}，安全阈值 {safety_limit}）。建议：开启新对话或缩短问题。"
        _async_log_util.warning(f"[Token守卫-图表] {err_msg}")
        raise SingleMessageError(err_msg)
```

- [ ] **Step 3: Add guard in SQL generation paths**

The SQL generation uses `self.sql_message` which flows through `generate_straight_sql_info()` (line 3888) and `generate_sql()` (line 3950+). Add a guard right before the `generate_straight_sql_info` call at line 3888:

```python
            # Token guard for SQL generation
            model_max_tokens = getattr(settings, "MODEL_MAX_TOKENS", 65536)
            estimated_sql_tokens = _estimate_tokens(self.straight_messages)
            safety_limit = int(model_max_tokens * 0.8)
            if estimated_sql_tokens > safety_limit:
                _async_log_util.warning(
                    f"[Token守卫-SQL] straight_messages token估计: {estimated_sql_tokens} > 安全阈值 {safety_limit}"
                )
                # Don't block SQL generation yet — chart is the main culprit.
                # But log aggressively so we can monitor.
                _async_log_util.warning(
                    f"[Token守卫-SQL] 警告：SQL上下文较大 ({estimated_sql_tokens} tokens)，"
                    f"建议减少历史轮次或简化表结构"
                )
```

Actually, for SQL generation the main issue is the accumulated chart messages (now fixed in Task 4) plus the schema size. The SQL messages have natural limits from the 7-turn window. So just log a warning for SQL path — don't block. The chart path is the critical one to block.

- [ ] **Step 4: Add MODEL_MAX_TOKENS to config**

Add the setting to `backend/common/core/config.py` near the other model-related settings (after line 143 where `QUESTION_ENHANCE_MAX_TURNS` is defined):

```python
MODEL_MAX_TOKENS: int = Field(default=65536, description="模型最大上下文窗口 token 数，用于守卫避免 400 错误")
```

- [ ] **Step 5: Commit**

```bash
git add backend/apps/chat/task/llm.py backend/common/core/config.py
git commit -m "feat(llm): add token estimation guard before chart generation API calls

Prevents 400 BadRequestError from context overflow by estimating tokens
and blocking calls that exceed 80% of model max context window.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 8: Integration test — simulate multi-turn scenario

**Files:**
- Create: `backend/tests/test_multi_turn_context.py`

- [ ] **Step 1: Write integration test for time inheritance**

```python
"""Integration tests for multi-turn conversation context fixes."""
import pytest
from apps.chat.context.extractors import EntityReferenceExtractor
from apps.chat.context.question_enhancer import is_any_follow_up


class TestTimeRangeDisplay:
    """Task 1: Human-readable time display."""

    def test_format_ym_range(self):
        result = EntityReferenceExtractor._format_time_display("202512")
        assert result == "2025年12月"

    def test_format_ymd_single(self):
        result = EntityReferenceExtractor._format_time_display("20251201")
        assert result == "2025年12月01日"

    def test_format_short_string(self):
        result = EntityReferenceExtractor._format_time_display("2025")
        assert result == "2025"


class TestTimeKeywordDetection:
    """Task 3: Time keyword detection for injection."""

    TIME_KEYWORDS = ['年', '月', '日', '时间', '日期', '期', '本月', '本年', '今年', '去年', '前年', '明年']

    def test_has_time_keyword_explicit(self):
        q = "2025年12月深圳纳税情况"
        assert any(kw in q for kw in self.TIME_KEYWORDS)

    def test_no_time_keyword_comparison(self):
        q = "广东省比江西省的对比情况"
        has_time = any(kw in q for kw in self.TIME_KEYWORDS)
        # '期' should not match because '对比情况' does not contain standalone '期'
        # But let's verify: '期' is in '对比情况'... actually yes, '况' != '期'
        assert not has_time, f"Unexpected time keyword match in: {q}"

    def test_has_time_keyword_month_only(self):
        q = "7月的呢"
        assert any(kw in q for kw in self.TIME_KEYWORDS)

    def test_no_time_entity_question(self):
        q = "深圳市的纳税情况"
        has_time = any(kw in q for kw in self.TIME_KEYWORDS)
        assert not has_time


class TestFollowUpDetection:
    """Verify follow-up detection not broken by changes."""

    def test_short_region_follow_up(self):
        assert is_any_follow_up("山西省的呢")

    def test_short_metric_follow_up(self):
        assert is_any_follow_up("利润呢")

    def test_long_comparison_not_follow_up(self):
        # This is correct: comparison questions are structurally different from follow-ups
        assert not is_any_follow_up("广东省的比江西省的对比情况")

    def test_explicit_time_question_not_follow_up(self):
        assert not is_any_follow_up("2025年12月深圳市的纳税情况")
```

- [ ] **Step 2: Run the integration tests**

```bash
cd backend && python -m pytest tests/test_multi_turn_context.py -v
```

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_multi_turn_context.py
git commit -m "test: add integration tests for multi-turn context fixes

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 9: Final verification and regression check

**Files:**
- No file changes, verification only.

- [ ] **Step 1: Run full test suite**

```bash
cd backend && python -m pytest tests/ -x -q --timeout=120 2>&1 | tail -30
```

Expected: All existing tests still pass.

- [ ] **Step 2: Run linting**

```bash
cd backend && bash scripts/lint.sh 2>&1 | tail -20
```

Expected: No new lint errors introduced.

- [ ] **Step 3: Verify all changes are committed**

```bash
git status
git log --oneline -10
```

Expected: Clean working tree, 9+ new commits on this branch.

- [ ] **Step 4: Final commit if needed**

Only if linting required fixes:
```bash
git add -A
git commit -m "chore: fix linting issues from multi-turn context changes

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Summary of Changes

| Task | File | Change |
|------|------|--------|
| 1 | `extractors.py` | Add `_format_time_display()` + `display` field to time_range |
| 2 | `prompt_builder.py` | Use display field + inject time hint instruction |
| 2 | `context_manager.py` | Pass `current_question` to builder |
| 3 | `context_manager.py` | Rule-based time injection for questions without time keywords |
| 4 | `llm.py` | Remove historical chart message accumulation |
| 5 | `context_manager.py` | Default max turns 5→7 |
| 5 | `llm.py` | Slice generate_sql_logs to max 7 turns |
| 6 | `context_manager.py` | Fix SQL truncation (remove inline import, preserve FROM clause) |
| 6 | `prompt_builder.py` | Belt-and-suspenders SQL length check |
| 7 | `llm.py` | Add `_estimate_tokens()` + guard in `generate_chart()` |
| 7 | `config.py` | Add `MODEL_MAX_TOKENS` setting |
| 8 | `tests/test_multi_turn_context.py` | Integration tests |
| 9 | — | Full test suite + lint verification |
