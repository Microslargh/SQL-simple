#!/usr/bin/env python
"""SQLBot批量化测试脚本 - 主入口"""

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Dict, Any, List

import pandas as pd
from tqdm import tqdm

from api_client import SQLBotAPIClient
from rate_limiter import RateLimiter
from trace_collector import ExecutionTrace
from report_generator import ExcelReportGenerator

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("batch_test.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class BatchTestRunner:
    """批量化测试运行器"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.client = SQLBotAPIClient(
            api_base_url=config["api_base_url"],
            user_token=config["user_token"],
            timeout_seconds=config["timeout_seconds"]
        )
        self.rate_limiter = RateLimiter(
            requests_per_minute=config["requests_per_minute"],
            max_concurrent=config["max_concurrent"]
        )
        self.report_generator = ExcelReportGenerator()
        self.progress_file = ".batch_test_progress.json"
        self.datasource_id = config.get("datasource_id")
    
    async def run_single_test(self, question_data: Dict[str, Any]) -> Dict[str, Any]:
        """运行单个测试问题"""
        question_id = question_data["question_id"]
        question = question_data["question"]
        expected_ds_id = question_data.get("expected_ds_id", self.datasource_id)
        
        result = {
            "question_id": question_id,
            "question": question,
            "expected_ds_id": expected_ds_id
        }
        
        try:
            logger.info(f"Processing question {question_id}: {question[:50]}...")
            
            # 使用问题中指定的数据源或默认数据源
            ds_id = expected_ds_id if expected_ds_id else self.datasource_id
            if not ds_id:
                raise Exception("No datasource ID specified")
            
            # 创建新对话
            chat_result = await self.client.create_chat(datasource=ds_id)
            chat_id = chat_result.get("id")
            result["chat_id"] = chat_id
            logger.debug(f"Created chat: {chat_id}")
            
            # 获取record_id（从创建对话的响应中）
            records = chat_result.get("records", [])
            if records:
                record_id = records[0].get("id")
                result["record_id"] = record_id
            
            # 发送问题
            events = await self.client.send_question(chat_id, question)
            
            # 解析执行轨迹
            trace_collector = ExecutionTrace()
            trace_collector.parse_sse_events(events)
            
            # 获取对话记录详情
            try:
                record_id = trace_collector.trace.get("record_id") or result.get("record_id")
                if record_id:
                    record_data = await self.client.get_chat_record(chat_id, record_id)
                    trace_collector.merge_with_record(record_data)
            except Exception as e:
                logger.warning(f"Failed to get chat record: {e}")
            
            # 合并轨迹结果
            result.update(trace_collector.get_trace())
            
            logger.info(f"Question {question_id} completed: {result.get('success', False)}")
            
        except Exception as e:
            logger.error(f"Question {question_id} failed: {e}")
            result["success"] = False
            result["error_message"] = str(e)
        
        return result
    
    async def run_batch_test(self, questions: List[Dict[str, Any]], limit: int = None) -> List[Dict[str, Any]]:
        """运行批量测试
        
        Args:
            questions: 问题列表
            limit: 限制测试数量（None表示全部）
        """
        results = []
        completed_ids = self._load_progress()
        
        # 如果设置了限制，只测试前limit个问题
        if limit is not None and limit > 0:
            questions = questions[:limit]
            logger.info(f"限制测试数量: {limit} 个问题")
        
        # 过滤已完成的问题
        pending_questions = [q for q in questions if q["question_id"] not in completed_ids]
        completed_count = len(questions) - len(pending_questions)
        
        if completed_count > 0:
            logger.info(f"跳过已完成的 {completed_count} 个问题")
        
        # 逐个处理问题（受速率限制）
        for question_data in tqdm(pending_questions, desc="测试进度", initial=completed_count):
            result = await self.rate_limiter.run_with_limit(self.run_single_test, question_data)
            results.append(result)
            
            # 保存进度
            completed_ids.add(question_data["question_id"])
            self._save_progress(completed_ids)
            
            # 添加到已完成结果
            if completed_count > 0:
                completed_count = 0  # 只记录一次
        
        return results
    
    def _load_progress(self) -> set:
        """加载已完成的问题ID"""
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, "r") as f:
                    data = json.load(f)
                    return set(data.get("completed_ids", []))
            except:
                return set()
        return set()
    
    def _save_progress(self, completed_ids: set):
        """保存已完成的问题ID"""
        with open(self.progress_file, "w") as f:
            json.dump({"completed_ids": list(completed_ids)}, f)
    
    def load_questions(self, file_path: str) -> List[Dict[str, Any]]:
        """加载问题列表"""
        try:
            df = pd.read_excel(file_path)
            return df.to_dict("records")
        except Exception as e:
            logger.error(f"Failed to load questions file: {e}")
            raise
    
    def generate_report(self, results: List[Dict[str, Any]], output_file: str):
        """生成测试报告"""
        self.report_generator.generate_report(results, output_file)
    
    def clear_progress(self):
        """清除进度文件"""
        if os.path.exists(self.progress_file):
            os.remove(self.progress_file)

def load_config(config_file: str) -> Dict[str, Any]:
    """加载配置文件"""
    with open(config_file, "r") as f:
        return json.load(f)

def main():
    parser = argparse.ArgumentParser(description="SQLBot批量化测试脚本")
    parser.add_argument("-c", "--config", default="config.json", help="配置文件路径")
    parser.add_argument("--clear-progress", action="store_true", help="清除之前的测试进度")
    parser.add_argument("--limit", type=int, default=None, help="限制测试问题数量（如 --limit 5 只测试前5个）")
    args = parser.parse_args()
    
    # 加载配置
    config = load_config(args.config)
    
    # 验证配置
    if not config.get("user_token"):
        logger.error("请在配置文件中设置 user_token")
        sys.exit(1)
    
    if not config.get("datasource_id"):
        logger.error("请在配置文件中设置 datasource_id")
        sys.exit(1)
    
    # 创建测试运行器
    runner = BatchTestRunner(config)
    
    # 清除进度（如果需要）
    if args.clear_progress:
        runner.clear_progress()
        logger.info("已清除测试进度")
    
    # 加载问题列表
    questions = runner.load_questions(config["questions_file"])
    logger.info(f"已加载 {len(questions)} 个测试问题")
    
    # 执行测试
    limit_info = f", 限制数量: {args.limit}" if args.limit else ""
    logger.info(f"开始批量测试，速率: {config['requests_per_minute']} 个/分钟，数据源ID: {config['datasource_id']}{limit_info}")
    results = asyncio.run(runner.run_batch_test(questions, limit=args.limit))
    
    # 生成报告
    runner.generate_report(results, config["output_file"])
    
    # 统计结果
    success_count = sum(1 for r in results if r.get("success"))
    logger.info(f"测试完成! 成功: {success_count}/{len(results)}")

if __name__ == "__main__":
    main()