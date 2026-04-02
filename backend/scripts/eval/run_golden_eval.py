#!/usr/bin/env python3
"""
自动化 Golden 评测：登录 → 创建会话 → 流式提问 → 拉取 chat 记录 → 对比 SQL（可选）。

不依赖浏览器；需后端已启动且模型/数据源可用。

用法::

    set SQLBOT_EVAL_TOKEN=你的Bearer后token
    python scripts/eval/run_golden_eval.py --questions scripts/eval/fixtures/golden_questions.example.json

或使用账号密码（经 sqlbot_encrypt，与 Web 一致）::

    set SQLBOT_EVAL_USERNAME=admin
    set SQLBOT_EVAL_PASSWORD=secret
    python scripts/eval/run_golden_eval.py --base-url http://localhost:8000/api/v1 --questions ...

输出: 控制台摘要 + --report 指定 JSON 报告路径。

Golden 对比优先使用题集中的 **description**（与 data_training 的说明/示例 SQL 一致）；仅当 description 为空时兼容 **reference_sql**。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[1]
_EVAL_DIR = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from sql_compare import format_sql_display, sql_matches_golden


def unwrap_api_data(resp: httpx.Response) -> Any:
    try:
        body = resp.json()
    except Exception:
        return None
    if isinstance(body, dict) and body.get("code") == 0 and "data" in body:
        return body["data"]
    return body


def pick_record(records: list[dict], question: str) -> dict | None:
    q = question.strip()
    matches = [r for r in records if (r.get("question") or "").strip() == q]
    if not matches:
        return None
    return max(matches, key=lambda r: int(r.get("id") or 0))


def _format_fail_reason(
    *,
    passed: bool,
    finish: bool,
    err: Any,
    golden: str,
    sql_ok: bool | None,
) -> str:
    if passed:
        return ""
    parts: list[str] = []
    if not finish:
        parts.append("finish=false(未完成或超时，可调大轮询/检查模型与日志)")
    if err is not None and str(err).strip():
        parts.append(f"record.error={str(err)[:200]}")
    if golden and sql_ok is False:
        parts.append(
            "description(标准答案)与生成SQL不一致(评测为规范化字符串相等，语义等价也可能判失败)"
        )
    if golden and sql_ok is None:
        parts.append("已设标准答案但未对比(逻辑异常)")
    return "; ".join(parts) if parts else "原因未知(见 report 中 finish/sql_ok)"


async def obtain_token(base_url: str, username: str | None, password: str | None) -> str:
    env_tok = os.environ.get("SQLBOT_EVAL_TOKEN") or os.environ.get("EVAL_SQLBOT_TOKEN")
    if env_tok:
        tok = env_tok.strip()
        if tok.lower().startswith("bearer "):
            tok = tok[7:].strip()
        return tok
    if not username or not password:
        raise SystemExit(
            "请设置 SQLBOT_EVAL_TOKEN（推荐），或设置 SQLBOT_EVAL_USERNAME + SQLBOT_EVAL_PASSWORD"
        )
    from common.utils.crypto import sqlbot_encrypt

    u = await sqlbot_encrypt(username)
    p = await sqlbot_encrypt(password)
    url = base_url.rstrip("/") + "/login/access-token"
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            url,
            data={"username": u, "password": p},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    r.raise_for_status()
    data = unwrap_api_data(r)
    if isinstance(data, dict) and data.get("access_token"):
        return str(data["access_token"])
    if isinstance(data, dict):
        # 部分环境可能不包一层
        tok = data.get("access_token")
        if tok:
            return str(tok)
    raise SystemExit(f"登录失败: {r.text[:500]}")


async def _fetch_chat_records(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    chat_id: int,
) -> list[dict]:
    get_url = base_url.rstrip("/") + f"/chat/get/{int(chat_id)}"
    rg = await client.get(get_url, headers=headers)
    rg.raise_for_status()
    gdata = unwrap_api_data(rg)
    if not isinstance(gdata, dict):
        return []
    return (gdata.get("records") or []) or []


def _golden_for_compare(item: dict[str, Any]) -> tuple[str, str]:
    """
    返回 (golden_text, source_label)。
    优先使用 description（数据训练中一般为示例 SQL/说明，非模板占位符）；
    若为空则兼容旧题集的 reference_sql。
    """
    desc = (item.get("description") or "").strip()
    if desc:
        return desc, "description"
    legacy = (item.get("reference_sql") or "").strip()
    if legacy:
        return legacy, "reference_sql(兼容旧题集)"
    return "", ""


async def run_one(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    item: dict[str, Any],
    *,
    ignore_golden: bool = False,
    poll_seconds: float = 0.75,
    poll_max_rounds: int = 200,
) -> dict[str, Any]:
    ds = item.get("datasource")
    question = (item.get("question") or "").strip()
    golden, golden_source = _golden_for_compare(item)
    if ignore_golden:
        golden = ""
    item_id = item.get("id", "")

    if not question:
        return {"id": item_id, "passed": False, "failure_reason": "empty question"}
    if ds is None:
        return {"id": item_id, "passed": False, "failure_reason": "missing datasource"}

    start_url = base_url.rstrip("/") + "/chat/start"
    try:
        ds_id = int(ds)
    except (TypeError, ValueError):
        return {"id": item_id, "passed": False, "failure_reason": f"datasource 无效: {ds!r}"}

    r = await client.post(start_url, headers=headers, json={"datasource": ds_id})
    if r.status_code >= 400:
        return {
            "id": item_id,
            "passed": False,
            "failure_reason": f"chat/start HTTP {r.status_code}: {r.text[:400]}",
        }
    data = unwrap_api_data(r)
    chat_id = data.get("id") if isinstance(data, dict) else None
    if not chat_id:
        return {
            "id": item_id,
            "passed": False,
            "failure_reason": f"start 响应无 chat id，body 片段: {r.text[:400]}",
        }

    q_url = base_url.rstrip("/") + "/chat/question"
    payload = {"chat_id": int(chat_id), "question": question}
    # 流式：读完全部响应后再拉取记录
    async with client.stream("POST", q_url, headers=headers, json=payload, timeout=600.0) as stream:
        await stream.aread()
        if stream.status_code >= 400:
            return {
                "id": item_id,
                "passed": False,
                "chat_id": chat_id,
                "failure_reason": f"chat/question HTTP {stream.status_code}",
            }

    # 流结束后 DB 可能尚未 flush finish；轮询直到 finish 或出现 error
    rec: dict | None = None
    for _ in range(poll_max_rounds):
        records = await _fetch_chat_records(client, base_url, headers, int(chat_id))
        rec = pick_record(records, question)
        if not rec:
            await asyncio.sleep(poll_seconds)
            continue
        finish = bool(rec.get("finish"))
        err = rec.get("error")
        if finish or (err is not None and str(err).strip()):
            break
        await asyncio.sleep(poll_seconds)

    if not rec:
        records = await _fetch_chat_records(client, base_url, headers, int(chat_id))
        questions_preview = [((x.get("question") or "")[:40]) for x in records[:5]]
        return {
            "id": item_id,
            "passed": False,
            "chat_id": chat_id,
            "failure_reason": f"未找到与题面完全一致的 question 记录；当前会话 questions 预览: {questions_preview}",
        }

    finish = bool(rec.get("finish"))
    err = rec.get("error")
    sql_out = rec.get("sql")
    ok_basic = finish and not (err and str(err).strip())
    passed = ok_basic
    sql_ok: bool | None = None
    if golden:
        sql_ok = sql_matches_golden(sql_out, golden)
        passed = ok_basic and bool(sql_ok)

    fail_reason = _format_fail_reason(
        passed=passed, finish=finish, err=err, golden=golden, sql_ok=sql_ok
    )

    sql_fmt = format_sql_display(sql_out)
    golden_fmt = format_sql_display(golden) if golden else ""

    out: dict[str, Any] = {
        "id": item_id,
        "question": question,
        "passed": passed,
        "chat_id": chat_id,
        "record_id": rec.get("id"),
        "finish": finish,
        "error": err,
        "sql_ok": sql_ok,
        "golden_set": bool(golden),
        "golden_source": golden_source,
        "failure_reason": fail_reason,
        "description": (item.get("description") or "").strip(),
        "sql_generated": (str(sql_out).strip() if sql_out else ""),
        "sql_generated_formatted": sql_fmt,
        "golden_compare_text": golden,
        "golden_compare_formatted": golden_fmt,
        "reference_sql": (item.get("reference_sql") or "").strip(),
        "reference_sql_formatted": format_sql_display((item.get("reference_sql") or "").strip())
        if (item.get("reference_sql") or "").strip()
        else "",
    }
    return out


async def amain() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("SQLBOT_EVAL_BASE_URL", "http://localhost:8000/api/v1"))
    parser.add_argument("--questions", required=True, help="题集 JSON（见 fixtures 示例）")
    parser.add_argument("--report", default="", help="写入 JSON 报告路径")
    parser.add_argument("--max-items", type=int, default=0, help="仅跑前 N 条（0 表示全部）")
    parser.add_argument(
        "--ignore-golden",
        "--ignore-reference-sql",
        dest="ignore_golden",
        action="store_true",
        help="不对比标准答案（description / 兼容旧题的 reference_sql），只要求 finish 且无 error",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="每条结束后在控制台打印「问题 + 格式化后的生成 SQL / 参考 SQL」",
    )
    args = parser.parse_args()

    user = os.environ.get("SQLBOT_EVAL_USERNAME")
    pwd = os.environ.get("SQLBOT_EVAL_PASSWORD")
    token = await obtain_token(args.base_url, user, pwd)

    raw = Path(args.questions).read_text(encoding="utf-8")
    bank = json.loads(raw)
    items = bank.get("items") if isinstance(bank, dict) else bank
    if not isinstance(items, list):
        raise SystemExit("题集格式错误：需要 { \"items\": [ ... ] } 或顶层数组")
    if args.max_items and args.max_items > 0:
        items = items[: args.max_items]

    headers = {
        "X-SQLBOT-TOKEN": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept-Language": "zh-CN",
    }

    results: list[dict[str, Any]] = []
    passed_n = 0
    async with httpx.AsyncClient(timeout=600.0) as client:
        for it in items:
            row = await run_one(
                client,
                args.base_url,
                headers,
                it,
                ignore_golden=bool(args.ignore_golden),
            )
            results.append(row)
            if row.get("passed"):
                passed_n += 1
            why = row.get("failure_reason") or row.get("error") or ""
            print(f"[{'OK' if row.get('passed') else 'FAIL'}] {it.get('id', '')} {why}")
            if args.verbose:
                q = row.get("question") or it.get("question") or ""
                print("-" * 60)
                print(f"问题:\n{q}\n")
                sg = row.get("sql_generated_formatted") or row.get("sql_generated") or "(无)"
                print(f"生成 SQL（格式化）:\n{sg}\n")
                src = row.get("golden_source") or ""
                if (row.get("golden_compare_text") or "").strip():
                    gf = row.get("golden_compare_formatted") or row.get("golden_compare_text")
                    print(f"标准答案（{src or 'golden_compare_text'}，格式化）:\n{gf}\n")
                if (row.get("reference_sql") or "").strip():
                    rf = row.get("reference_sql_formatted") or row.get("reference_sql")
                    print(f"题集中 reference_sql（仅展示，不参与判分）:\n{rf}\n")
                print("-" * 60)

    total = len(results)
    failed_n = total - passed_n
    summary = {"total": total, "passed": passed_n, "failed": failed_n, "results": results}
    # 注意：passed/total 表示「通过数/本批总条数」，不是「只跑到第几条」；本批共执行 total 条（受 --max-items 限制）
    print(f"\n本批共 {total} 条：通过 {passed_n}，未通过 {failed_n}（--max-items={args.max_items or '全部'}）")
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report: {args.report}")
    return 0 if passed_n == total else 1


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
