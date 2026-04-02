#!/usr/bin/env python3
"""
基于 data_training 示例，调用大模型生成「不同问法」的合成题，用于扩充评测 / badcase。

每条合成题默认沿用种子的 description（示例 SQL）作为 Golden 标准答案，可直接喂给
``run_golden_eval.py``。

依赖：
  - 可连 PostgreSQL（与主应用相同配置）
  - OpenAI 兼容 ``/chat/completions``（默认读 ``settings.QUESTION_ENHANCE_API_URL`` 等；可用环境变量覆盖）

用法（在 backend 目录）::

    # 使用 .env 中的 QUESTION_ENHANCE_API_URL / MODEL
    python scripts/eval/synthesize_badcase_questions.py --out scripts/eval/fixtures/synthetic_badcase.json --limit-seeds 5 --per-seed 4

    # 指定种子 ID、覆盖接口
    set SQLBOT_SYNTH_API_URL=http://host:8000/v1
    set SQLBOT_SYNTH_API_KEY=sk-xxx
    python scripts/eval/synthesize_badcase_questions.py --out out.json --seed-ids 101,102 --per-seed 3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select
from sqlmodel import Session

from apps.data_training.models.data_training_model import DataTraining
from common.core.config import settings
from common.core.db import engine

SYSTEM_PROMPT = """你是智能问数场景的「评测题合成器」。你会收到一条「种子」：原始业务问题 + 对应的示例 SQL（可能较长）。
请围绕**同一业务意图与同一批表/指标**，生成若干条**不同的自然语言问法**，用于测试 NL2SQL 的鲁棒性。

硬性要求：
1. 新问题必须仍能用给定示例 SQL 回答（或语义上等价、仅差无关格式化）；不要引入新的过滤维度、新表、新指标，除非种子 SQL 已覆盖。
2. 问题用语要与种子有明显差异：可换句式、口语化、带轻微噪声、时间口径模糊表述、多一句背景等；可生成「难例」类：歧义、指代、否定、错别字（少量）。
3. 不要输出任何表名/字段名的英文技术名，除非种子问题里已出现；保持中文业务表述习惯。
4. 只输出一个 JSON 对象，不要 Markdown 代码块，不要解释文字。

输出 JSON 格式（严格遵守）：
{
  "items": [
    {
      "variant_type": "paraphrase | ambiguous | typo | negation | colloquial | edge_case",
      "question": "合成后的完整用户问题，一句",
      "intent_note": "10字以内说明本条变体在测什么"
    }
  ]
}
variant_type 含义简述：paraphrase=同义改写；ambiguous=时间/口径略模糊；typo=轻微错字；negation=含否定/排除；colloquial=口语；edge_case=边界/易错。"""


def _resolve_api_config() -> tuple[str, str, str | None, float]:
    """返回 (base_url, model, api_key, timeout)。"""
    base = (
        os.environ.get("SQLBOT_SYNTH_API_URL", "").strip().rstrip("/")
        or (getattr(settings, "QUESTION_ENHANCE_API_URL", "") or "").strip().rstrip("/")
    )
    model = os.environ.get("SQLBOT_SYNTH_MODEL", "").strip() or getattr(
        settings, "QUESTION_ENHANCE_MODEL", "gpt-4o-mini"
    )
    api_key = (os.environ.get("SQLBOT_SYNTH_API_KEY") or "").strip() or None
    timeout = float(os.environ.get("SQLBOT_SYNTH_TIMEOUT", "120"))
    return base, model, api_key, timeout


def _call_chat_completions(
    *,
    api_base: str,
    model: str,
    api_key: str | None,
    system: str,
    user: str,
    timeout: float,
    temperature: float,
) -> str:
    url = f"{api_base}/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "stream": False,
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message", {}) or {}).get("content") or ""


def _parse_json_object(text: str) -> dict[str, Any]:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```\s*$", "", t)
    return json.loads(t)


def _load_seeds(
    session: Session,
    *,
    limit: int,
    datasource: int | None,
    seed_ids: list[int] | None,
) -> list[DataTraining]:
    if seed_ids:
        stmt = select(DataTraining).where(DataTraining.id.in_(seed_ids))
        rows = list(session.execute(stmt).scalars().all())
        return sorted(rows, key=lambda r: r.id or 0)
    stmt = select(DataTraining)
    if datasource is not None:
        stmt = stmt.where(DataTraining.datasource == datasource)
    stmt = stmt.order_by(DataTraining.id.desc()).limit(limit)
    return list(session.execute(stmt).scalars().all())


def main() -> None:
    parser = argparse.ArgumentParser(description="用大模型基于 data_training 种子合成评测题")
    parser.add_argument("--out", required=True, help="输出 JSON 路径（run_golden_eval 可读）")
    parser.add_argument("--limit-seeds", type=int, default=10, help="从库中取的种子条数上限（与 --seed-ids 互斥优先 seed-ids）")
    parser.add_argument("--per-seed", type=int, default=4, help="每条种子请求模型生成的合成题数量")
    parser.add_argument("--datasource", type=int, default=None, help="仅选取指定数据源")
    parser.add_argument("--seed-ids", type=str, default="", help="逗号分隔的种子 data_training.id，指定时忽略 --limit-seeds 抽样")
    parser.add_argument("--api-url", type=str, default="", help="覆盖 OpenAI 兼容 API 根 URL（默认 env 或 QUESTION_ENHANCE_API_URL）")
    parser.add_argument("--model", type=str, default="", help="覆盖模型名")
    parser.add_argument("--temperature", type=float, default=0.6)
    parser.add_argument("--sleep", type=float, default=0.5, help="每条种子请求间隔秒数，降低限流风险")
    parser.add_argument("--dry-run", action="store_true", help="只打印首条 user prompt，不调用模型")
    parser.add_argument(
        "--include-empty-description",
        action="store_true",
        help="包含无 description（示例 SQL）的种子；默认跳过（无 Golden 无法跑 strict eval）",
    )
    args = parser.parse_args()

    skip_empty = not args.include_empty_description

    seed_ids: list[int] | None = None
    if args.seed_ids.strip():
        seed_ids = [int(x.strip()) for x in args.seed_ids.split(",") if x.strip()]

    base, model, api_key, timeout = _resolve_api_config()
    if args.api_url.strip():
        base = args.api_url.strip().rstrip("/")
    if args.model.strip():
        model = args.model.strip()

    if not base and not args.dry_run:
        print(
            "错误：未配置 API 地址。请设置环境变量 SQLBOT_SYNTH_API_URL 或在 Settings 中配置 QUESTION_ENHANCE_API_URL。",
            file=sys.stderr,
        )
        sys.exit(1)

    all_items: list[dict[str, Any]] = []
    errors: list[str] = []

    with Session(engine) as session:
        seeds = _load_seeds(session, limit=args.limit_seeds, datasource=args.datasource, seed_ids=seed_ids)

    for seed in seeds:
        sid = seed.id
        q = (seed.question or "").strip()
        desc = (seed.description or "").strip()
        ds = seed.datasource

        if not q:
            errors.append(f"seed id={sid} 无 question，已跳过")
            continue
        if skip_empty and not desc:
            errors.append(f"seed id={sid} 无 description，已跳过")
            continue

        user_block = f"""【每种子条数】请生成恰好 {args.per_seed} 条（items 数组长度 = {args.per_seed}）。

【种子问题】
{q}

【示例 SQL（标准答案，合成问题须仍能用此 SQL 或其语义等价回答）】
{desc if desc else "(种子未填示例 SQL，请仅生成合理同域问题，意图尽量贴近种子问题)"}

【表/补充】
{(seed.tables or '').strip() or "无"}
"""

        if args.dry_run:
            print("=== SYSTEM ===\n", SYSTEM_PROMPT[:500], "...\n")
            print("=== USER ===\n", user_block)
            sys.exit(0)

        try:
            raw = _call_chat_completions(
                api_base=base,
                model=model,
                api_key=api_key,
                system=SYSTEM_PROMPT,
                user=user_block,
                timeout=timeout,
                temperature=args.temperature,
            )
            obj = _parse_json_object(raw)
            synth_list = obj.get("items")
            if not isinstance(synth_list, list):
                raise ValueError("响应 JSON 缺少 items 数组")

            for i, it in enumerate(synth_list):
                if not isinstance(it, dict):
                    continue
                sq = (it.get("question") or "").strip()
                if not sq:
                    continue
                vid = f"synth_{sid}_{i}_{it.get('variant_type', 'x')}"
                all_items.append(
                    {
                        "id": vid,
                        "question": sq,
                        "datasource": ds,
                        "description": desc,
                        "reference_sql": "",
                        "source": "synthetic_badcase",
                        "seed_data_training_id": sid,
                        "variant_type": it.get("variant_type"),
                        "intent_note": it.get("intent_note"),
                    }
                )
        except Exception as e:
            errors.append(f"seed id={sid} 失败: {e!s}")

        time.sleep(max(0.0, args.sleep))

    payload = {
        "version": 1,
        "meta": {
            "generator": "synthesize_badcase_questions",
            "model": model,
            "api_base": base,
            "per_seed_requested": args.per_seed,
            "errors": errors,
        },
        "items": all_items,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(all_items)} synthetic items to {out_path}")
    if errors:
        print("Warnings/errors:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
