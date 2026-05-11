#!/usr/bin/env python
"""Excel报告生成模块"""

import pandas as pd
from typing import List, Dict, Any

class ExcelReportGenerator:
    """Excel报告生成器"""

    def __init__(self):
        self.columns = [
            "question_id",
            "question",
            "expected_ds_id",
            "chat_id",
            "record_id",
            "actual_ds_id",
            # 问题改写模块
            "question_rewrite_input",
            "question_rewrite_output",
            # SQL生成相关
            "sql_gen_prompt",
            "sql_gen_terminology",
            "sql_gen_training_data",
            # SQL结果
            "sql_generated",
            "sql_status",
            "sql_error",
            "row_count",
            "chart_type",
            "analysis_text",
            # 耗时统计
            "step_question_enhance_ms",
            "step_terminology_ms",
            "step_training_ms",
            "step_sql_gen_ms",
            "step_sql_exec_ms",
            "step_chart_ms",
            "step_analysis_ms",
            "total_duration_ms",
            # 状态
            "success",
            "error_message"
        ]

    def generate_report(self, results: List[Dict[str, Any]], output_file: str):
        """生成Excel报告"""
        # 创建主数据表
        df = pd.DataFrame(results)

        # 确保所有列都存在
        for col in self.columns:
            if col not in df.columns:
                df[col] = ""

        # 重新排列列顺序
        df = df[self.columns]

        # 创建Excel写入器
        with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
            # 写入主数据表
            df.to_excel(writer, sheet_name="测试结果", index=False)

            # 生成统计汇总
            self._generate_summary(df, writer)

            # 设置列宽
            self._set_column_widths(writer)

        print(f"测试报告已生成: {output_file}")

    def _generate_summary(self, df: pd.DataFrame, writer):
        """生成统计汇总sheet"""
        total_count = len(df)
        success_count = df["success"].sum()
        fail_count = total_count - success_count
        success_rate = (success_count / total_count) * 100 if total_count > 0 else 0

        avg_duration = df["total_duration_ms"].mean() / 1000 if total_count > 0 else 0

        # 按步骤统计平均耗时
        step_columns = [
            "step_question_enhance_ms",
            "step_terminology_ms",
            "step_training_ms",
            "step_sql_gen_ms",
            "step_sql_exec_ms",
            "step_chart_ms",
            "step_analysis_ms"
        ]

        # 获取步骤耗时的平均值
        avg_step_durations = []
        for col in step_columns:
            if col in df.columns:
                avg_step_durations.append(df[col].mean() / 1000)
            else:
                avg_step_durations.append(0.0)

        # 构建汇总数据
        summary_data = [
            {"指标": "总问题数", "数值": total_count},
            {"指标": "成功数", "数值": success_count},
            {"指标": "失败数", "数值": fail_count},
            {"指标": "成功率(%)", "数值": f"{success_rate:.2f}"},
            {"指标": "平均总耗时(秒)", "数值": f"{avg_duration:.2f}"},
            {"指标": "", "数值": ""},
            {"指标": "平均问题增强耗时(秒)", "数值": f"{avg_step_durations[0]:.2f}"},
            {"指标": "平均术语检索耗时(秒)", "数值": f"{avg_step_durations[1]:.2f}"},
            {"指标": "平均训练数据耗时(秒)", "数值": f"{avg_step_durations[2]:.2f}"},
            {"指标": "平均SQL生成耗时(秒)", "数值": f"{avg_step_durations[3]:.2f}"},
            {"指标": "平均SQL执行耗时(秒)", "数值": f"{avg_step_durations[4]:.2f}"},
            {"指标": "平均图表生成耗时(秒)", "数值": f"{avg_step_durations[5]:.2f}"},
            {"指标": "平均数据分析耗时(秒)", "数值": f"{avg_step_durations[6]:.2f}"},
        ]

        summary_df = pd.DataFrame(summary_data)
        summary_df.to_excel(writer, sheet_name="统计汇总", index=False)

    def _set_column_widths(self, writer):
        """设置列宽"""
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for col in worksheet.columns:
                max_length = 0
                column = col[0].column_letter
                for cell in col:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column].width = adjusted_width