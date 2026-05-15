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
            # 新增字段：SQL生成提示词和上下文
            "sql_gen_prompt": "",
            "sql_gen_terminology": "",
            "sql_gen_training_data": ""
        }

    def parse_sse_events(self, events: List[Dict[str, Any]]):
        """解析SSE事件并提取轨迹信息"""
        self.raw_events = events

        for event in events:
            event_type = event.get("type")

            if event_type == "id":
                self.trace["record_id"] = event.get("id")

            elif event_type == "datasource":
                self.trace["actual_ds_id"] = event.get("id")

            elif event_type == "sql":
                self.trace["sql_generated"] = event.get("content", "")

            elif event_type == "chart":
                try:
                    chart_config = json.loads(event.get("content", "{}"))
                    self.trace["chart_type"] = chart_config.get("type", "")
                except json.JSONDecodeError:
                    pass

            elif event_type == "analysis-result":
                content = event.get("content", "")
                self.trace["analysis_text"] += content

            elif event_type == "analysis-replace":
                self.trace["analysis_text"] = event.get("content", "")

            elif event_type == "step-complete":
                step_name = event.get("step")
                duration_ms = event.get("duration_ms", 0)
                self._update_step_duration(step_name, duration_ms)

            elif event_type == "total_duration":
                self.trace["total_duration_ms"] = event.get("duration_ms", 0)

            elif event_type == "error":
                error_content = event.get("content") or event.get("message") or ""
                if error_content:
                    self.trace["success"] = False
                    self.trace["error_message"] = error_content
                    self.trace["sql_status"] = "failed"

            elif event_type == "sql-data":
                self.trace["sql_status"] = "success"

            elif event_type == "step-error":
                self.trace["sql_status"] = "failed"
                step_error = event.get("message") or event.get("error") or ""
                if step_error and not self.trace["error_message"]:
                    self.trace["error_message"] = step_error

            elif event_type == "finish":
                self.trace["success"] = True

        self._determine_success()

    def parse_execution_trace(self, trace_nodes: List[Dict[str, Any]]):
        """从执行轨迹API解析详细信息"""
        for node in trace_nodes:
            node_key = node.get("node_key")
            input_payload = node.get("input_payload", {})
            output_payload = node.get("output_payload", {})
            
            # 提取问题改写信息（prompt_build节点 - 所有路径都有）
            if node_key == "prompt_build":
                self.trace["question_rewrite_output"] = output_payload.get("enhanced_question", "")
            
            # 提取术语信息（terminology_retrieval节点）
            elif node_key == "terminology_retrieval":
                items = output_payload.get("items", [])
                terms = []
                for item in items:
                    words = item.get("words", [])
                    description = item.get("description", "")
                    terms.append(f"{', '.join(words)}: {description}")
                self.trace["sql_gen_terminology"] = "\n".join(terms)
            
            # 提取训练数据信息（training_retrieval节点）
            elif node_key == "training_retrieval":
                questions = output_payload.get("questions", [])
                template_ids = output_payload.get("template_ids", [])
                training_info = []
                for i, q in enumerate(questions):
                    template_id = template_ids[i] if i < len(template_ids) else ""
                    training_info.append(f"模板{template_id}: {q}")
                self.trace["sql_gen_training_data"] = "\n".join(training_info)
            
            # ========== 路径1：快速模板匹配成功 ==========
            elif node_key == "quick_template_match":
                # 检查模板是否匹配成功
                response_text = output_payload.get("response_text", "")
                try:
                    response_json = json.loads(response_text)
                    if response_json.get("success"):
                        # 模板匹配成功，提取提示词
                        messages = input_payload.get("messages", [])
                        prompt_parts = []
                        for msg in messages:
                            content = msg.get("content", "")
                            if content:
                                prompt_parts.append(content[:500])
                        self.trace["sql_gen_prompt"] = "\n\n".join(prompt_parts)
                except json.JSONDecodeError:
                    pass
            
            # ========== 路径2：快速模板匹配失败，走正常SQL生成路径 ==========
            # 问题重写节点（模板匹配失败时出现）
            elif node_key == "question_rewrite":
                # 提取改写后的问题
                rewritten_question = output_payload.get("rewritten_question", "")
                if rewritten_question:
                    self.trace["question_rewrite_output"] = rewritten_question
                
                # 提取输入问题（从input_payload的messages中）
                messages = input_payload.get("messages", [])
                for msg in messages:
                    if msg.get("type") == "human":
                        content = msg.get("content", "")
                        # 从content中提取user-question
                        if "<user-question>" in content:
                            start = content.find("<user-question>") + len("<user-question>")
                            end = content.find("</user-question>")
                            if start > 0 and end > start:
                                self.trace["question_rewrite_input"] = content[start:end].strip()
            
            # 问题重写汇总节点
            elif node_key == "question_rewrite_summary":
                self.trace["question_rewrite_input"] = self.trace["question_rewrite_input"] or input_payload.get("question_before", "")
                self.trace["question_rewrite_output"] = self.trace["question_rewrite_output"] or output_payload.get("question_after", "")
            
            # SQL生成节点（模板匹配失败时出现）
            elif node_key == "sql_generation":
                # 提取SQL生成的提示词
                messages = input_payload.get("messages", [])
                prompt_parts = []
                for msg in messages:
                    content = msg.get("content", "")
                    if content:
                        prompt_parts.append(content[:500])
                self.trace["sql_gen_prompt"] = "\n\n".join(prompt_parts)
                
                # 提取生成的SQL
                response_text = output_payload.get("response_text", "")
                try:
                    response_json = json.loads(response_text)
                    if response_json.get("success"):
                        self.trace["sql_generated"] = response_json.get("sql", "")
                except json.JSONDecodeError:
                    pass
            
            # SQL执行结果（两种路径都有）
            elif node_key == "sql_execution":
                self.trace["sql_generated"] = input_payload.get("sql", "")
                self.trace["row_count"] = output_payload.get("row_count", 0)
                self.trace["sql_status"] = "success"
            
            # 分析结果
            elif node_key == "analysis_generation":
                self.trace["analysis_text"] = output_payload.get("analysis_text", "")
            
            # 提取步骤耗时
            duration_ms = node.get("duration_ms", 0)
            if duration_ms > 0:
                self._update_step_duration_from_node(node_key, duration_ms)

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

    def _update_step_duration_from_node(self, node_key: str, duration_ms: int):
        """从轨迹节点更新步骤耗时"""
        step_mapping = {
            "terminology_retrieval": "step_terminology_ms",
            "training_retrieval": "step_training_ms",
            "quick_template_match": "step_sql_gen_ms",
            "question_rewrite": "step_question_enhance_ms",
            "sql_generation": "step_sql_gen_ms",
            "sql_execution": "step_sql_exec_ms",
            "chart_generation": "step_chart_ms",
            "analysis_generation": "step_analysis_ms",
        }

        field_name = step_mapping.get(node_key)
        if field_name:
            # 如果已经有值（比如模板匹配失败后又走了sql_generation），累加耗时
            if self.trace[field_name] > 0:
                self.trace[field_name] += duration_ms
            else:
                self.trace[field_name] = duration_ms

    def _determine_success(self):
        """综合判断成功状态"""
        if self.trace.get("error_message"):
            self.trace["success"] = False
        elif self.trace["sql_status"] == "success" and self.trace["record_id"]:
            self.trace["success"] = True
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
            if latest_record.get("analysis"):
                self.trace["analysis_text"] = latest_record["analysis"]
            
            if latest_record.get("question"):
                self.trace["question_rewrite_input"] = self.trace["question_rewrite_input"] or latest_record["question"]