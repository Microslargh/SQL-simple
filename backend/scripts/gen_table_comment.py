#!/usr/bin/env python3
"""
根据表名从 SQL 示例库（data_training）中检索相关问题，并调用数据分析师角色的大模型生成表注释。

用法（需在 backend 目录下执行）:
  cd backend
  python -m scripts.gen_table_comment dws_cgn_jq_zbval_month
  python -m scripts.gen_table_comment dws_cgn_jq_zbval_month --oid 1

输出格式示例:
  表名：月报表
  维度：集团维度
  周期：月度周期
  口径分为集团合并和集团单户信息，以group_type=合并/单户区分。
  相关问题：1.一利五率 2.两金数据
  示例问题：xxxx
"""
import argparse
import asyncio
import sys
from pathlib import Path

# 将 backend 根目录加入 path，便于作为脚本运行
_backend = Path(__file__).resolve().parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from sqlalchemy import select
from sqlmodel import Session
from langchain_core.messages import SystemMessage, HumanMessage

from common.core.db import engine
from apps.data_training.models.data_training_model import DataTraining
from apps.ai_model.model_factory import get_default_config, LLMFactory


SYSTEM_PROMPT = """你是一名数据分析师，擅长根据业务问题归纳数据表的业务含义与使用场景。
你会收到「涉及某张表的全部示例问题」列表。你的目标是：覆盖这些问题涉及的主题，不要遗漏主要方向。

必须严格按以下格式输出，不要添加多余标题或说明，直接输出内容即可：

表名：（填写表的中文业务名称，如：月报表、年报表；可根据问题推断）
维度：（如：集团维度、公司维度；可多维度用顿号分隔）
周期：（如：月度周期、年度周期；可根据问题推断）
口径：（描述数据口径；如存在「合并/单户」等分口径字段请写清楚）
相关问题：（必须按序号列出「主题类别」，且尽量覆盖全部示例问题。要求：
  - 若示例问题数量 <= 6：输出不少于 3 类
  - 若示例问题数量 7~15：输出不少于 5 类
  - 若示例问题数量 > 15：输出不少于 7 类
  - 每一类用 8~18 个字概括，避免过泛）
示例问题：（从给定列表中选 2 个典型问题原文作为示例）
问题清单：（原样输出你收到的全部问题编号与原文，用于人工核对；不要改写）"""


def get_questions_by_table(session: Session, table_name: str, oid: int = 1) -> list[str]:
    """从 data_training 中检索 tables 字段包含 table_name 的所有问题的去重列表。"""
    pattern = f"%{table_name}%"
    stmt = (
        select(DataTraining.question)
        .where(
            DataTraining.oid == oid,
            DataTraining.tables.isnot(None),
            DataTraining.tables.ilike(pattern),
            DataTraining.question.isnot(None),
            DataTraining.question != "",
        )
        .distinct()
    )
    rows = session.execute(stmt).scalars().all()
    return [q.strip() for q in rows if q and str(q).strip()]


def invoke_llm_for_comment(llm, table_name: str, question_list: list[str]) -> str:
    """调用大模型生成表注释，返回完整回复文本。"""
    if not question_list:
        return "（未找到该表相关示例问题，无法生成表注释）"
    questions_text = "\n".join(f"{i+1}. {q}" for i, q in enumerate(question_list))
    user_content = f"""表名（物理表）：{table_name}
示例问题数量：{len(question_list)}

以下为 SQL 示例库中涉及该表的全部问题（请严格按约定格式输出，尤其是「相关问题」需覆盖主题，「问题清单」需原样保留）：\n\n{questions_text}"""
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]
    # 非流式一次拿全量结果，便于直接打印
    response = llm.invoke(messages)
    if hasattr(response, "content"):
        return response.content
    return str(response)


def main(table_name: str, oid: int = 1) -> None:
    if not table_name or not table_name.strip():
        print("错误：请提供非空表名。", file=sys.stderr)
        sys.exit(1)
    table_name = table_name.strip()

    with Session(engine) as session:
        questions = get_questions_by_table(session, table_name, oid=oid)
        print(f"[INFO] 表名: {table_name}, 检索到 {len(questions)} 条相关问题", file=sys.stderr)
        if not questions:
            print("未找到与该表相关的示例问题，请检查表名或示例库中是否包含该表。", file=sys.stderr)
            sys.exit(2)

    llm_config = asyncio.run(get_default_config())
    llm = LLMFactory.create_llm(llm_config).llm
    comment = invoke_llm_for_comment(llm, table_name, questions)
    print(comment)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="根据表名从 SQL 示例库检索问题并生成表注释（数据分析师大模型）"
    )
    parser.add_argument(
        "table_name",
        type=str,
        help="物理表名，如 dws_cgn_jq_zbval_month",
    )
    parser.add_argument(
        "--oid",
        type=int,
        default=1,
        help="租户/组织 id，默认 1",
    )
    args = parser.parse_args()
    try:
        main(table_name=args.table_name, oid=args.oid)
    except KeyboardInterrupt:
        print("\n[INFO] 已中断", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        raise
