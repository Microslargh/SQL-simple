import json
import argparse
import signal
import sys
import time
from pathlib import Path

from tqdm import tqdm
from sqlmodel import Session, text, select
from sqlalchemy import func
from apps.db.engine import get_engine_conn
from apps.chat.task.llm import LLMService
from apps.chat.models.chat_model import ChatQuestion, AiModelQuestion
from apps.ai_model.model_factory import get_default_config, LLMFactory
import asyncio


def init_llm_service():
    llm_config = asyncio.run(get_default_config())
    llm = LLMFactory.create_llm(llm_config).llm
    return llm


def get_llm_result(llm, sql, timeout=60, max_chunks=50000):
    """
    获取 LLM 结果，带超时和重试机制
    
    Args:
        llm: LLM 实例
        sql: SQL 语句
        timeout: 超时时间（秒）
        max_chunks: 最大 chunk 数量，防止无限循环（默认 50000，基本不限制）
    
    Returns:
        LLM 返回的完整内容
    """
    question = AiModelQuestion()
    question.question = sql
    prompt = question.sql_straight_template_question()
    
    start_time = time.time()
    result = llm.stream(prompt)
    content_list = []
    chunk_count = 0
    last_chunk_time = time.time()
    no_content_count = 0  # 连续没有内容的 chunk 数量
    
    try:
        for chunk in result:
            chunk_count += 1
            current_time = time.time()
            
            # 检查超时
            if current_time - start_time > timeout:
                raise TimeoutError(f"LLM response timeout after {timeout} seconds")
            
            # 检查 chunk 数量限制（仅作为安全阀，默认值很大）
            if chunk_count > max_chunks:
                print(f"[WARN] Reached max chunks limit ({max_chunks}), but continuing...")
                # 不再抛出异常，而是继续处理，但记录警告
            
            # 检查是否完成
            finish_reason = None
            if hasattr(chunk, 'response_metadata') and chunk.response_metadata:
                finish_reason = chunk.response_metadata.get("finish_reason")
            
            # 检查 additional_kwargs 中是否有完成标志
            if not finish_reason and hasattr(chunk, 'additional_kwargs'):
                finish_reason = chunk.additional_kwargs.get("finish_reason")
            
            # 如果检测到完成标志，添加当前内容后退出
            if finish_reason:
                if hasattr(chunk, 'content') and chunk.content:
                    content_list.append(chunk.content)
                break
            
            # 添加内容
            chunk_content = None
            if hasattr(chunk, 'content') and chunk.content:
                chunk_content = chunk.content
                content_list.append(chunk_content)
                last_chunk_time = current_time
                no_content_count = 0
            else:
                no_content_count += 1
                # 如果连续 100 个 chunk 都没有内容，可能已经结束
                if no_content_count > 100:
                    print(f"[WARN] No content received for {no_content_count} chunks, assuming completion")
                    break
            
            # 检查是否长时间没有新内容（超过 10 秒），可能已经结束
            if current_time - last_chunk_time > 10 and len(content_list) > 0:
                print(f"[WARN] No new content for 10 seconds, assuming completion")
                break
        
        full_content = "".join(content_list)
        if not full_content or len(full_content.strip()) == 0:
            raise ValueError("LLM returned empty response")
        
        return full_content
    except Exception as e:
        # 如果出错，返回已收集的内容（如果有）
        partial_content = "".join(content_list)
        if partial_content and len(partial_content) > 100:
            print(f"[WARN] Partial LLM response received: {len(partial_content)} chars")
            # 如果部分内容足够长，尝试使用它
            return partial_content
        raise e


def get_sql_template(llm, old_sql_template, max_retries=3, timeout=60, max_chunks=50000):
    """
    使用已初始化的 LLM 生成 SQL 模板，带重试机制
    
    Args:
        llm: LLM 实例
        old_sql_template: 原始 SQL 模板
        max_retries: 最大重试次数
        timeout: 超时时间（秒）
        max_chunks: 最大 chunk 数量
    
    Returns:
        包含 sql_template, template_k, tables 的字典
    """
    last_error = None
    
    for attempt in range(1, max_retries + 1):
        try:
            result = get_llm_result(llm, old_sql_template, timeout=timeout, max_chunks=max_chunks)
            
            # 尝试解析 JSON
            try:
                result_dict = json.loads(result)
            except json.JSONDecodeError as e:
                # 尝试提取 JSON 部分
                import re
                json_match = re.search(r'\{.*\}', result, re.DOTALL)
                if json_match:
                    result_dict = json.loads(json_match.group())
                else:
                    raise ValueError(f"Failed to parse JSON from LLM response: {result[:200]}")
            
            # 检查是否成功
            if not result_dict.get("success", False):
                error_msg = result_dict.get('message', 'Unknown error')
                raise ValueError(f"LLM returned success=false: {error_msg}")
            
            return_dict = {
                "sql_template": result_dict.get("sql-template"),
                "template_k": json.dumps(result_dict.get("sql-info"), ensure_ascii=False),
                "tables": result_dict.get("tables"),
            }
            
            # 验证必要字段
            if not return_dict["sql_template"]:
                raise ValueError("sql-template is empty in LLM response")
            if not return_dict["template_k"] or return_dict["template_k"] == "null":
                raise ValueError("sql-info is empty in LLM response")
            if not return_dict["tables"]:
                raise ValueError("tables is empty in LLM response")
            
            return return_dict
            
        except (TimeoutError, ValueError, json.JSONDecodeError) as e:
            last_error = e
            if attempt < max_retries:
                wait_time = attempt * 2  # 递增等待时间
                print(f"[WARN] Attempt {attempt} failed: {e}. Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print(f"[ERROR] All {max_retries} attempts failed. Last error: {e}")
                raise last_error
        except Exception as e:
            # 其他异常直接抛出，不重试
            raise e
    
    raise last_error


# 全局变量用于保存进度
progress_file = Path("generate_template_progress.json")
interrupted = False


def save_progress(processed_ids, failed_ids):
    """保存处理进度"""
    progress = {
        "processed_ids": processed_ids,
        "failed_ids": failed_ids,
        "timestamp": time.time()
    }
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)


def load_progress():
    """加载处理进度"""
    if progress_file.exists():
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def signal_handler(sig, frame):
    """处理中断信号"""
    global interrupted
    interrupted = True
    print("\n[INFO] Interrupt signal received. Saving progress and exiting gracefully...")


def main(force_regenerate=False, skip_processed=False, timeout=60, max_chunks=50000, target_ids=None):
    """
    主函数：生成或重新生成 SQL 模板
    
    Args:
        force_regenerate: 如果为 True，重新生成所有模板（包括已有模板）
                         如果为 False，只生成 sql_template IS NULL 或空字符串的记录
        skip_processed: 如果为 True，跳过已处理的记录（基于进度文件）
        timeout: LLM 响应超时时间（秒）
        max_chunks: 最大 chunk 数量限制（默认 50000，基本不限制）
        target_ids: 如果提供，只处理指定的 ID 列表（覆盖其他条件）
    """
    global interrupted
    
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    engine = get_engine_conn()
    
    # 加载进度
    processed_ids = set()
    if skip_processed:
        progress = load_progress()
        if progress:
            processed_ids = set(progress.get("processed_ids", []))
            print(f"[INFO] Loaded progress: {len(processed_ids)} records already processed")

    with Session(engine) as session:
        # 根据参数决定查询条件
        if target_ids:
            # 如果指定了目标 ID，只处理这些 ID
            # 使用 SQLAlchemy 的 in_() 方法更安全
            from sqlalchemy import select
            from apps.data_training.models.data_training_model import DataTraining
            
            count_stmt = select(func.count()).select_from(DataTraining).where(
                DataTraining.id.in_(target_ids),
                DataTraining.description.isnot(None)
            )
            query_stmt = select(DataTraining.id, DataTraining.description).where(
                DataTraining.id.in_(target_ids),
                DataTraining.description.isnot(None)
            ).order_by(DataTraining.id)
            
            print(f"[INFO] Mode: Regenerate templates for specific IDs: {target_ids}")
            total = session.exec(count_stmt).first()
            rows = session.exec(query_stmt).all()
        elif force_regenerate:
            count_sql = text("""
                SELECT COUNT(1)
                FROM data_training
                WHERE description IS NOT NULL
            """)
            query_sql = text("""
                SELECT id, description
                FROM data_training
                WHERE description IS NOT NULL
                ORDER BY id
            """)
            print("[INFO] Mode: Force regenerate all templates")
            total = session.exec(count_sql).first()
        else:
            # 同时检查 NULL 和空字符串，因为前端删除时可能设置为空字符串
            count_sql = text("""
                SELECT COUNT(1)
                FROM data_training
                WHERE (sql_template IS NULL OR sql_template = '')
                AND description IS NOT NULL
            """)
            query_sql = text("""
                SELECT id, description
                FROM data_training
                WHERE (sql_template IS NULL OR sql_template = '')
                AND description IS NOT NULL
                ORDER BY id
            """)
            print("[INFO] Mode: Generate templates for records without sql_template (NULL or empty)")
            total = session.exec(count_sql).first()
        
        print(f"[INFO] Total records to process: {total}")

        if not total or total == 0:
            print("[INFO] No records need processing")
            return

        if not target_ids:
            rows = session.exec(query_sql).all()

        # 只初始化一次 LLM，提高性能
        print("[INFO] Initializing LLM service...")
        llm = init_llm_service()
        print("[INFO] LLM service initialized successfully")

        updated = 0
        failed = 0
        failed_ids = []
        all_processed_ids = list(processed_ids)

        try:
            for row in tqdm(rows, desc="Processing data_training"):
                if interrupted:
                    print("\n[INFO] Interrupted by user. Saving progress...")
                    break
                
                record_id = row.id
                
                # 跳过已处理的记录
                if skip_processed and record_id in processed_ids:
                    continue
                
                old_sql_template = row.description

                if not old_sql_template or old_sql_template.strip() == "":
                    failed += 1
                    failed_ids.append(record_id)
                    all_processed_ids.append(record_id)
                    print(f"\n[WARN] Skipping record id={record_id}: description is empty")
                    continue

                try:
                    print(f"\n[INFO] Processing record id={record_id}...")
                    result = get_sql_template(llm, old_sql_template, timeout=timeout, max_chunks=max_chunks)
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
                    all_processed_ids.append(record_id)
                    
                    # 每处理 5 条记录提交一次并保存进度，避免事务过大
                    if updated % 5 == 0:
                        session.commit()
                        save_progress(all_processed_ids, failed_ids)
                        print(f"[INFO] Progress saved: {updated} updated, {failed} failed")

                except Exception as e:  # noqa: BLE001
                    failed += 1
                    failed_ids.append(record_id)
                    all_processed_ids.append(record_id)
                    print(f"\n[ERROR] Process data_training id={record_id} failed: {e}")
                    # 继续处理下一条记录，不中断整个流程
                    
                    # 即使失败也保存进度
                    if len(all_processed_ids) % 5 == 0:
                        save_progress(all_processed_ids, failed_ids)

            # 最终提交
            session.commit()
            save_progress(all_processed_ids, failed_ids)
            
        except KeyboardInterrupt:
            print("\n[INFO] Keyboard interrupt. Saving progress...")
            save_progress(all_processed_ids, failed_ids)
            session.commit()
            raise

    print(f"\n[DONE] Summary:")
    print(f"  - Successfully updated: {updated}")
    print(f"  - Failed: {failed}")
    if failed_ids:
        print(f"  - Failed IDs: {failed_ids[:10]}{'...' if len(failed_ids) > 10 else ''}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate or regenerate SQL templates for data_training records"
    )
    parser.add_argument(
        "--force",
        "--regenerate",
        action="store_true",
        help="Force regenerate all templates (including existing ones). "
             "If not set, only process records where sql_template IS NULL."
    )
    parser.add_argument(
        "--skip-processed",
        action="store_true",
        help="Skip records that were already processed (based on progress file)."
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Timeout in seconds for each LLM request (default: 60)."
    )
    parser.add_argument(
        "--max-chunks",
        type=int,
        default=50000,
        help="Maximum number of chunks to process per LLM request (default: 50000). "
             "Increase this if you encounter 'max chunks limit' errors."
    )
    parser.add_argument(
        "--clear-progress",
        action="store_true",
        help="Clear progress file before starting."
    )
    parser.add_argument(
        "--ids",
        type=str,
        help="Comma-separated list of specific IDs to regenerate (e.g., '1,2,3'). "
             "This overrides other filtering options."
    )
    args = parser.parse_args()
    
    if args.clear_progress and progress_file.exists():
        progress_file.unlink()
        print("[INFO] Progress file cleared")
    
    # 解析目标 ID 列表
    target_ids = None
    if args.ids:
        try:
            target_ids = [int(id_str.strip()) for id_str in args.ids.split(',') if id_str.strip()]
            if not target_ids:
                print("[ERROR] No valid IDs provided in --ids argument")
                sys.exit(1)
            print(f"[INFO] Target IDs: {target_ids}")
        except ValueError as e:
            print(f"[ERROR] Invalid ID format in --ids argument: {e}")
            sys.exit(1)
    
    try:
        main(
            force_regenerate=args.force,
            skip_processed=args.skip_processed,
            timeout=args.timeout,
            max_chunks=args.max_chunks,
            target_ids=target_ids
        )
    except KeyboardInterrupt:
        print("\n[INFO] Script interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n[FATAL] Script failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)