#!/usr/bin/env python
"""SQL生成质量评测脚本 - 主入口"""

import argparse
import asyncio
import json
import logging
import os
import sys

from data_loader import DataLoader, ProgressManager
from llm_scorer import LLMEvaluator
from report_generator import ReportGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def load_config(config_file: str) -> dict:
    """加载配置文件"""
    if not os.path.exists(config_file):
        logger.error(f"配置文件不存在: {config_file}")
        sys.exit(1)
    
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}")
        sys.exit(1)

async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="SQL生成质量评测脚本")
    parser.add_argument("-c", "--config", default="config.json", help="配置文件路径")
    parser.add_argument("-i", "--input", help="输入文件路径（覆盖配置）")
    parser.add_argument("-o", "--output", help="输出文件路径（覆盖配置）")
    parser.add_argument("--clear-progress", action="store_true", help="清除进度文件")
    parser.add_argument("--limit", type=int, help="限制处理数量")
    parser.add_argument("--sample", type=float, help="抽样百分比 (0.0-100.0)，如 --sample 10 表示抽取10%的数据")
    parser.add_argument("--sample-seed", type=int, default=42, help="抽样随机种子，保证结果可重复（默认42）")
    args = parser.parse_args()
    
    # 加载配置
    config = load_config(args.config)
    
    # 处理命令行参数覆盖
    if args.input:
        config["output"]["input_file"] = args.input
    if args.output:
        config["output"]["output_file"] = args.output
    
    input_file = config["output"]["input_file"]
    output_file = config["output"]["output_file"]
    progress_file = config["output"]["progress_file"]
    
    # 检查API密钥（兼容API可能不需要密钥）
    if not config["llm"].get("api_key") and not config["llm"].get("api_base"):
        logger.warning("未设置API密钥，部分API可能无法使用")
    
    # 初始化组件
    data_loader = DataLoader(input_file)
    progress_manager = ProgressManager(progress_file)
    llm_evaluator = LLMEvaluator(config)
    report_generator = ReportGenerator()
    
    # 清除进度
    if args.clear_progress:
        progress_manager.clear_progress()
    
    try:
        # 加载数据（支持抽样）
        if args.sample:
            if args.sample <= 0 or args.sample > 100:
                logger.error("抽样百分比必须在 0-100 之间")
                sys.exit(1)
            records = data_loader.get_sampled_records(args.sample, args.sample_seed)
        else:
            records = data_loader.get_records()
        
        # 限制数量（在抽样之后应用）
        if args.limit:
            records = records[:args.limit]
        
        # 获取待处理记录
        pending_records = progress_manager.get_pending_records(records)
        
        # 如果没有待处理记录，尝试从原始数据中获取已完成的结果
        if not pending_records:
            logger.info("没有待处理记录，直接生成报告")
            report_generator.generate_report(records, output_file)
            return
        
        # 评估记录
        logger.info(f"开始评估 {len(pending_records)} 条记录...")
        
        # 按question_id排序
        pending_records.sort(key=lambda x: x.get("question_id", 0))
        
        # 逐条评估
        results = []
        for record in pending_records:
            record_id = record.get("question_id")
            question = record.get("question", "")[:50]
            
            logger.info(f"Processing record {record_id}: {question}...")
            
            try:
                # 评估记录
                evaluation = await llm_evaluator.evaluate_record(record)
                
                # 合并结果
                result = {**record, **evaluation}
                
                # 标记完成并保存进度
                progress_manager.mark_completed(record_id)
                progress_manager.save_progress()
                
                results.append(result)
                
                logger.info(f"Record {record_id} completed - Overall: {evaluation.get('overall_score')}")
                
            except Exception as e:
                logger.error(f"处理记录 {record_id} 失败: {e}")
                record["error"] = str(e)
                results.append(record)
        
        # 合并已完成的记录
        for record in records:
            record_id = record.get("question_id")
            if progress_manager.is_completed(record_id) and record_id not in [r.get("question_id") for r in results]:
                results.append(record)
        
        # 生成报告
        report_generator.generate_report(results, output_file)
        
        logger.info("评估完成！")
        
    except Exception as e:
        logger.error(f"评估过程出错: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())