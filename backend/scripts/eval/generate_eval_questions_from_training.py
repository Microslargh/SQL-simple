#!/usr/bin/env python3
"""
从 data_training（SQL 示例库）导出评测题集 JSON。

用法（在 backend 目录下，已配置 .env / 数据库可连）::

    python scripts/eval/generate_eval_questions_from_training.py --out eval/fixtures/from_training.json --limit 50

可选按数据源过滤::

    python scripts/eval/generate_eval_questions_from_training.py --out out.json --datasource 1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select
from sqlmodel import Session

from apps.data_training.models.data_training_model import DataTraining
from common.core.db import engine


def main() -> None:
    parser = argparse.ArgumentParser(description="从 data_training 导出 golden 题集")
    parser.add_argument("--out", required=True, help="输出 JSON 路径")
    parser.add_argument("--limit", type=int, default=200, help="最多导出条数")
    parser.add_argument("--datasource", type=int, default=None, help="仅导出指定数据源 ID")
    args = parser.parse_args()

    stmt = select(DataTraining)
    if args.datasource is not None:
        stmt = stmt.where(DataTraining.datasource == args.datasource)
    stmt = stmt.order_by(DataTraining.id.desc()).limit(args.limit)

    with Session(engine) as session:
        rows = list(session.execute(stmt).scalars().all())

    items: list[dict] = []
    for r in rows:
        items.append(
            {
                "id": f"dt_{r.id}",
                "question": (r.question or "").strip(),
                "datasource": r.datasource,
                # 评测对比使用 description（示例/说明 SQL）；sql_template 为待填充模板，不再写入 reference_sql
                "description": (r.description or "").strip(),
                "reference_sql": "",
                "source": "data_training",
                "data_training_id": r.id,
            }
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "meta": {
            "generator": "data_training",
            "note": "golden 对比字段为 description；reference_sql 留空（旧版模板勿作标准答案）",
        },
        "items": items,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    desc_lens = [len((it.get("description") or "")) for it in items]
    mx = max(desc_lens) if desc_lens else 0
    print(f"Wrote {len(items)} items to {out_path}")
    print(f"description 长度: max={mx} 字符（本脚本不对 description 截断；若曾用旧版导出带 [:500]，请重新生成题集）")


if __name__ == "__main__":
    main()
