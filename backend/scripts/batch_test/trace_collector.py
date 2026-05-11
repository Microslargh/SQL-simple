#!/usr/bin/env python
"""执行轨迹收集模块"""

import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class ExecutionTrace:
    """执行轨迹收集器"""

    def __init__(self):
        self.trace: Dict[str, Any] = {
            "chat_id": None,
            "record_id": None,
            "actual_ds_id": None,
            "sql_generated": "",
            "sql_status": "unknown",
            "sql_error": "",
            "row_count": 0,
            "chart_type": "",
            "analysis_text": "",
            "step_question_enhance_ms": 0,
            "step_terminology_ms": 0,
            "step_training_ms": 0,
            "step_sql_gen_ms": 0,
            "step_sql_exec_ms": 0,
            "step_chart_ms": 0,
            "step_analysis_ms": 0,
            "total_duration_ms": 0,
            "success": False,
            "error_message": "",
            "raw_events": [],
            # 新增字段：问题改写模块
            "question_rewrite_input": "",
            "question_rewrite_output": "",
            # 新增字段：SQL生成提示词
            "sql_gen_prompt": "",
            "sql_gen_terminology": "",
            "sql_gen_training_data": ""
        }

    def parse_sse_events(self, events: List[Dict[str, Any]]):
        """解析SSE事件并提取轨迹信息"""
        self.raw_events = events

        for event in events:
            event_type = event.get("type")

            # 提取对话ID
            if event_type == "id":
                self.trace["record_id"] = event.get("id")

            # 提取数据源信息
            elif event_type == "datasource":
                self.trace["actual_ds_id"] = event.get("id")

            # 提取SQL
            elif event_type == "sql":
                self.trace["sql_generated"] = event.get("content", "")

            # 提取图表配置
            elif event_type == "chart":
                try:
                    chart_config = json.loads(event.get("content", "{}"))
                    self.trace["chart_type"] = chart_config.get("type", "")
                except json.JSONDecodeError:
                    pass

            # 提取分析结果
            elif event_type == "analysis-result":
                content = event.get("content", "")
                self.trace["analysis_text"] += content

            # 提取分析完成事件
            elif event_type == "analysis-replace":
                self.trace["analysis_text"] = event.get("content", "")

            # 提取步骤耗时
            elif event_type == "step-complete":
                step_name = event.get("step")
                duration_ms = event.get("duration_ms", 0)
                self._update_step_duration(step_name, duration_ms)

                # 提取步骤详情（问题改写和SQL生成的输入输出）
                step_data = event.get("data", {})
                if step_name == "question-rewrite" or step_name == "question-enhancement":
                    self.trace["question_rewrite_input"] = step_data.get("input", "")
                    self.trace["question_rewrite_output"] = step_data.get("output", "")
                    self.trace["question_rewrite_input"] = self.trace["question_rewrite_input"] or step_data.get("question", "")
                    self.trace["question_rewrite_output"] = self.trace["question_rewrite_output"] or step_data.get("enhanced_question", "")
                
                elif step_name == "sql-generation":
                    self.trace["sql_gen_prompt"] = step_data.get("prompt", "")
                    self.trace["sql_gen_terminology"] = step_data.get("terminology", "")
                    self.trace["sql_gen_training_data"] = step_data.get("training_data", "")

            # 提取总耗时
            elif event_type == "total_duration":
                self.trace["total_duration_ms"] = event.get("duration_ms", 0)

            # 提取错误信息 - 改进：检查是否有error事件且有有效错误内容
            elif event_type == "error":
                error_content = event.get("content") or event.get("message") or ""
                if error_content:
                    self.trace["success"] = False
                    self.trace["error_message"] = error_content
                    self.trace["sql_status"] = "failed"

            # 提取执行成功
            elif event_type == "sql-data":
                self.trace["sql_status"] = "success"

            # 提取步骤错误
            elif event_type == "step-error":
                self.trace["sql_status"] = "failed"
                step_error = event.get("message") or event.get("error") or ""
                if step_error and not self.trace["error_message"]:
                    self.trace["error_message"] = step_error

            # 提取完成事件
            elif event_type == "finish":
                self.trace["success"] = True

            # 提取问题改写信息（可能在其他事件类型中）
            elif event_type == "question-enhance":
                self.trace["question_rewrite_input"] = event.get("original", "")
                self.trace["question_rewrite_output"] = event.get("enhanced", "")

            # 提取SQL生成上下文
            elif event_type == "sql-context":
                self.trace["sql_gen_prompt"] = event.get("prompt", "")
                self.trace["sql_gen_terminology"] = event.get("terminology", "")
                self.trace["sql_gen_training_data"] = event.get("examples", "")

        # 综合判断成功状态
        self._determine_success()

    def _update_step_duration(self, step_name: str, duration_ms: int):
        """更新步骤耗时"""
        step_mapping = {
            "question-enhancement": "step_question_enhance_ms",
            "terminology-retrieval": "step_terminology_ms",
            "training-retrieval": "step_training_ms",
            "sql-generation": "step_sql_gen_ms",
            "sql-execution": "step_sql_exec_ms",
            "chart-generation": "step_chart_ms",
            "data-analysis": "step_analysis_ms",
            "question-rewrite": "step_question_enhance_ms",
            "sql-validation": "step_sql_gen_ms",
        }

        field_name = step_mapping.get(step_name)
        if field_name:
            self.trace[field_name] = duration_ms

    def _determine_success(self):
        """综合判断成功状态"""
        # 如果有明确的错误消息，标记为失败
        if self.trace.get("error_message"):
            self.trace["success"] = False
        # 如果sql执行成功且有record_id，标记为成功
        elif self.trace["sql_status"] == "success" and self.trace["record_id"]:
            self.trace["success"] = True
        # 如果是finish事件，标记为成功
        elif any(e.get("type") == "finish" for e in self.raw_events):
            self.trace["success"] = True

    def get_trace(self) -> Dict[str, Any]:
        """获取轨迹字典"""
        return self.trace

    def merge_with_record(self, record_data: Dict[str, Any]):
        """合并对话记录数据"""
        if record_data.get("id"):
            self.trace["chat_id"] = record_data["id"]

        records = record_data.get("records", [])
        if records:
            latest_record = records[-1]
            if latest_record.get("sql"):
                self.trace["sql_generated"] = latest_record["sql"]
            if latest_record.get("chart"):
                try:
                    chart_config = json.loads(latest_record.get("chart", "{}"))
                    self.trace["chart_type"] = chart_config.get("type", "")
                except json.JSONDecodeError:
                    pass
            if latest_record.get("analysis_answer"):
                try:
                    analysis = json.loads(latest_record.get("analysis_answer", "{}"))
                    self.trace["analysis_text"] = analysis.get("content", "")
                except json.JSONDecodeError:
                    pass

            # 从record中提取问题改写和SQL生成信息
            if latest_record.get("question"):
                self.trace["question_rewrite_input"] = self.trace["question_rewrite_input"] or latest_record["question"]
            
            if latest_record.get("enhanced_question"):
                self.trace["question_rewrite_output"] = self.trace["question_rewrite_output"] or latest_record["enhanced_question"]