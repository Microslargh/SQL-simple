#!/usr/bin/env python
"""报告生成模块"""

import logging
import os
from typing import List, Dict, Any

import pandas as pd

logger = logging.getLogger(__name__)

class ReportGenerator:
    """报告生成器"""

    def __init__(self):
        pass

    def generate_report(self, results: List[Dict[str, Any]], output_file: str):
        """生成评估报告"""
        # 创建详细结果DataFrame
        df = pd.DataFrame(results)
        
        # 确保列顺序合理
        columns_order = [
            "question_id",
            "question",
            "question_rewrite_input",
            "question_rewrite_output",
            "question_rewrite_score",
            "question_rewrite_reason",
            "sql_generated",
            "sql_generation_score",
            "sql_generation_reason",
            "overall_score",
            "success",
            "error_message"
        ]
        
        # 只保留存在的列
        existing_columns = [col for col in columns_order if col in df.columns]
        df = df[existing_columns + [col for col in df.columns if col not in columns_order]]
        
        # 创建统计汇总
        summary = self._generate_summary(df)
        
        # 确保输出目录存在
        output_dir = os.path.dirname(output_file)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        # 写入Excel文件
        with pd.ExcelWriter(output_file, engine="xlsxwriter") as writer:
            df.to_excel(writer, sheet_name="详细结果", index=False)
            summary.to_excel(writer, sheet_name="统计汇总", index=False)
            
            # 设置列宽
            for sheet_name in ["详细结果", "统计汇总"]:
                worksheet = writer.sheets[sheet_name]
                for i, col in enumerate(df.columns if sheet_name == "详细结果" else summary.columns):
                    worksheet.set_column(i, i, 20)
        
        logger.info(f"报告已生成: {output_file}")

    def _generate_summary(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成统计汇总"""
        summary_data = []
        
        # 总体统计
        total_count = len(df)
        success_count = df["success"].sum() if "success" in df.columns else total_count
        
        # 问题改写评分统计
        qr_scores = df["question_rewrite_score"].dropna()
        if not qr_scores.empty:
            summary_data.append({"指标": "问题改写评分-平均分", "数值": f"{qr_scores.mean():.2f}"})
            summary_data.append({"指标": "问题改写评分-最高分", "数值": f"{qr_scores.max():.0f}"})
            summary_data.append({"指标": "问题改写评分-最低分", "数值": f"{qr_scores.min():.0f}"})
            summary_data.append({"指标": "问题改写评分-标准差", "数值": f"{qr_scores.std():.2f}"})
            
            # 评分分布
            qr_dist = qr_scores.value_counts(bins=[0, 60, 70, 80, 90, 101], sort=False)
            bins_labels = ["0-60", "61-70", "71-80", "81-90", "91-100"]
            for label, count in zip(bins_labels, qr_dist):
                summary_data.append({"指标": f"问题改写评分-区间{label}数量", "数值": f"{count}"})
        
        # SQL生成评分统计
        sql_scores = df["sql_generation_score"].dropna()
        if not sql_scores.empty:
            summary_data.append({"指标": "SQL生成评分-平均分", "数值": f"{sql_scores.mean():.2f}"})
            summary_data.append({"指标": "SQL生成评分-最高分", "数值": f"{sql_scores.max():.0f}"})
            summary_data.append({"指标": "SQL生成评分-最低分", "数值": f"{sql_scores.min():.0f}"})
            summary_data.append({"指标": "SQL生成评分-标准差", "数值": f"{sql_scores.std():.2f}"})
            
            # 评分分布
            sql_dist = sql_scores.value_counts(bins=[0, 60, 70, 80, 90, 101], sort=False)
            bins_labels = ["0-60", "61-70", "71-80", "81-90", "91-100"]
            for label, count in zip(bins_labels, sql_dist):
                summary_data.append({"指标": f"SQL生成评分-区间{label}数量", "数值": f"{count}"})
        
        # 综合评分统计
        overall_scores = df["overall_score"].dropna()
        if not overall_scores.empty:
            summary_data.append({"指标": "综合评分-平均分", "数值": f"{overall_scores.mean():.2f}"})
            summary_data.append({"指标": "综合评分-最高分", "数值": f"{overall_scores.max():.0f}"})
            summary_data.append({"指标": "综合评分-最低分", "数值": f"{overall_scores.min():.0f}"})
            summary_data.append({"指标": "综合评分-标准差", "数值": f"{overall_scores.std():.2f}"})
        
        # 基础统计
        summary_data.insert(0, {"指标": "总记录数", "数值": f"{total_count}"})
        summary_data.insert(1, {"指标": "成功数", "数值": f"{success_count}"})
        summary_data.insert(2, {"指标": "成功率(%)", "数值": f"{(success_count/total_count*100):.2f}"})
        
        return pd.DataFrame(summary_data)