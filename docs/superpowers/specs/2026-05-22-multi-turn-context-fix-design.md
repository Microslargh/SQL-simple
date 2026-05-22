# Multi-Turn Context Fix — Design Spec

**Date:** 2026-05-22
**Branch:** 145-reg

## Problem Summary

Two bugs in multi-turn conversation:

1. **Time context lost on comparison/long questions**: Short follow-ups (e.g., "山西省的呢") correctly inherit time from history, but longer comparison questions (e.g., "广东省的比江西省的对比情况") are treated as new topics. Time range is only present in `<context>` XML as raw `202512` format, which the LLM often misses.

2. **Context window explosion**: Historical chart messages accumulate unboundedly — each contains full SQL + chart JSON. After 5+ turns, total input tokens exceed the model's 65536 limit, causing a 400 BadRequestError at chart generation.

## Design Decisions

### Fix 1: Time Inheritance for Non-Follow-Up Questions

**Strategy**: Rule-based time injection when question lacks time keywords and history has time range.

**Changes**:

1. `context_manager.py` — `enhance_question_with_history()`:
   - After the existing early-return for short follow-ups (line 95-97), add a new path: if question has no time keywords AND history SQL has extractable time range, prepend time to the question.
   - Cap `history_turns` to 7 (driven by `QUESTION_ENHANCE_MAX_TURNS`, default 7).

2. `prompt_builder.py` — `build()`:
   - Convert time_range display from raw `202512` to natural language `2025年12月`.
   - When time_range exists and current question lacks time keywords, append an explicit instruction: "注意：历史查询的时间范围为 2025年12月，当前问题未指定时间，请默认使用该时间范围。"

3. `extractors.py` — `EntityReferenceExtractor.extract_time_range()`:
   - Return a new `display` field with human-readable time format (e.g., `"2025年12月"`).

### Fix 2: Context Window Management

**Strategy**: Stop accumulating historical chart messages; limit SQL conversation window to 7 turns; add token guard.

**Changes**:

1. `llm.py` — `init_messages()`:
   - Remove the block (lines 1141-1167) that appends ALL historical chart messages. Chart messages now only contain: system prompt + current turn's user question.
   - SQL history logs already limited by `QUESTION_ENHANCE_MAX_TURNS` (default 7).

2. `llm.py` — `_resolve_db_schema_for_sql()` / `extract_context()`:
   - Truncate `history_sql` to 500 chars, preserving SELECT...FROM clause.

3. `llm.py` — `generate_chart()` and SQL generation calls:
   - Add a token guard: estimate input tokens before API call. If exceeds 80% of model max (default 65536 * 0.8 = 52428), raise a clear user-facing error before hitting the API.

4. `context_manager.py` — `enhance_question_with_history()`:
   - Enforce `QUESTION_ENHANCE_MAX_TURNS` (default 7) on history_turns slice.

## Affected Files

| File | Change |
|------|--------|
| `backend/apps/chat/context/context_manager.py` | Time injection rule path + 7-turn window |
| `backend/apps/chat/context/prompt_builder.py` | Human-readable time + explicit time instruction |
| `backend/apps/chat/context/extractors.py` | Add `display` field to time_range output |
| `backend/apps/chat/task/llm.py` | Remove chart history accumulation; add token guard; SQL truncation |
| `backend/apps/chat/context/question_enhancer.py` | May need minor helpers for time keyword detection |

## Non-Goals

- Not changing `is_any_follow_up` core logic (short follow-up patterns remain intact)
- Not adding new LLM calls (no latency increase)
- Not modifying `template.yaml` prompts
