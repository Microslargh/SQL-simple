import json

from tqdm import tqdm
from sqlmodel import Session, text
from apps.db.engine import get_engine_conn
from apps.chat.task.llm import LLMService
from apps.chat.models.chat_model import ChatQuestion, AiModelQuestion
from apps.ai_model.model_factory import get_default_config,LLMFactory
import asyncio


def init_llm_service():
    llm_config = asyncio.run(get_default_config())
    llm = LLMFactory.create_llm(llm_config).llm
    return llm


def get_llm_result(llm, sql):
    question = AiModelQuestion()
    question.question = sql
    prompt = question.sql_straight_template_question()
    result = llm.stream(prompt)
    content_list = []
    for chuck in result:
        if chuck.response_metadata.get("finish_reason"):
            break
        content_list.append(chuck.content)
    return "".join(content_list)


def get_sql_template(old_sql_template):
    llm = init_llm_service()
    result = get_llm_result(llm, old_sql_template)
    result_dict = json.loads(result)
    print(result_dict)
    return_dict = {
        "sql_template": result_dict.get("sql-template"),
        "template_k": json.dumps(result_dict.get("sql-info")),
        "tables": result_dict.get("tables"),
    }
    return return_dict


def main():
    engine = get_engine_conn()

    with Session(engine) as session:

        count_sql = text("""
            SELECT COUNT(1)
            FROM data_training
            WHERE sql_template IS NULL
        """)
        total = session.exec(count_sql).first()
        print(f"[INFO] total records to process: {total}")

        if not total or total == 0:
            print("[INFO] no records need processing")
            return

        query_sql = text("""
            SELECT id, description
            FROM data_training
            WHERE sql_template IS NULL
        """)
        rows = session.exec(query_sql).all()

        updated = 0
        failed = 0

        for row in tqdm(rows, desc="Processing data_training"):
            record_id = row.id
            old_sql_template = row.description

            try:
                result = get_sql_template(old_sql_template)
                new_sql_template = result.get("sql_template")
                template_k = result.get("template_k")
                tables = result.get("tables")

                update_sql = text("""
                    UPDATE data_training
                    SET
                        sql_template = :sql_template,
                        template_k = :template_k,
                        tables = :tables
                    WHERE id = :id
                """)

                session.exec(
                    update_sql,
                    params={
                        "id": record_id,
                        "sql_template": new_sql_template,
                        "template_k": template_k,
                        "tables": tables,
                    },
                )

                updated += 1

            except Exception as e:  # noqa: BLE001
                failed += 1
                print(
                    f"[ERROR] process data_training id={record_id} failed: {e}"
                )

        session.commit()

    print(f"[DONE] updated={updated}, failed={failed}")


if __name__ == "__main__":
    main()