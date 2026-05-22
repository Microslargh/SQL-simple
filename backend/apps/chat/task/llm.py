import concurrent
import json
import os
import re
import traceback
import urllib.parse
import warnings
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime
from typing import Any, List, Optional, Union, Dict, Iterator
from uuid import uuid4

import numpy as np
import orjson
import pandas as pd
import requests
import sqlparse
from langchain.chat_models.base import BaseChatModel
from langchain_community.utilities import SQLDatabase
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, BaseMessageChunk
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import and_, select
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session

from apps.ai_model.model_factory import LLMConfig, LLMFactory, get_default_config
from apps.chat.curd.chat import save_question, save_sql_answer, save_sql, \
    save_error_message, save_sql_exec_data, save_chart_answer, save_chart, \
    finish_record, save_analysis_answer, save_predict_answer, save_predict_data, \
    save_select_datasource_answer, save_recommend_question_answer, \
    get_old_questions, save_analysis_predict_record, rename_chat, get_chart_config, \
    get_chat_chart_data, list_generate_sql_logs, list_generate_chart_logs, start_log, end_log, \
    get_last_execute_sql_error, start_execution_trace, end_execution_trace
from apps.chat.models.chat_model import ChatQuestion, ChatRecord, Chat, RenameChat, ChatLog, OperationEnum, \
    ChatFinishStep, ChatExecutionTrace, ChatExecutionTraceStatus
from apps.chat.context import ContextStateManager
from apps.chat.context.question_enhancer import is_any_follow_up
from apps.chat.utils.table_data_analyzer import (
    build_detail_sample,
    compute_table_summary,
    format_summary_for_prompt,
)
from sqlbot_xpack.license.license_manage import SQLBotLicenseUtil
# 兼容旧版本 sqlbot-xpack，custom_prompt 模块可能不存在
try:
    from sqlbot_xpack.custom_prompt.curd.custom_prompt import find_custom_prompts
    from sqlbot_xpack.custom_prompt.models.custom_prompt_model import CustomPromptTypeEnum
except ImportError:
    # 如果模块不存在，定义占位函数和枚举
    find_custom_prompts = None
    CustomPromptTypeEnum = None
from apps.data_training.curd.data_training import get_training_template, get_training_template_with_data, to_xml_string
from apps.datasource.crud.datasource import get_table_schema, get_table_schema_for_tables, get_table_schema_for_guess, get_table_schema_candidates
from apps.datasource.crud.permission import get_row_permission_filters, is_normal_user
from apps.datasource.embedding.ds_embedding import get_ds_embedding
from apps.datasource.models.datasource import CoreDatasource
from apps.db.db import exec_sql, get_version, check_connection
from apps.system.crud.assistant import AssistantOutDs, AssistantOutDsFactory, get_assistant_ds
from apps.system.schemas.system_schema import AssistantOutDsSchema
from apps.terminology.curd.terminology import get_terminology_template, get_terminology_template_with_data
from apps.template.generate_chart.generator import get_base_data_training_template
from apps.template.question_enhance.generator import get_question_enhance_template
from apps.chat.utils.entity_type_filter import filter_training_data_by_entity_type, infer_entity_type, ENTITY_REGION, ENTITY_COMPANY_INDUSTRY
from apps.chat.utils.implicit_param_extract import extract_implicit_replacements, apply_implicit_replacements
from common.core.config import settings
from common.core.db import engine
from common.core.deps import CurrentAssistant, CurrentUser
from common.error import SingleMessageError, SQLBotDBError, ParseSQLResultError, SQLBotDBConnectionError
from common.utils.utils import SQLBotLogUtil, extract_nested_json, prepare_for_orjson, _async_log_util

warnings.filterwarnings("ignore")

base_message_count_limit = 6

# Token estimation: Chinese ~1.5 chars/token, English ~4 chars/token
# Conservative estimate for mixed content using 2 chars/token
def _estimate_tokens(messages: list) -> int:
    """Estimate total tokens for a list of LangChain messages.
    Conservative estimate: 2 chars per token for mixed Chinese/English content.
    """
    total_chars = 0
    for msg in messages:
        content = getattr(msg, 'content', '') or ''
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            # Handle multimodal content lists
            total_chars += sum(len(part.get('text', '')) if isinstance(part, dict) else 0 for part in content)
    return total_chars // 2  # conservative: 2 chars per token

executor = ThreadPoolExecutor(max_workers=200)

dynamic_ds_types = [1, 3]
dynamic_subsql_prefix = 'select * from sqlbot_dynamic_temp_table_'

session_maker = sessionmaker(bind=engine)


def _parse_table_names_from_sql(sql: str) -> List[str]:
    """
    从 SQL 中解析出表名（FROM / JOIN 后的表），用于追问时补全这些表的字段定义。
    支持 "schema"."table"、schema.table、table 等写法。
    """
    if not sql or not sql.strip():
        return []
    names = []
    # 匹配 FROM / JOIN 后的表：双引号 "schema"."table" 或 单引号 或 无引号标识符
    # 忽略子查询 (SELECT ... FROM ...) 的简单做法：按 FROM/JOIN 切分后取第一个标识符
    sql_upper = sql.upper()
    # 匹配 FROM "xxx"."yyy" 或 FROM xxx.yyy 或 FROM yyy
    for m in re.finditer(
        r'\b(?:FROM|JOIN)\s+(["\']?)(\w+)["\']?\s*(?:\.\s*["\']?(\w+)["\']?)?',
        sql,
        re.IGNORECASE
    ):
        if m.group(3):
            names.append(f"{m.group(2)}.{m.group(3)}")
            names.append(m.group(3))  # 也加入纯表名便于匹配
        else:
            names.append(m.group(2))
    return list(dict.fromkeys(names))  # 去重保序


class LLMService:
    ds: CoreDatasource
    chat_question: ChatQuestion
    record: ChatRecord
    config: LLMConfig
    llm: BaseChatModel
    sql_message: List[Union[BaseMessage, dict[str, Any]]] = []
    straight_messages: List[Union[BaseMessage, dict[str, Any]]] = []
    rewrite_messages: List[Union[BaseMessage, dict[str, Any]]] = []
    chart_message: List[Union[BaseMessage, dict[str, Any]]] = []

    session: Session
    current_user: CurrentUser
    current_assistant: "CurrentAssistant | None" = None
    out_ds_instance: Optional[AssistantOutDs] = None
    change_title: bool = False

    generate_sql_logs: List[ChatLog] = []
    generate_chart_logs: List[ChatLog] = []

    current_logs: dict[OperationEnum, ChatLog] = {}
    trace_group: str
    _pipeline_trace: Optional[ChatExecutionTrace] = None

    chunk_list: List[str] = []
    future: Future

    last_execute_sql_error: str = None
    original_question: Optional[str] = None  # 保存原始问题，用于上下文分析

    def __init__(self, current_user: CurrentUser, chat_question: ChatQuestion,
                 current_assistant: "CurrentAssistant | None" = None, no_reasoning: bool = False,
                 embedding: bool = False, config: LLMConfig = None):
        self.chunk_list = []
        self.trace_group = uuid4().hex
        self._pipeline_trace = None
        # 必须使用“每个 LLMService 实例独立 Session”，避免并发请求共享同一个 Session 导致事务互相污染。
        self.session = session_maker()
        self.session.exec = self.session.exec if hasattr(self.session, "exec") else self.session.execute
        self.current_user = current_user
        self.current_assistant = current_assistant
        # chat = self.session.query(Chat).filter(Chat.id == chat_question.chat_id).first()
        chat_id = chat_question.chat_id
        chat: Chat | None = self.session.get(Chat, chat_id)
        if not chat:
            raise SingleMessageError(f"Chat with id {chat_id} not found")
        
        # 检查权限：只有创建者才能访问
        if chat.create_by != current_user.id:
            raise SingleMessageError(f"Permission denied: You don't have permission to access this chat")
        if config is None:
            raise SingleMessageError("LLM config is required")
        # 先初始化模型实例：后续表结构检索可能走 LLM 选表节点
        self.chat_question = chat_question
        self.config = config
        self.chat_question.ai_modal_id = self.config.model_id
        self.chat_question.ai_modal_name = self.config.model_name
        llm_instance = LLMFactory.create_llm(self.config)
        self.llm = llm_instance.llm

        ds: CoreDatasource | AssistantOutDsSchema | None = None
        if chat.datasource:
            # Get available datasource
            # ds = self.session.query(CoreDatasource).filter(CoreDatasource.id == chat.datasource).first()
            if current_assistant and current_assistant.type in dynamic_ds_types:
                self.out_ds_instance = AssistantOutDsFactory.get_instance(current_assistant)
                ds = self.out_ds_instance.get_ds(chat.datasource)
                if not ds:
                    raise SingleMessageError("No available datasource configuration found")
                chat_question.engine = ds.type + get_version(ds)
                chat_question.db_schema = self.out_ds_instance.get_db_schema(ds.id)
            else:
                ds = self.session.get(CoreDatasource, chat.datasource)
                if not ds:
                    raise SingleMessageError("No available datasource configuration found")
                chat_question.engine = (ds.type_name if ds.type != 'excel' else 'PostgreSQL') + get_version(ds)
                self.ds = CoreDatasource(**ds.model_dump())
                # 不在初始化阶段做选表/检索，避免非 SQL 主链路（如猜你想问）触发额外选表请求
                # 统一在 run_task 的 schema_retrieval 节点解析 db_schema
                chat_question.db_schema = ""

        self.generate_sql_logs = list_generate_sql_logs(session=self.session, chart_id=chat_id, current_user=current_user)
        # Enforce max conversation window to prevent context overflow
        max_turns = getattr(settings, "QUESTION_ENHANCE_MAX_TURNS", 7)
        if len(self.generate_sql_logs) > max_turns:
            self.generate_sql_logs = self.generate_sql_logs[-max_turns:]
        self.generate_chart_logs = list_generate_chart_logs(session=self.session, chart_id=chat_id, current_user=current_user)

        self.change_title = len(self.generate_sql_logs) == 0

        chat_question.lang = get_lang_name(current_user.language)

        self.ds = (ds if isinstance(ds, AssistantOutDsSchema) else CoreDatasource(**ds.model_dump())) if ds else None
        if no_reasoning:
            # only work while using qwen
            if self.config.additional_params:
                if self.config.additional_params.get('extra_body'):
                    if self.config.additional_params.get('extra_body').get('enable_thinking'):
                        del self.config.additional_params['extra_body']['enable_thinking']

        # get last_execute_sql_error
        last_execute_sql_error = get_last_execute_sql_error(self.session, self.chat_question.chat_id, current_user)
        if last_execute_sql_error:
            self.chat_question.error_msg = f'''<error-msg>
{last_execute_sql_error}
</error-msg>'''
        else:
            self.chat_question.error_msg = ''

        # 法人户数 cqs 表：SQL 中时间批次被修正为库内最新时，写入分析提示
        self._cqs_snapshot_time_notice: Optional[str] = None

    @classmethod
    async def create(cls, *args, **kwargs):
        config: LLMConfig = await get_default_config()
        instance = cls(*args, **kwargs, config=config)
        return instance

    def _close_session_safely(self):
        try:
            if getattr(self, "session", None):
                self.session.close()
        except Exception:
            pass

    def is_running(self, timeout=0.5):
        try:
            r = concurrent.futures.wait([self.future], timeout)
            if len(r.not_done) > 0:
                return True
            else:
                return False
        except Exception as e:
            return True

    def _enhance_question_by_llm(self, current_question: str, history_question: str) -> Optional[str]:
        """使用大模型将含指代词或「时间追问」的当前问题补充为完整、自然的一句问句。失败或无效时返回 None。"""
        reference_words = ['这些', '它们', '上述', '上面', '刚才', '之前', '上一轮', '刚才的', '那些']
        need_expand = any(word in current_question for word in reference_words) or is_any_follow_up(current_question)
        if not need_expand:
            return None
        if not (current_question and history_question):
            return None
        try:
            tpl = get_question_enhance_template()
            sys_content = tpl['system']
            user_content = tpl['user'].format(
                history_question=history_question,
                current_question=current_question
            )
            messages = [SystemMessage(content=sys_content), HumanMessage(content=user_content)]
            response = self.llm.invoke(messages)
            enhanced = self._extract_message_text(response)
            if enhanced and enhanced != current_question:
                _async_log_util.info(f"[问题增强-LLM] 原始: {current_question}")
                _async_log_util.info(f"[问题增强-LLM] 补充后: {enhanced}")
                return enhanced
        except Exception as e:
            _async_log_util.debug(f"[问题增强-LLM] 调用失败，将使用规则补充: {e}")
        return None

    def _get_user_question_from_log(self, log: ChatLog) -> Optional[str]:
        """从日志得到当轮用户真实问题。优先用 ChatRecord.question；否则从 messages 取 human 且跳过 <context> 等上下文块。"""
        if log.pid:
            record = self.session.get(ChatRecord, log.pid)
            if record and getattr(record, 'question', None):
                return (record.question or '').strip()
        if not log.messages:
            return None
        for msg in log.messages:
            if msg.get('type') != 'human':
                continue
            content = (msg.get('content') or '').strip()
            if not content or content.startswith('<context>') or content.startswith('<time-range') or content.startswith('<history'):
                continue
            return content
        return None

    def _get_effective_current_time(self) -> str:
        """获取用于 prompt 的 current_time（不再依赖固定表名推断快照时点）。"""
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    @staticmethod
    def _sql_json_response_format() -> dict:
        """SQL 生成结构化输出约束：优先要求模型只返回可解析 JSON。"""
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "sqlbot_sql_response",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "sql": {"type": "string"},
                        "tables": {"type": "array", "items": {"type": "string"}},
                        "chart-type": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["success"],
                    "additionalProperties": True
                }
            }
        }

    @staticmethod
    def _chart_json_response_format() -> dict:
        """图表生成结构化输出约束：至少保证返回对象并包含 type。"""
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "sqlbot_chart_response",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "reason": {"type": "string"},
                        "axis": {"type": "object"},
                        "columns": {"type": "array"},
                        "name": {"type": "string"},
                        "data": {"type": "array"},
                    },
                    "required": ["type"],
                    "additionalProperties": True
                }
            }
        }

    @staticmethod
    def _double_check_response_format() -> dict:
        """快速模板二次校验结构化输出：避免模型混合输出 True/False。"""
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "sqlbot_double_check_response",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "matched": {"type": "boolean"}
                    },
                    "required": ["matched"],
                    "additionalProperties": False
                }
            }
        }

    @staticmethod
    def _rewrite_response_format() -> dict:
        """问题重写结构化输出：统一返回 question_rewrite 字段。"""
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "sqlbot_rewrite_response",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "question_rewrite": {"type": "string"}
                    },
                    "required": ["question_rewrite"],
                    "additionalProperties": False
                }
            }
        }

    def _stream_with_response_format(
            self,
            messages: List[Union[BaseMessage, dict[str, Any]]],
            response_format: Optional[dict],
            log_prefix: str
    ):
        """
        优先使用 response_format 约束模型返回 JSON；
        若服务端不兼容或调用失败，自动回退到普通 stream。
        """
        if response_format:
            try:
                structured_llm = self.llm.bind(response_format=response_format)
                _async_log_util.info(f"[{log_prefix}] 已启用 response_format=json_schema")
                for chunk in structured_llm.stream(messages):
                    yield chunk
                return
            except Exception as e:
                _async_log_util.warning(f"[{log_prefix}] json_schema 调用失败，回退普通模式: {str(e)}")

        for chunk in self.llm.stream(messages):
            yield chunk

    @staticmethod
    def _extract_message_text(msg: Any) -> str:
        """兼容 content/reasoning/tool_calls 的统一文本提取。"""
        content_obj = getattr(msg, "content", None)
        if isinstance(content_obj, str) and content_obj.strip():
            return content_obj.strip()
        if isinstance(content_obj, list):
            parts: List[str] = []
            for item in content_obj:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
                elif isinstance(item, dict):
                    txt = item.get("text") or item.get("content") or item.get("value")
                    if txt:
                        parts.append(str(txt).strip())
            if parts:
                return "\n".join(parts).strip()
        if isinstance(content_obj, dict):
            txt = content_obj.get("text") or content_obj.get("content") or content_obj.get("value")
            if txt:
                return str(txt).strip()

        # 部分 vLLM/Qwen 网关：content 为空，正文在 reasoning（与 raw HTTP 一致）
        for attr in ("reasoning_content", "reasoning"):
            v = getattr(msg, attr, None)
            if isinstance(v, str) and v.strip():
                return v.strip()

        additional = getattr(msg, "additional_kwargs", None) or {}
        parsed = additional.get("parsed")
        if parsed:
            try:
                return orjson.dumps(parsed).decode()
            except Exception:
                return str(parsed).strip()
        for k in ("output_text", "text", "response", "message", "reasoning_content", "reasoning"):
            v = additional.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        tool_calls = additional.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str) and args.strip():
                    return args.strip()
        return str(content_obj or "").strip()

    @staticmethod
    def _extract_text_from_raw_api_message(msg: Any) -> str:
        """
        从 OpenAI 兼容 HTTP 返回的 message 对象中提取可解析文本。
        与 _extract_message_text 对齐：支持字符串 / 列表分片 / dict 形态 content、
        structured outputs 的 parsed、reasoning、tool_calls 等。

        常见情况（如 vLLM + Qwen）：content 为 null，助手正文仅在 reasoning 字段。
        """
        if not isinstance(msg, dict):
            return ""
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
                elif isinstance(item, dict):
                    txt = item.get("text") or item.get("content") or item.get("value")
                    if isinstance(txt, str) and txt.strip():
                        parts.append(txt.strip())
            if parts:
                return "\n".join(parts).strip()
        if isinstance(content, dict):
            txt = content.get("text") or content.get("content") or content.get("value")
            if isinstance(txt, str) and txt.strip():
                return txt.strip()
        parsed = msg.get("parsed")
        if parsed is not None:
            if isinstance(parsed, (dict, list)):
                try:
                    return orjson.dumps(parsed).decode()
                except Exception:
                    return str(parsed).strip()
            if isinstance(parsed, str) and parsed.strip():
                return parsed.strip()
        reasoning = msg.get("reasoning") or msg.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning.strip()
        for k in ("output_text", "text", "response"):
            v = msg.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        tool_calls = msg.get("tool_calls") or []
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                fn = (tc or {}).get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str) and args.strip():
                    return args.strip()
        function_call = msg.get("function_call") or {}
        args = function_call.get("arguments")
        if isinstance(args, str) and args.strip():
            return args.strip()
        return ""

    def _invoke_openai_compatible_raw(
            self,
            messages: List[dict[str, str]],
            response_format: Optional[dict] = None,
            timeout_sec: int = 20,
    ) -> str:
        """
        绕过 LangChain 消息适配层，直接调用 OpenAI 兼容接口并提取文本。
        兼容 content（含列表分片）/parsed/reasoning/reasoning_content/tool_calls/function_call。
        """
        if not self.config or not self.config.api_base_url:
            return ""
        base = (self.config.api_base_url or "").strip().rstrip("/")
        if base.endswith("/chat/completions"):
            url = base
        elif base.endswith("/v1"):
            url = f"{base}/chat/completions"
        else:
            url = f"{base}/v1/chat/completions"

        payload: dict[str, Any] = {
            "model": (self.config.model_name or "").strip(),
            "messages": messages,
            "stream": False,
        }
        # 透传模型配置中的附加参数（如 extra_body 等）
        if getattr(self.config, "additional_params", None):
            payload.update(self.config.additional_params or {})
        if response_format:
            payload["response_format"] = response_format

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        try:
            # 使用(connect_timeout, read_timeout)避免长时间无响应挂起
            resp = requests.post(url, headers=headers, json=payload, timeout=(5, timeout_sec))
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                msg = choice.get("message") or {}
                text = self._extract_text_from_raw_api_message(msg if isinstance(msg, dict) else {})
                if text:
                    return text
                legacy = choice.get("text")
                if isinstance(legacy, str) and legacy.strip():
                    return legacy.strip()
            if choices:
                first = choices[0] if isinstance(choices[0], dict) else {}
                m = first.get("message") if isinstance(first, dict) else {}
                _async_log_util.warning(
                    f"[raw_http] 助手消息无可用文本 | finish_reason={first.get('finish_reason')!r} "
                    f"| message_keys={list(m.keys()) if isinstance(m, dict) else 'n/a'}"
                )
            return ""
        except Exception as e:
            _async_log_util.warning(f"[raw_http] OpenAI 兼容请求失败: {e}")
            return ""

    @staticmethod
    def _table_selector_response_format() -> dict:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "sqlbot_table_selector",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "tables": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["tables"],
                    "additionalProperties": False
                }
            }
        }

    def _terminology_snippet_for_table_selector(self, question: str) -> str:
        """
        优先复用当前流程已检索好的术语结果（self.chat_question.terminologies），
        直接注入到 LLM 选表提示词中，避免重复检索。
        """
        try:
            terms = (self.chat_question.terminologies or "").strip()
            if not terms:
                return ""
            # 术语模板本身为结构化文本（XML片段），按既有流程原样注入即可。
            return "\n\n术语参考（来自术语检索结果）：\n" + terms + "\n"
        except Exception:
            return ""

    def _select_tables_by_llm(self, question: str) -> List[str]:
        """
        使用 LLM 从当前数据源所有候选表中选择最相关表名。
        失败时返回空列表，由调用方决定回退策略。
        """
        if not self.ds or not getattr(settings, "TABLE_SELECTOR_LLM_ENABLED", False):
            return []

        try:
            _async_log_util.info("[LLM选表] 开始执行选表")
            candidates = get_table_schema_candidates(
                session=self.session,
                current_user=self.current_user,
                ds=self.ds
            )
            if not candidates:
                _async_log_util.warning("[LLM选表] 候选表为空，跳过选表")
                return []

            # 十几张表场景：直接把全部候选摘要给 LLM
            # 控制输入长度：每张表最多展示前 12 行 schema 片段
            table_blocks = []
            valid_table_names = set()
            candidate_names = []
            for c in candidates:
                name = (c.get("table_name") or "").strip()
                if not name:
                    continue
                valid_table_names.add(name.lower())
                candidate_names.append(name)
                schema_table = c.get("schema_table") or ""
                brief = "\n".join(schema_table.splitlines()[:12])
                table_blocks.append(brief)

            if not table_blocks:
                _async_log_util.warning("[LLM选表] 候选表摘要为空，跳过选表")
                return []

            topk = max(1, int(getattr(settings, "TABLE_SELECTOR_LLM_TOPK", 1) or 1))
            # 强约束：选表节点固定返回 1 张表，避免空值影响下游
            topk = 1
            terminology_snippet = self._terminology_snippet_for_table_selector(question or "")
            system_prompt = (
                "你是 SQL 选表器。根据用户问题，从候选表中选出最相关的表名。"
                "只允许从候选列表中选择，不得编造。"
                "必须返回且仅返回 1 个表名，不能为空。"
                f"返回 JSON: {{\"tables\": [\"table_name\"]}}，长度必须为 {topk}。"
            )
            if terminology_snippet.strip():
                system_prompt += " 若用户消息中提供了业务术语说明，请结合术语含义理解指标与维度后再选表。"
            user_prompt = (
                " 表规范："
                "1. 表格名称规则为dwm_ai_wenshu_xxx_[group/company/region]_[month/year/constant],  代表主键为公司，集团，区域。周期为月，年，固定。"
                "2. 一般而言，公司名company_name，集团名group_name是表的主键，月度日期字段datetime_month，年度日期字段是datetime_year"
                "3. 字段注释必定包含的字段名，有可能包含，别名，信息，备注等别的额外信息"
                "选表规则："
                "1. 年度问题优先使用年表（year），如果没有提供对应的年表，再选择月表"
                "2. 集团问题优先使用集团表（group），如果没有提供对应的集团表，再使用公司表/区域表"
                "3. 表选择要考虑表注释，和字段注释。特别是表注释的周期，维度，相关问题。字段注释的字段名，别名，备注信息。"
                "相关术语："
                + terminology_snippet + "\n"
                f"用户问题：{question or ''}\n\n"
                "候选表结构（节选）：\n"
                + "\n".join(table_blocks)
            )

            # 选表优先走 raw_http（有明确 timeout），避免 LangChain client 重试导致长时间阻塞
            _async_log_util.info("[LLM选表] 调用模型（structured）")
            raw = self._invoke_openai_compatible_raw(
                messages=[
                    {"role": "system", "content": system_prompt + " 不要输出解释，只输出纯JSON。"},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=self._table_selector_response_format(),
                timeout_sec=15,
            )
            # 无结构化结果时再试一次普通 JSON 返回
            if not raw:
                _async_log_util.info("[LLM选表] structured为空，调用模型（plain_json）")
                raw = self._invoke_openai_compatible_raw(
                    messages=[
                        {"role": "system", "content": system_prompt + " 不要输出解释，只输出纯JSON。"},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format=None,
                    timeout_sec=15,
                )
            tables: List[Any] = []
            if raw:
                json_str = extract_nested_json(raw) or raw
                data = orjson.loads(json_str)
                tables = data.get("tables", []) if isinstance(data, dict) else []
            if not isinstance(tables, list):
                tables = []

            def _normalize_selected(raw_tables: List[Any]) -> List[str]:
                selected = []
                for t in raw_tables:
                    name = str(t or "").strip().strip('"').strip("'")
                    if not name:
                        continue
                    key = name.split(".")[-1].lower()
                    if key in valid_table_names and key not in [s.lower() for s in selected]:
                        selected.append(name.split(".")[-1])
                    if len(selected) >= topk:
                        break
                return selected

            selected = _normalize_selected(tables)

            # 强制二次修正：若仍无结果，用“候选表名清单 + 闭集输出”再问一次
            if not selected:
                enforce_system = (
                    "你是 SQL 选表器。必须从给定候选表名中选择且仅选择一个。"
                    "禁止返回空值，禁止返回候选外名称。"
                    "只返回 JSON: {\"tables\": [\"table_name\"]}"
                )
                enforce_user = (
                    f"用户问题：{question or ''}\n"
                    f"候选表名：{candidate_names}\n"
                    "请只返回1个最相关表名。"
                    + terminology_snippet
                )
                enforce_msg = self.llm.invoke([
                    SystemMessage(content=enforce_system),
                    HumanMessage(content=enforce_user),
                ])
                enforce_raw = self._extract_message_text(enforce_msg)
                if not enforce_raw:
                    _async_log_util.info("[LLM选表] force_pick_one 调用模型（structured）")
                    enforce_raw = self._invoke_openai_compatible_raw(
                        messages=[
                            {"role": "system", "content": enforce_system},
                            {"role": "user", "content": enforce_user},
                        ],
                        response_format=self._table_selector_response_format(),
                        timeout_sec=12,
                    )
                if enforce_raw:
                    try:
                        enforce_json = extract_nested_json(enforce_raw) or enforce_raw
                        enforce_data = orjson.loads(enforce_json)
                        enforce_tables = enforce_data.get("tables", []) if isinstance(enforce_data, dict) else []
                        if isinstance(enforce_tables, list):
                            selected = _normalize_selected(enforce_tables)
                    except Exception:
                        pass

            # 最终兜底：保证永远返回 1 张有效表
            if not selected and candidate_names:
                q = (question or "").lower()
                preferred = None
                for n in candidate_names:
                    n_low = n.lower()
                    if ("group" in q or "集团" in q) and "group" in n_low:
                        preferred = n
                        break
                    if ("company" in q or "公司" in q) and "company" in n_low:
                        preferred = n
                        break
                    if ("region" in q or "地区" in q or "区域" in q) and "region" in n_low:
                        preferred = n
                        break
                    if ("year" in q or "年" in q) and "year" in n_low:
                        preferred = n
                        break
                    if ("month" in q or "月" in q) and "month" in n_low:
                        preferred = n
                        break
                selected = [preferred or candidate_names[0]]
                _async_log_util.warning(f"[LLM选表] 模型未返回有效表，使用兜底表: {selected[0]}")

            _async_log_util.info(f"[LLM选表] 选表完成: {selected}")
            return selected
        except Exception as e:
            _async_log_util.warning(f"[LLM选表] 选表失败，回退默认检索: {str(e)}")
            return []

    def _resolve_db_schema_for_sql(self, question: str, embedding: bool = True) -> str:
        """
        SQL 生成前统一的表结构解析入口：
        1) 动态数据源：沿用外部 schema；
        2) 普通数据源：优先 LLM 选表 -> 指定表 schema；
        3) 选表失败：按 strict 开关决定回退或报错。
        """
        _async_log_util.info(
            f"[表结构获取][入口] selector_enabled={getattr(settings, 'TABLE_SELECTOR_LLM_ENABLED', False)} "
            f"| strict={getattr(settings, 'TABLE_SELECTOR_LLM_STRICT', True)} "
            f"| embedding={embedding} | question_len={len(question or '')}"
        )

        if self.out_ds_instance:
            _async_log_util.info("[表结构获取] 使用外部数据源 schema（out_ds_instance）")
            return self.out_ds_instance.get_db_schema(self.ds.id)

        if getattr(settings, "TABLE_SELECTOR_LLM_ENABLED", False):
            selected_tables = self._select_tables_by_llm(question or "")
            if selected_tables:
                schema = get_table_schema_for_tables(
                    session=self.session,
                    current_user=self.current_user,
                    ds=self.ds,
                    table_names=selected_tables,
                )
                if schema and schema.strip():
                    return schema
                _async_log_util.warning(
                    f"[表结构获取] 指定表schema为空 | selected_tables={selected_tables} | schema_len={len(schema or '')}"
                )
            if getattr(settings, "TABLE_SELECTOR_LLM_STRICT", True):
                raise SingleMessageError("LLM table selection failed: no valid table selected")
            _async_log_util.warning("[LLM选表] 未选中有效表，回退默认表结构检索")
        else:
            _async_log_util.info("[表结构获取] TABLE_SELECTOR_LLM_ENABLED=False，跳过 LLM 选表")

        return get_table_schema(
            session=self.session,
            current_user=self.current_user,
            ds=self.ds,
            question=question,
            embedding=embedding
        )

    @staticmethod
    def _basic_sql_sanity_error(sql: str) -> Optional[str]:
        """
        轻量语法体检：用于在落库前提前拦截常见拼接错误。
        """
        if not sql or not sql.strip():
            return "SQL is empty"
        s = sql.strip()
        if re.search(r'\bAS\s+"[^"]*"\s+AS\s+"[^"]*"', s, re.IGNORECASE):
            return "SQL contains duplicated alias assignment (AS ... AS ...)"
        if s.count("(") != s.count(")"):
            return "SQL has unbalanced parentheses"
        if s.count("'") % 2 != 0:
            return "SQL has unmatched single quote"
        return None

    def _build_syntax_check_sql(self, sql: str) -> Optional[str]:
        """
        为不同引擎构造“仅语法检查”语句（不执行原查询数据扫描）。
        返回 None 表示当前引擎暂不支持专用语法检查，交由后续执行阶段兜底。
        """
        if not self.ds or not getattr(self.ds, "type", None):
            return None
        origin_sql = (sql or "").strip().rstrip(";")
        if not origin_sql:
            return None
        ds_type = (self.ds.type or "").lower()

        # 优先覆盖当前项目常见引擎；ClickHouse 用 EXPLAIN SYNTAX 最稳
        if ds_type == "ck":
            return f"EXPLAIN SYNTAX {origin_sql}"
        if ds_type in {"mysql", "doris", "pg", "redshift", "kingbase", "excel"}:
            return f"EXPLAIN {origin_sql}"
        if ds_type == "sqlserver":
            return f"SET PARSEONLY ON; {origin_sql}; SET PARSEONLY OFF;"
        if ds_type == "oracle":
            return f"EXPLAIN PLAN FOR {origin_sql}"
        # dm/es 等引擎不统一，先返回 None
        return None

    def _db_syntax_error(self, sql: str) -> Optional[str]:
        """
        使用数据库语法器做一手校验（第一性原则）：能通过数据库解析才算可执行 SQL。
        """
        check_sql = self._build_syntax_check_sql(sql)
        if not check_sql:
            return None
        try:
            exec_sql(self.ds, check_sql, True)
            return None
        except Exception as e:
            return str(e)

    @staticmethod
    def _classify_sql_error(error_msg: str) -> tuple[str, str]:
        """
        将数据库报错归类，返回 (error_type, fix_hint)。
        """
        msg = (error_msg or "").lower()
        if any(k in msg for k in ["syntax", "parse", "parser", "token", "unexpected", "expected"]):
            return "syntax_error", "修复 SQL 语法结构（关键字、逗号、括号、别名、引号）"
        if any(k in msg for k in ["unknown column", "column not found", "no such column", "cannot find column"]):
            return "missing_column", "仅使用 schema 中存在的字段，修复拼写或改用正确字段"
        if any(k in msg for k in ["unknown table", "table not found", "no such table", "relation does not exist"]):
            return "missing_table", "仅使用已提供的表名，修复库名/表名拼写和引用方式"
        if any(k in msg for k in ["unknown function", "function not found", "no function matches", "unsupported function"]):
            return "unsupported_function", "改用当前数据库支持的等价函数"
        if any(k in msg for k in ["ambiguous", "is ambiguous"]):
            return "ambiguous_reference", "为冲突字段补充表别名，消除歧义"
        if any(k in msg for k in ["duplicate", "already exists", "duplicated alias", "multiple aliases"]):
            return "duplicate_alias", "移除重复别名/重复字段定义，保证每个表达式别名唯一"
        return "other_error", "在不改变业务语义的前提下修复可执行性问题"

    def _repair_sql_answer_once(self, raw_answer: str, error_msg: str) -> str:
        """
        让 LLM 基于错误信息修复 SQL，要求只返回结构化 JSON。
        """
        error_type, fix_hint = self._classify_sql_error(error_msg)
        _async_log_util.info(
            f"[SQL自动修复] 开始修复 | error_type={error_type} | error_msg={error_msg[:500]}"
        )
        _async_log_util.info(
            f"[SQL自动修复][输入-原始回答] {str(raw_answer)[:1500]}"
        )
        system_prompt = (
            "你是 SQL 语法修复器。"
            "你会收到一个 SQLBot 的原始 JSON/文本回答和错误信息。"
            "请只修复 SQL 语法，不改变查询语义、筛选条件、指标和表名。"
            f"错误类型：{error_type}。修复目标：{fix_hint}。"
            "只返回 JSON 对象，格式："
            "{\"success\": true, \"sql\": \"...\", \"tables\": [\"...\"], \"chart-type\": \"table\"}。"
        )
        user_prompt = (
            f"数据库引擎：{self.chat_question.engine}\n"
            f"错误信息：{error_msg}\n"
            f"原始回答：\n{raw_answer}\n"
        )
        msgs = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        repair_llm = self.llm.bind(response_format=self._sql_json_response_format())
        msg = repair_llm.invoke(msgs)
        fixed = self._extract_message_text(msg)
        if not fixed:
            _async_log_util.warning("[SQL自动修复] response_format 返回空内容，回退 plain_json 重试")
            plain_msg = self.llm.invoke([
                SystemMessage(content=system_prompt + " 不要输出解释，只输出纯JSON。"),
                HumanMessage(content=user_prompt),
            ])
            fixed = self._extract_message_text(plain_msg)
        if not fixed:
            raise SingleMessageError("SQL auto-fix returned empty content")
        _async_log_util.info(
            f"[SQL自动修复][输出-修复回答] {fixed[:1500]}"
        )
        return fixed

    def _deterministic_sql_fix(self, raw_answer: str, error_msg: str) -> Optional[str]:
        """
        规则化兜底修复（当 LLM 修复无效时使用），优先处理高频方言错误。
        当前覆盖：ClickHouse 中未加引号的中文别名导致语法错误。
        """
        try:
            sql, tables = self.check_sql(raw_answer)
        except Exception:
            return None

        ds_type = (getattr(self.ds, "type", "") or "").lower()
        msg = (error_msg or "").lower()
        fixed_sql = sql

        # ClickHouse: AS 后中文别名需加双引号
        # 例：AS 主键_公司名, -> AS "主键_公司名",
        if ds_type == "ck":
            # 匹配 AS 后跟中文或特殊字符的别名，处理多种边界情况
            # 场景1: AS 中文别名,  → AS "中文别名",
            # 场景2: AS 中文别名\n → AS "中文别名"
            # 场景3: AS 中文别名 FROM → AS "中文别名" FROM
            def fix_clickhouse_alias(sql_text: str) -> str:
                # 分多轮处理，确保各种边界情况都被覆盖
                # 第一轮：处理 AS 别名, 或 AS 别名\n 的情况
                pattern1 = r'(?i)\bAS\s+([\u4e00-\u9fff][^,\n\r]*?)(?=\s*,|\s*\n|\s+FROM|\s+WHERE|\s+GROUP|\s+ORDER|\s+HAVING|\s+LIMIT|\s*$)'

                def replace_alias(m):
                    alias = m.group(1).strip()
                    # 如果已经加引号了，跳过
                    if (alias.startswith('"') and alias.endswith('"')) or \
                       (alias.startswith('`') and alias.endswith('`')):
                        return m.group(0)
                    return f'AS "{alias}"'

                result = re.sub(pattern1, replace_alias, sql_text)

                # 第二轮：处理可能残留的 AS 别名（更宽松的匹配）
                # 匹配 AS 后跟连续的非空白字符（包含中文）
                pattern2 = r'(?i)\bAS\s+([\u4e00-\u9fff][^\s,;)]*)'

                def replace_alias2(m):
                    alias = m.group(1).strip()
                    # 如果已经加引号了，跳过
                    if (alias.startswith('"') and alias.endswith('"')) or \
                       (alias.startswith('`') and alias.endswith('`')):
                        return m.group(0)
                    # 如果包含中文，加引号
                    if re.search(r'[\u4e00-\u9fff]', alias):
                        return f'AS "{alias}"'
                    return m.group(0)

                result = re.sub(pattern2, replace_alias2, result)
                return result

            fixed_sql = fix_clickhouse_alias(fixed_sql)

            if fixed_sql != sql:
                _async_log_util.info(f"[SQL自动修复][规则修复] ClickHouse中文别名加引号 | 原SQL前200字符: {sql[:200]}")

        if fixed_sql == sql:
            return None

        repaired = {
            "success": True,
            "sql": fixed_sql,
            "tables": tables if isinstance(tables, list) else [],
            "chart-type": "table"
        }
        out = orjson.dumps(repaired).decode()
        _async_log_util.info(f"[SQL自动修复][规则兜底输出] {out[:1500]}")
        return out

    def _validate_and_autofix_sql_answer(
            self,
            raw_answer: str,
            max_retries: int = 1
    ) -> tuple[str, int]:
        """
        在 check_save_sql 之前执行：
        - 校验 answer 可解析且 SQL 无明显语法错误
        - 失败则自动修复并重试
        返回：最终可用 answer 文本、修复次数
        """
        attempts = max(0, int(max_retries))
        current = raw_answer
        fixed_times = 0
        last_error = ""
        _async_log_util.info(
            f"[SQL自动修复] 进入校验链路 | max_retries={attempts} | raw_answer_preview={str(raw_answer)[:1000]}"
        )

        for idx in range(attempts + 1):
            try:
                sql, _ = self.check_sql(current)
                _async_log_util.info(
                    f"[SQL自动修复][第{idx + 1}次] 解析SQL成功 | sql_preview={sql[:1200]}"
                )
                syntax_error = self._basic_sql_sanity_error(sql)
                if syntax_error:
                    raise SingleMessageError(syntax_error)
                db_syntax_error = self._db_syntax_error(sql)
                if db_syntax_error:
                    raise SingleMessageError(f"Database parser error: {db_syntax_error}")
                _async_log_util.info(
                    f"[SQL自动修复][第{idx + 1}次] 校验通过 | fixed_times={fixed_times}"
                )
                return current, fixed_times
            except Exception as e:
                last_error = str(e)
                _async_log_util.warning(
                    f"[SQL自动修复][第{idx + 1}次] 校验失败 | error={last_error[:500]}"
                )
                if idx >= attempts:
                    break
                repaired = self._repair_sql_answer_once(current, last_error)
                if repaired.strip() == (current or "").strip():
                    # LLM 修复无变化时，触发规则化兜底，避免无效重试循环
                    deterministic = self._deterministic_sql_fix(current, last_error)
                    if deterministic:
                        current = deterministic
                    else:
                        current = repaired
                else:
                    current = repaired
                fixed_times += 1
                _async_log_util.info(
                    f"[SQL自动修复][第{idx + 1}次] 已完成自动修复，准备重试"
                )

        raise SingleMessageError(f"SQL auto-fix failed: {last_error}")

    def init_messages(self):
        """初始化SQL生成消息，使用智能上下文管理"""
        self.sql_message = []

        # 多轮对话：追问场景做问题增强；单请求内只增强一次，已增强则直接复用
        if self.original_question is None:  # 只在第一次调用时保存原始问题
            self.original_question = self.chat_question.question
        original_question = self.original_question  # 使用保存的原始问题
        enhanced_question = self.chat_question.question
        if len(self.generate_sql_logs) > 0:
            if getattr(self, "_question_enhanced", False):
                enhanced_question = self.chat_question.question
            else:
                context_manager = ContextStateManager(self.session, self.current_user)
                rule_enhanced = context_manager.enhance_question_with_history(
                    self.chat_question.question,
                    self.generate_sql_logs
                )
                if rule_enhanced != self.chat_question.question:
                    enhanced_question = rule_enhanced
                else:
                    latest_log = self.generate_sql_logs[-1]
                    history_question = self._get_user_question_from_log(latest_log)
                    if history_question and history_question == (self.chat_question.question or '').strip() and len(self.generate_sql_logs) >= 2:
                        history_question = self._get_user_question_from_log(self.generate_sql_logs[-2])
                    llm_enhanced = self._enhance_question_by_llm(self.chat_question.question, history_question) if history_question else None
                    enhanced_question = llm_enhanced if llm_enhanced else self.chat_question.question

                if enhanced_question != self.chat_question.question:
                    _async_log_util.info(f"[问题增强] 原始问题: {self.chat_question.question}")
                    _async_log_util.info(f"[问题增强] 增强后问题: {enhanced_question}")
                    self.chat_question.question = enhanced_question
                    self._question_enhanced = True
        
        # add sys prompt
        self.sql_message.append(SystemMessage(content=self.chat_question.sql_sys_question()))
        
        # 使用上下文管理器进行智能上下文提取
        # 注意：使用原始问题进行上下文需求分析，以便正确检测时间追问等模式
        if len(self.generate_sql_logs) > 0:
            _async_log_util.info(f"[多轮对话] 开始智能上下文分析，历史日志数量: {len(self.generate_sql_logs)}")
            
            context_manager = ContextStateManager(self.session, self.current_user)
            # 使用原始问题检测时间追问等模式，但使用扩展后的问题进行上下文提取
            context_needs = context_manager.analyze_context_needs(
                original_question,  # 使用原始问题检测时间追问
                self.generate_sql_logs
            )
            
            if context_needs.needs_any_context():
                structured_context = context_manager.extract_context(
                    context_needs, 
                    self.generate_sql_logs,
                    current_question=enhanced_question
                )
                context_prompt = context_manager.build_context_prompt(
                    structured_context,
                    current_question=enhanced_question,
                    history_logs=self.generate_sql_logs
                )
                
                if context_prompt:
                    # 将上下文作为单独的消息添加到提示中
                    context_message = f"<context>\n{context_prompt}\n</context>"
                    self.sql_message.append(HumanMessage(content=context_message))
                    self._context_prompt_cache = context_prompt  # 供 init_straight_messages 复用，避免重复分析+仲裁
                    _async_log_util.info(f"[多轮对话] 已添加结构化上下文提示，长度: {len(context_prompt)} 字符")
                else:
                    _async_log_util.info(f"[多轮对话] 上下文提取完成，但无有效上下文内容")
            else:
                self._context_prompt_cache = ""  # 标记本请求已分析过、无需上下文，供快速模板分支跳过重复分析
                _async_log_util.info(f"[多轮对话] 当前问题无需上下文信息")
        else:
            _async_log_util.info(f"[多轮对话] 无历史SQL日志，这是第一轮对话")

        # 每个问题的图表是独立的，不累加历史图表消息，避免上下文爆炸
        self.chart_message = []
        # add sys prompt
        self.chart_message.append(SystemMessage(content=self.chat_question.chart_sys_question()))
        _async_log_util.info(f"[多轮对话] 图表消息仅保留当前轮 (不累加历史)")

    def init_straight_messages(self):
        """初始化快速模板匹配的消息列表，使用智能上下文管理"""
        self.straight_messages = []
        # 单请求内只增强一次，已增强则直接复用（避免重复调 LLM 与过度增强）
        enhanced_question = self.chat_question.question
        if len(self.generate_sql_logs) > 0:
            if getattr(self, "_question_enhanced", False):
                enhanced_question = self.chat_question.question
            else:
                context_manager = ContextStateManager(self.session, self.current_user)
                rule_enhanced = context_manager.enhance_question_with_history(
                    self.chat_question.question,
                    self.generate_sql_logs
                )
                if rule_enhanced != self.chat_question.question:
                    enhanced_question = rule_enhanced
                else:
                    latest_log = self.generate_sql_logs[-1]
                    history_question = self._get_user_question_from_log(latest_log)
                    if history_question and history_question == (self.chat_question.question or '').strip() and len(self.generate_sql_logs) >= 2:
                        history_question = self._get_user_question_from_log(self.generate_sql_logs[-2])
                    llm_enhanced = self._enhance_question_by_llm(self.chat_question.question, history_question) if history_question else None
                    enhanced_question = llm_enhanced if llm_enhanced else self.chat_question.question

                if enhanced_question != self.chat_question.question:
                    _async_log_util.info(f"[问题增强-快速模板] 原始问题: {self.chat_question.question}")
                    _async_log_util.info(f"[问题增强-快速模板] 增强后问题: {enhanced_question}")
                    self.chat_question.question = enhanced_question
                    self._question_enhanced = True
        
        # add straight prompt
        self.straight_messages.append(SystemMessage(content=self.chat_question.sql_straight_question()))
        
        # 使用上下文管理器进行智能上下文提取；同请求内若 init_messages 已算过则复用缓存，避免重复分析+仲裁者调用
        if len(self.generate_sql_logs) > 0:
            cached = getattr(self, "_context_prompt_cache", None)
            if cached is not None:
                if cached:
                    context_message = f"<context>\n{cached}\n</context>"
                    self.straight_messages.append(HumanMessage(content=context_message))
                    _async_log_util.info(f"[多轮对话-快速模板] 复用已缓存的上下文提示，长度: {len(cached)} 字符（避免重复分析）")
                else:
                    _async_log_util.info(f"[多轮对话-快速模板] 复用：当前问题无需上下文（避免重复分析）")
            else:
                _async_log_util.info(f"[多轮对话-快速模板] 开始智能上下文分析，历史日志数量: {len(self.generate_sql_logs)}")
                context_manager = ContextStateManager(self.session, self.current_user)
                original_question = self.original_question if self.original_question else enhanced_question
                context_needs = context_manager.analyze_context_needs(
                    original_question,
                    self.generate_sql_logs
                )
                if context_needs.needs_any_context():
                    structured_context = context_manager.extract_context(
                        context_needs,
                        self.generate_sql_logs,
                        current_question=enhanced_question
                    )
                    context_prompt = context_manager.build_context_prompt(
                        structured_context,
                        current_question=enhanced_question,
                        history_logs=self.generate_sql_logs
                    )
                    if context_prompt:
                        context_message = f"<context>\n{context_prompt}\n</context>"
                        self.straight_messages.append(HumanMessage(content=context_message))
                        _async_log_util.info(f"[多轮对话-快速模板] 已添加结构化上下文提示，长度: {len(context_prompt)} 字符")
                    else:
                        _async_log_util.info(f"[多轮对话-快速模板] 上下文提取完成，但无有效上下文内容")
                else:
                    _async_log_util.info(f"[多轮对话-快速模板] 当前问题无需上下文信息")
        else:
            _async_log_util.info(f"[多轮对话-快速模板] 无历史SQL日志，这是第一轮对话")

    def init_rewrite_messages(self):
        """初始化问题重写消息列表，复用上下文缓存避免重复分析。"""
        self.rewrite_messages = []
        self.rewrite_messages.append(SystemMessage(content=self.chat_question.sql_rewrite_question()))

        if len(self.generate_sql_logs) > 0:
            cached = getattr(self, "_context_prompt_cache", None)
            if cached:
                context_message = f"<context>\n{cached}\n</context>"
                self.rewrite_messages.append(HumanMessage(content=context_message))
                _async_log_util.info(f"[多轮对话-重写] 复用已缓存上下文，长度: {len(cached)} 字符")
            elif cached == "":
                _async_log_util.info("[多轮对话-重写] 复用：当前问题无需上下文")


    def init_record(self) -> ChatRecord:
        self.record = save_question(session=self.session, current_user=self.current_user, question=self.chat_question)
        self._trace_step(
            node_key="question_received",
            node_name="接收问题",
            input_payload={
                "question": self.chat_question.question,
                "chat_id": self.chat_question.chat_id,
                "datasource": self.chat_question.datasource if hasattr(self.chat_question, "datasource") else None,
            },
            output_payload={
                "record_id": self.record.id,
                "chat_id": self.record.chat_id,
            }
        )
        return self.record

    def get_record(self):
        return self.record

    def set_record(self, record: ChatRecord):
        self.record = record

    @staticmethod
    def _serialize_messages_for_trace(messages: List[Union[BaseMessage, dict[str, Any]]]) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, dict):
                serialized.append({
                    "type": msg.get("type"),
                    "content": msg.get("content"),
                })
                continue
            serialized.append({
                "type": getattr(msg, "type", msg.__class__.__name__),
                "content": getattr(msg, "content", ""),
            })
        return serialized

    def _trace_start(self, node_key: str, node_name: str, input_payload: Any = None,
                     extra_data: Any = None) -> Optional[ChatExecutionTrace]:
        if not getattr(self, "record", None) or not getattr(self.record, "id", None):
            return None
        try:
            return start_execution_trace(
                session=self.session,
                record_id=self.record.id,
                chat_id=self.record.chat_id,
                create_by=self.current_user.id,
                trace_group=self.trace_group,
                node_key=node_key,
                node_name=node_name,
                input_payload=input_payload,
                extra_data=extra_data,
            )
        except SQLAlchemyError as exc:
            try:
                self.session.rollback()
            except Exception:
                pass
            _async_log_util.warning(f"[执行轨迹] start trace 数据库异常，已降级忽略 {node_key}: {exc}")
            return None
        except Exception as exc:
            _async_log_util.debug(f"[执行轨迹] start trace 失败 {node_key}: {exc}")
            return None

    def _trace_end(self, trace: Optional[ChatExecutionTrace], status: str = ChatExecutionTraceStatus.SUCCESS,
                   output_payload: Any = None, error_message: str | None = None,
                   extra_data: Any = None) -> None:
        if trace is None:
            return
        try:
            end_execution_trace(
                session=self.session,
                trace=trace,
                status=status,
                output_payload=output_payload,
                error_message=error_message,
                extra_data=extra_data,
            )
        except SQLAlchemyError as exc:
            try:
                self.session.rollback()
            except Exception:
                pass
            _async_log_util.warning(f"[执行轨迹] end trace 数据库异常，已降级忽略 {getattr(trace, 'node_key', '')}: {exc}")
        except Exception as exc:
            _async_log_util.debug(f"[执行轨迹] end trace 失败 {getattr(trace, 'node_key', '')}: {exc}")

    def _trace_step(self, node_key: str, node_name: str, input_payload: Any = None, output_payload: Any = None,
                    status: str = ChatExecutionTraceStatus.SUCCESS, error_message: str | None = None,
                    extra_data: Any = None) -> None:
        trace = self._trace_start(node_key=node_key, node_name=node_name, input_payload=input_payload, extra_data=extra_data)
        self._trace_end(trace, status=status, output_payload=output_payload, error_message=error_message)

    def get_fields_from_chart(self):
        chart_info = get_chart_config(self.session, self.record.id, self.current_user)
        fields = []
        if chart_info.get('columns') and len(chart_info.get('columns')) > 0:
            for column in chart_info.get('columns'):
                column_str = column.get('value')
                if column.get('value') != column.get('name'):
                    column_str = column_str + '(' + column.get('name') + ')'
                fields.append(column_str)
        if chart_info.get('axis'):
            for _type in ['x', 'y', 'series']:
                if chart_info.get('axis').get(_type):
                    column = chart_info.get('axis').get(_type)
                    column_str = column.get('value')
                    if column.get('value') != column.get('name'):
                        column_str = column_str + '(' + column.get('name') + ')'
                    fields.append(column_str)
        return fields

    @staticmethod
    def generate_sql_template_from_data_training(self):

        analysis_msg: List[Union[BaseMessage, dict[str, Any]]] = []

        analysis_msg.append(SystemMessage(content=self.chat_question.generate_sql_template_question()))
        analysis_msg.append(HumanMessage(content=self.chat_question.analysis_user_question()))

        self.current_logs[OperationEnum.ANALYSIS] = start_log(session=self.session,
                                                              ai_modal_id=self.chat_question.ai_modal_id,
                                                              ai_modal_name=self.chat_question.ai_modal_name,
                                                              operate=OperationEnum.ANALYSIS,
                                                              record_id=self.record.id,
                                                              full_message=[
                                                                  {'type': msg.type,
                                                                   'content': msg.content} for
                                                                  msg
                                                                  in analysis_msg])
        full_thinking_text = ''
        full_analysis_text = ''
        token_usage = {}
        res = process_stream(self.llm.stream(analysis_msg), token_usage)
        reflection_enabled = bool(getattr(settings, "ANALYSIS_REFLECTION_ENABLED", True))
        for chunk in res:
            if chunk.get('content'):
                full_analysis_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')
            # 启用反思时：先流式输出初稿，避免前端长时间无内容（反思完成后会再下发最终稿覆盖）
            if reflection_enabled and (chunk.get('content') or chunk.get('reasoning_content')):
                yield {
                    "content": chunk.get('content') or "",
                    "reasoning_content": chunk.get('reasoning_content') or "",
                }

        # 反思与修正：基于真实数据对初稿做 Fact-check & Sanitization，避免幻觉/提示词泄露
        try:
            full_analysis_text = self._reflect_and_correct_analysis(
                draft=full_analysis_text,
                question=self.chat_question.question or "",
                sql=self.chat_question.sql or "",
                fields=self.chat_question.fields or "",
                data=self.chat_question.data or "[]",
                data_total_rows=self.chat_question.data_total_rows or "0",
                data_summary=self.chat_question.data_summary or "",
            )
        except Exception as e:
            _async_log_util.warning(f"[分析反思] 反思节点异常，已跳过: {e}")

        # 流式输出修正后的最终稿：按句/段分块 yield，前端可逐块追加展示
        for chunk in _stream_text_chunks(full_analysis_text, chunk_size=80):
            if chunk:
                yield {"content": chunk, "reasoning_content": ""}

        analysis_msg.append(AIMessage(full_analysis_text))

        self.current_logs[OperationEnum.ANALYSIS] = end_log(session=self.session,
                                                            log=self.current_logs[
                                                                OperationEnum.ANALYSIS],
                                                            full_message=[
                                                                {'type': msg.type,
                                                                  'content': msg.content}
                                                                for msg in analysis_msg],
                                                            reasoning_content=full_thinking_text,
                                                            token_usage=token_usage)
        self.record = save_analysis_answer(session=self.session, record_id=self.record.id,
                                           current_user=self.current_user,
                                           answer=orjson.dumps({'content': full_analysis_text}).decode())



    def generate_analysis(self):
        trace = self._trace_start(
            node_key="analysis_generation",
            node_name="分析生成",
            input_payload={
                "question": self.chat_question.question,
                "record_id": self.record.id if self.record else None,
            }
        )
        # 优先从图表配置获取字段（图表已在分析之前生成）
        try:
            fields = self.get_fields_from_chart()
            # 如果图表配置存在但字段为空，尝试从SQL结果获取
            if not fields:
                fields = self.get_fields_from_sql_result()
        except Exception:
            # 如果图表配置获取失败，从SQL执行结果获取字段
            fields = self.get_fields_from_sql_result()
        
        self.chat_question.fields = orjson.dumps(fields).decode()
        data = get_chat_chart_data(self.session, self.record.id, self.current_user)
        raw_data = data.get('data') if isinstance(data, dict) else None
        field_names = data.get('fields') if isinstance(data, dict) else None
        total_rows = len(raw_data) if isinstance(raw_data, list) else 0
        self.chat_question.data_total_rows = str(total_rows)

        # 表格数据智能预分析：对「分类+数值」结构预计算总数、分项明细，避免模型长表遗漏
        self.chat_question.data_summary = ""
        summary = None
        if raw_data and len(raw_data) > 0:
            try:
                chart_info = get_chart_config(self.session, self.record.id, self.current_user)
                summary = compute_table_summary(raw_data, field_names, chart_info)
                if summary.get("has_numeric_breakdown"):
                    self.chat_question.data_summary = format_summary_for_prompt(summary)
                    _async_log_util.info(
                        f"[数据分析] 已预计算统计量: row_count={summary.get('row_count')}, "
                        f"column_sums={summary.get('column_sums')}, breakdown_rows={len(summary.get('breakdown_rows', []))}"
                    )
            except Exception as e:
                _async_log_util.warning(f"[数据分析] 预分析失败，将不注入 data_summary: {e}", exc_info=True)

        # 当预分析成功时：传聚合结果 + 明细样本，兼顾数值准确与分析深度
        if summary and summary.get("has_numeric_breakdown") and summary.get("breakdown_rows"):
            breakdown = summary["breakdown_rows"]
            if breakdown and isinstance(breakdown[0], tuple):
                cat_key = summary.get("category_col") or "产业"
                compact_data = [{cat_key: name, "户数": cnt} for name, cnt in breakdown]
                category_col = summary.get("category_col")
                detail_sample_rows: List[Dict] = []
                detail_sample_text = ""
                if category_col and raw_data:
                    detail_sample_rows, detail_sample_text = build_detail_sample(
                        raw_data, category_col, breakdown, max_per_category=4, max_total=120
                    )
                self.chat_question.data_summary = format_summary_for_prompt(summary, detail_sample_text)
                data_payload: Dict[str, Any] = {"aggregates": compact_data}
                if detail_sample_rows:
                    data_payload["detail_sample"] = detail_sample_rows
                self.chat_question.data = orjson.dumps(data_payload).decode()
                total_val = summary.get("column_sums") or {}
                total_val = total_val.get("户数") or next(iter(total_val.values()), summary.get("row_count", total_rows))
                self.chat_question.data_total_rows = str(total_val)
                _async_log_util.info(
                    f"[数据分析] 已传聚合+明细样本: {len(compact_data)} 个分类, {len(detail_sample_rows)} 条样本, 总数={total_val}"
                )
            elif breakdown and isinstance(breakdown[0], dict):
                compact_data = breakdown
                if summary.get("is_detail_only"):
                    self.chat_question.data_summary = format_summary_for_prompt(summary)
                    self.chat_question.data = orjson.dumps(compact_data).decode()
                    self.chat_question.data_total_rows = str(len(raw_data) if raw_data else 0)
                    _async_log_util.info(f"[数据分析] 纯明细表模式: {len(compact_data)} 条样本, 总数={len(raw_data)}")
                else:
                    self.chat_question.data = orjson.dumps(compact_data).decode()
                    total_val = summary.get("column_sums") or {}
                    total_val = total_val.get("户数") or next(iter(total_val.values()), summary.get("row_count", total_rows))
                    self.chat_question.data_total_rows = str(total_val)
                    self.chat_question.data_summary = format_summary_for_prompt(summary)
                    _async_log_util.info(f"[数据分析] 已用预分析结果替换原始数据，共 {len(compact_data)} 个分类，总数={total_val}")
            else:
                compact_data = []
                self.chat_question.data = orjson.dumps(compact_data).decode()
                self.chat_question.data_summary = format_summary_for_prompt(summary)
                self.chat_question.data_total_rows = str(summary.get("row_count", total_rows))
        else:
            if raw_data and total_rows > 0 and (not summary or not summary.get("has_numeric_breakdown")):
                keys_preview = list((raw_data[0] or {}).keys())[:8] if raw_data else []
                _async_log_util.info(f"[数据分析] 预分析未命中，将传原始数据。行数={total_rows}, 列名={keys_preview}")
            ANALYSIS_DATA_ROW_LIMIT = int(getattr(settings, "ANALYSIS_DATA_ROW_LIMIT", 200) or 200)
            ANALYSIS_DATA_MAX_CHARS = 30000  # ~15K tokens, keep safe headroom below 65K context
            if total_rows > ANALYSIS_DATA_ROW_LIMIT:
                self.chat_question.data = orjson.dumps(raw_data[:ANALYSIS_DATA_ROW_LIMIT]).decode()
                _async_log_util.info(f"[数据分析] 数据共 {total_rows} 行，仅传前 {ANALYSIS_DATA_ROW_LIMIT} 行供分析")
            else:
                self.chat_question.data = orjson.dumps(raw_data).decode() if raw_data is not None else "[]"
            # Belt-and-suspenders: if serialized data still too large, cap by char count
            if len(self.chat_question.data or "") > ANALYSIS_DATA_MAX_CHARS:
                truncated_rows = min(len(raw_data) if raw_data else 0, 50)
                self.chat_question.data = orjson.dumps(raw_data[:truncated_rows]).decode() if raw_data else "[]"
                _async_log_util.warning(
                    f"[数据分析] 数据序列化后仍超 {ANALYSIS_DATA_MAX_CHARS} 字符，硬截断至前 {truncated_rows} 行 "
                    f"(原始 {total_rows} 行)"
                )
        
        # 传递SQL信息给数据分析模块，用于正确识别时间范围
        if self.record and self.record.sql:
            self.chat_question.sql = self.record.sql
        
        analysis_msg: List[Union[BaseMessage, dict[str, Any]]] = []

        ds_id = self.ds.id if isinstance(self.ds, CoreDatasource) else None
        self.chat_question.terminologies = get_terminology_template(self.session, self.chat_question.question,
                                                                    self.current_user.oid, ds_id)
        
        # 获取自定义提示词
        custom_prompt_parts = []
        if SQLBotLicenseUtil.valid() and find_custom_prompts is not None:
            custom_prompt_from_db = find_custom_prompts(self.session, CustomPromptTypeEnum.ANALYSIS,
                                                       self.current_user.oid, ds_id)
            if custom_prompt_from_db:
                custom_prompt_parts.append(custom_prompt_from_db)
        
        if getattr(self, "_cqs_snapshot_time_notice", None):
            custom_prompt_parts.append(self._cqs_snapshot_time_notice)
            _async_log_util.info("[数据分析] 已注入法人户数时点修正说明")

        # 合并所有 custom_prompt 部分
        self.chat_question.custom_prompt = "\n\n".join(custom_prompt_parts) if custom_prompt_parts else ""
        _async_log_util.info(
            f"[数据分析] 输入规模: question={len(self.chat_question.question or '')}, "
            f"fields={len(self.chat_question.fields or '')}, data={len(self.chat_question.data or '')}, "
            f"summary={len(self.chat_question.data_summary or '')}, sql={len(self.chat_question.sql or '')}, "
            f"rows={self.chat_question.data_total_rows or '0'}"
        )

        analysis_msg.append(SystemMessage(content=self.chat_question.analysis_sys_question()))
        analysis_msg.append(HumanMessage(content=self.chat_question.analysis_user_question()))

        self.current_logs[OperationEnum.ANALYSIS] = start_log(session=self.session,
                                                              ai_modal_id=self.chat_question.ai_modal_id,
                                                              ai_modal_name=self.chat_question.ai_modal_name,
                                                              operate=OperationEnum.ANALYSIS,
                                                              record_id=self.record.id,
                                                              full_message=[
                                                                  {'type': msg.type,
                                                                   'content': msg.content} for
                                                                  msg
                                                                  in analysis_msg])
        # Token guard: prevent 400 error from context overflow
        model_max_tokens = getattr(settings, "MODEL_MAX_TOKENS", 65536)
        estimated = _estimate_tokens(analysis_msg)
        safety_limit = int(model_max_tokens * 0.8)
        if estimated > safety_limit:
            err_msg = f"分析上下文长度超出限制：当前输入约 {estimated} tokens（模型上限 {model_max_tokens}，安全阈值 {safety_limit}）。建议：减少查询数据量或开启新对话。"
            _async_log_util.warning(f"[Token守卫-分析] {err_msg}")
            raise SingleMessageError(err_msg)

        full_thinking_text = ''
        full_analysis_text = ''
        token_usage = {}
        res = process_stream(self.llm.stream(analysis_msg), token_usage)
        yield {"type": "analysis-keepalive", "content": "", "reasoning_content": "", "stage": "draft_start"}
        draft_chunk_count = 0
        draft_started_at = datetime.now()
        for chunk in res:
            draft_chunk_count += 1
            if chunk.get('content'):
                full_analysis_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')
            # 初稿实时透传，避免前端长时间无内容导致中间层空闲断流
            if chunk.get('content') or chunk.get('reasoning_content'):
                yield {
                    "type": "analysis-result",
                    "content": chunk.get('content') or "",
                    "reasoning_content": chunk.get('reasoning_content') or "",
                    "phase": "draft",
                }
            # 保活：即使前端不展示该事件，也能持续收到流数据，避免网关空闲超时断流
            if draft_chunk_count % 20 == 0:
                yield {"type": "analysis-keepalive", "content": "", "reasoning_content": "", "stage": "draft_stream"}
        _async_log_util.info(
            f"[数据分析] 初稿生成完成: chars={len(full_analysis_text)}, chunks={draft_chunk_count}, "
            f"elapsed_ms={(datetime.now() - draft_started_at).total_seconds() * 1000:.0f}"
        )

        # 反思与修正：基于真实数据对初稿做 Fact-check & Sanitization，避免幻觉/提示词泄露
        reflection_enabled = bool(getattr(settings, "ANALYSIS_REFLECTION_ENABLED", True))
        reflection_timeout = float(getattr(settings, "ANALYSIS_REFLECTION_TIMEOUT", 25) or 25)
        if reflection_enabled and full_analysis_text.strip():
            reflection_started_at = datetime.now()
            reflection_input_draft = full_analysis_text
            reflection_future = executor.submit(
                self._reflect_and_correct_analysis,
                full_analysis_text,
                self.chat_question.question or "",
                self.chat_question.sql or "",
                self.chat_question.fields or "",
                self.chat_question.data or "[]",
                self.chat_question.data_total_rows or "0",
                self.chat_question.data_summary or "",
            )
            reflection_done = False
            waited = 0.0
            heartbeat_interval = 2.0
            while waited < reflection_timeout:
                try:
                    reflected = reflection_future.result(timeout=heartbeat_interval)
                    full_analysis_text = reflected
                    reflection_done = True
                    break
                except concurrent.futures.TimeoutError:
                    waited += heartbeat_interval
                    yield {
                        "type": "analysis-keepalive",
                        "content": "",
                        "reasoning_content": "",
                        "stage": "reflection_wait",
                        "elapsed_s": int(waited),
                    }
                except Exception as e:
                    _async_log_util.warning(f"[分析反思] 反思节点异常，已跳过: {e}")
                    reflection_done = True
                    break

            if not reflection_done:
                _async_log_util.warning(
                    f"[分析反思] 反思超时，已降级使用初稿: timeout={reflection_timeout}s"
                )
                full_analysis_text = reflection_input_draft
            _async_log_util.info(
                f"[分析反思] 结束: enabled={reflection_enabled}, changed={full_analysis_text != reflection_input_draft}, "
                f"elapsed_ms={(datetime.now() - reflection_started_at).total_seconds() * 1000:.0f}"
            )
        else:
            _async_log_util.info("[分析反思] 已跳过: 开关关闭或初稿为空")

        # 反思完成后发送最终稿覆盖事件，前端应以该稿替换初稿，避免重复追加
        yield {
            "type": "analysis-replace",
            "content": full_analysis_text,
            "reasoning_content": "",
            "phase": "final",
        }

        analysis_msg.append(AIMessage(full_analysis_text))

        self.current_logs[OperationEnum.ANALYSIS] = end_log(session=self.session,
                                                            log=self.current_logs[
                                                                OperationEnum.ANALYSIS],
                                                            full_message=[
                                                                {'type': msg.type,
                                                                 'content': msg.content}
                                                                for msg in analysis_msg],
                                                            reasoning_content=full_thinking_text,
                                                            token_usage=token_usage)
        self.record = save_analysis_answer(session=self.session, record_id=self.record.id,
                                           current_user=self.current_user,
                                           answer=orjson.dumps({'content': full_analysis_text}).decode())
        self._trace_end(
            trace,
            output_payload={
                "analysis_text": full_analysis_text,
                "reasoning_content": full_thinking_text,
                "token_usage": token_usage,
                "fields": fields,
                "data_total_rows": self.chat_question.data_total_rows,
            }
        )

    def generate_predict(self):
        trace = self._trace_start(
            node_key="predict_generation",
            node_name="预测生成",
            input_payload={
                "question": self.chat_question.question,
                "record_id": self.record.id if self.record else None,
            }
        )
        fields = self.get_fields_from_chart()
        self.chat_question.fields = orjson.dumps(fields).decode()
        data = get_chat_chart_data(self.session, self.record.id, self.current_user)
        self.chat_question.data = orjson.dumps(data.get('data')).decode()

        if SQLBotLicenseUtil.valid() and find_custom_prompts is not None:
            ds_id = self.ds.id if isinstance(self.ds, CoreDatasource) else None
            self.chat_question.custom_prompt = find_custom_prompts(self.session, CustomPromptTypeEnum.PREDICT_DATA,
                                                               self.current_user.oid, ds_id)

        predict_msg: List[Union[BaseMessage, dict[str, Any]]] = []
        predict_msg.append(SystemMessage(content=self.chat_question.predict_sys_question()))
        predict_msg.append(HumanMessage(content=self.chat_question.predict_user_question()))

        self.current_logs[OperationEnum.PREDICT_DATA] = start_log(session=self.session,
                                                                  ai_modal_id=self.chat_question.ai_modal_id,
                                                                  ai_modal_name=self.chat_question.ai_modal_name,
                                                                  operate=OperationEnum.PREDICT_DATA,
                                                                  record_id=self.record.id,
                                                                  full_message=[
                                                                      {'type': msg.type,
                                                                       'content': msg.content} for
                                                                      msg
                                                                      in predict_msg])
        full_thinking_text = ''
        full_predict_text = ''
        token_usage = {}
        res = process_stream(self.llm.stream(predict_msg), token_usage)
        for chunk in res:
            if chunk.get('content'):
                full_predict_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')
            yield chunk

        predict_msg.append(AIMessage(full_predict_text))
        self.record = save_predict_answer(session=self.session, record_id=self.record.id,
                                          answer=orjson.dumps({'content': full_predict_text}).decode(),
                                          current_user=self.current_user)
        self.current_logs[OperationEnum.PREDICT_DATA] = end_log(session=self.session,
                                                                log=self.current_logs[
                                                                    OperationEnum.PREDICT_DATA],
                                                                full_message=[
                                                                    {'type': msg.type,
                                                                     'content': msg.content}
                                                                    for msg in predict_msg],
                                                                reasoning_content=full_thinking_text,
                                                                token_usage=token_usage)
        self._trace_end(
            trace,
            output_payload={
                "predict_text": full_predict_text,
                "reasoning_content": full_thinking_text,
                "token_usage": token_usage,
                "fields": fields,
            }
        )

    @staticmethod
    def _parse_recommended_questions(content: str) -> List[str]:
        if not content or not content.strip():
            return []
        try:
            json_str = extract_nested_json(content)
            if not json_str:
                return []
            parsed = orjson.loads(json_str)
            if not isinstance(parsed, list):
                return []
            return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            return []

    @staticmethod
    def _extract_sql_text(raw_text: str) -> str:
        if not raw_text:
            return ""
        text = raw_text.strip()
        # 兼容 ```sql ... ``` 代码块
        if "```" in text:
            blocks = re.findall(r"```(?:sql)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
            if blocks:
                text = blocks[0].strip()
        # 兼容 JSON {"sql":"..."}
        try:
            maybe_obj = orjson.loads(text)
            if isinstance(maybe_obj, dict) and maybe_obj.get("sql"):
                text = str(maybe_obj.get("sql")).strip()
        except Exception:
            pass
        return text

    def _reflect_and_correct_analysis(
        self,
        draft: str,
        question: str,
        sql: str,
        fields: str,
        data: str,
        data_total_rows: str,
        data_summary: str,
    ) -> str:
        """对分析初稿做事实核查、清洗与一致性修正（避免数据幻觉与提示词泄露）。"""
        if not getattr(settings, "ANALYSIS_REFLECTION_ENABLED", True):
            return draft
        if not draft or not draft.strip():
            return draft

        system_prompt = (
            "你是一个“反思与修正代理（Reflection & Correction Agent）”。"
            "你不会重新生成SQL，你只对【分析初稿】做审查与修正，输出给用户的最终报告。\n"
            "【必须遵守】\n"
            "1) 事实核查：初稿中的每个数字/占比/总计/排名结论，都必须能从<data-summary>/<data>中找到依据或严格计算得出；"
            "   若缺少分母（如集团总额/全量分布），严禁输出任何百分比/占比/集中度结论。\n"
            "2) 逻辑一致：若数据只有1行，不要强行做“排名/分布/集中度”结论；若仅TopN/前N列表且无全量分布/总额，不要推断“高度集中”，也不要用“九成/大部分/几乎全部”等变体暗示比例。\n"
            "3) 内容清洗：删除一切内部推理与规则说明文本，例如「若用户问…」「根据规则…」「系统判断…」「提示：…」。\n"
            "4) 只输出最终报告正文（简体中文），不要输出检查清单、不要解释修正过程。\n"
            "5) **补全表格中的占比**：若<data>或<fields>中已包含占比/比例列（如“占净…”“…比例”或数值带%），而初稿只写了绝对值未写占比，修正稿必须将表中对应比例写入正文，格式为「XXX亿元（占…YY%）」，不得遗漏。例如表格有单一主体占净50.84%、合并范围占净21.33%时，正文须出现「约691.28亿元（占单体净资产50.84%）」「约793.62亿元（占合并净资产21.33%）」等表述。\n"
        )

        user_prompt = (
            f"<user-question>\n{question}\n</user-question>\n"
            f"<sql>\n{sql}\n</sql>\n"
            f"<fields>\n{fields}\n</fields>\n"
            f"<data-total-rows>{data_total_rows}</data-total-rows>\n"
            f"<data-summary>\n{data_summary}\n</data-summary>\n"
            f"<data>\n{data}\n</data>\n"
            f"<draft>\n{draft}\n</draft>\n"
            "请输出修正后的最终分析报告正文："
        )

        try:
            resp = self.llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
            text = self._extract_message_text(resp)
            if text:
                # 兜底：仅在“数据未提供占比/分母”时，改写占比句子为“无法计算占比”，避免误杀正常占比问题
                cleaned = self._sanitize_percent_claims(
                    text,
                    question=question,
                    sql=sql,
                    fields=fields,
                    data=data,
                    data_summary=data_summary,
                )
                if cleaned != text:
                    _async_log_util.info("[分析反思] 已处理占比/集中度表述（无分母/无ratio则改写）")
                _async_log_util.info("[分析反思] 已生成修正稿")
                return cleaned
        except Exception as e:
            _async_log_util.warning(f"[分析反思] 调用失败，已跳过: {e}")
        return draft

    @staticmethod
    def _sanitize_percent_claims(
        text: str,
        question: str = "",
        sql: str = "",
        fields: str = "",
        data: str = "",
        data_summary: str = "",
    ) -> str:
        """当数据未提供“占比/分母/ratio”时，仅删除无依据的占比句；若表中已有占比列或数值含%，则保留所有占比表述。"""
        if not text:
            return text

        # 1) 放宽判定：字段名含“占/比例/占比/ratio/percent”或数据中已出现数字+% 即视为有占比数据，不删
        hay = f"{fields}\n{data_summary}\n{data}"
        has_ratio_col = bool(
            re.search(r"(ratio|percent|percentage|share|占比|比例|贡献率|集中度|占净|占\s*[^\s，。；]*比)", hay, flags=re.IGNORECASE)
        ) or bool(re.search(r"\d+(?:\.\d+)?\s*%", data or ""))

        # 2) 若问题本身明确在问占比/比例，且数据也提供了 ratio 字段，则不处理（避免误杀）
        q = (question or "").strip()
        user_asks_ratio = bool(re.search(r"(占比|比例|百分比|贡献度|集中度)", q))
        if user_asks_ratio and has_ratio_col:
            return text

        # 3) 若数据没有 ratio/分母字段，但文本里出现百分号或“九成/几乎全部”等比例暗示，则逐句改写
        if has_ratio_col:
            # 有 ratio 列但用户没问，占比表述一般也可保留（由反思模型自行控制）
            return text

        # 仅删除无数据支撑的占比/比例句，不插入免责声明，避免与上文“与全年总额一致”等表述矛盾
        def _drop_sentence(m: re.Match) -> str:
            sent = m.group(0)
            if not sent.strip():
                return sent
            if re.search(r"(\d+(?:\.\d+)?\s*%|占[^。；;\n]*\d+|九成|八成|七成|六成|五成|大部分|几乎全部|接近全部)", sent):
                return ""  # 直接删除该句，不追加“无法计算占比”等废话
            return sent

        cleaned = re.sub(r"[^。；;\n]*[。；;\n]", _drop_sentence, text)
        cleaned = re.sub(r"[^。；;\n]*$", _drop_sentence, cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned or text

    def _validate_recommend_question_sql(self, question: str) -> bool:
        """
        轻量预验证：为推荐问题生成一条只读 SQL 并尝试执行。
        仅当配置 GUESS_SQL_VALIDATE_ENABLED=True 时生效。
        """
        if not self.ds:
            return False
        schema = self.chat_question.db_schema or ""
        engine = self.chat_question.engine or ""
        system_prompt = (
            "你是SQL生成器。只返回一条可执行的只读SQL，不要解释，不要Markdown，不要多条语句。"
            "如果无法生成，请只返回 EMPTY。"
        )
        user_prompt = (
            f"数据库引擎: {engine}\n"
            f"Schema:\n{schema}\n\n"
            f"问题: {question}\n"
            "要求：仅返回 SELECT/WITH 开头的 SQL。"
        )
        try:
            resp = self.llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
            resp_text = self._extract_message_text(resp)
            sql = self._extract_sql_text(resp_text)
            if not sql:
                return False
            sql_head = sql.lstrip().lower()
            if not (sql_head.startswith("select") or sql_head.startswith("with")):
                return False
            from apps.db.db import exec_sql
            result = exec_sql(self.ds, sql, True)
            if isinstance(result, dict) and result.get("error"):
                return False
            return True
        except Exception:
            return False

    def _apply_guess_sql_validation(self, full_guess_text: str) -> str:
        if not settings.GUESS_SQL_VALIDATE_ENABLED:
            return full_guess_text
        questions = self._parse_recommended_questions(full_guess_text)
        if not questions:
            return full_guess_text
        _async_log_util.info(f"[猜你想问-预验证] 原始推荐问题数量: {len(questions)}")
        validated: List[str] = []
        for q in questions:
            if self._validate_recommend_question_sql(q):
                validated.append(q)
            else:
                _async_log_util.info(f"[猜你想问-预验证] 丢弃不可执行问题: {q}")
        _async_log_util.info(f"[猜你想问-预验证] 通过验证数量: {len(validated)}")
        return orjson.dumps(validated).decode() if validated else "[]"

    def generate_recommend_questions_task(self):

        # get schema（猜你想问）：优先取「当前对话 SQL 用到的表」的表结构；无 SQL 时再回退检索
        if self.ds:
            if self.out_ds_instance:
                self.chat_question.db_schema = self.out_ds_instance.get_db_schema(self.ds.id)
            else:
                sql_used = (self.record.sql or "").strip()
                table_names = _parse_table_names_from_sql(sql_used) if sql_used else []
                _async_log_util.info(
                    f"[猜你想问] 当前记录 SQL 长度: {len(sql_used)}, 解析出表数量: {len(table_names)}"
                )
                if table_names:
                    self.chat_question.db_schema = get_table_schema_for_tables(
                        session=self.session,
                        current_user=self.current_user,
                        ds=self.ds,
                        table_names=table_names,
                    )
                    _async_log_util.info(f"[猜你想问] 表结构来源=SQL用表, 解析表: {table_names}")
                else:
                    # 无 SQL 或解析不到表（如首轮）时回退为检索
                    self.chat_question.db_schema = get_table_schema_for_guess(
                        session=self.session,
                        current_user=self.current_user,
                        ds=self.ds,
                        question=self.chat_question.question,
                    )
                    _async_log_util.info("[猜你想问] 表结构来源=检索(无SQL或未解析到表)")

        schema_input = self.chat_question.db_schema or ""
        _async_log_util.info(f"[猜你想问] 表结构(schema)输入长度: {len(schema_input)} 字符")
        _async_log_util.info(f"[猜你想问] 表结构(schema)内容:\n{schema_input}")

        guess_msg: List[Union[BaseMessage, dict[str, Any]]] = []
        guess_msg.append(SystemMessage(content=self.chat_question.guess_sys_question()))

        try:
            raw_old_questions = get_old_questions(self.session, self.record.datasource, self.current_user) or []
        except Exception as e:
            _async_log_util.error(f"[猜你想问] 获取以往提问失败，回退为空列表: {e}", exc_info=True)
            raw_old_questions = []

        old_questions: List[str] = []
        for q in raw_old_questions:
            if q is None:
                continue
            try:
                q_str = str(q).strip()
                if q_str:
                    old_questions.append(q_str)
            except Exception:
                continue
        _async_log_util.info(f"[猜你想问] 以往提问(old_questions)数量: {len(old_questions)}")
        _async_log_util.info(f"[猜你想问] 以往提问(old_questions)内容: {old_questions}")

        guess_msg.append(
            HumanMessage(content=self.chat_question.guess_user_question(orjson.dumps(old_questions).decode())))

        self.current_logs[OperationEnum.GENERATE_RECOMMENDED_QUESTIONS] = start_log(session=self.session,
                                                                                    ai_modal_id=self.chat_question.ai_modal_id,
                                                                                    ai_modal_name=self.chat_question.ai_modal_name,
                                                                                    operate=OperationEnum.GENERATE_RECOMMENDED_QUESTIONS,
                                                                                    record_id=self.record.id,
                                                                                    full_message=[
                                                                                        {'type': msg.type,
                                                                                         'content': msg.content} for
                                                                                        msg
                                                                                        in guess_msg])
        full_thinking_text = ''
        full_guess_text = ''
        token_usage = {}
        res = process_stream(self.llm.stream(guess_msg), token_usage)
        for chunk in res:
            if chunk.get('content'):
                full_guess_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')
            yield chunk

        # 可选高级优化：推荐问题 SQL 预执行验证（默认关闭）
        full_guess_text = self._apply_guess_sql_validation(full_guess_text)

        guess_msg.append(AIMessage(full_guess_text))

        self.current_logs[OperationEnum.GENERATE_RECOMMENDED_QUESTIONS] = end_log(session=self.session,
                                                                                  log=self.current_logs[
                                                                                      OperationEnum.GENERATE_RECOMMENDED_QUESTIONS],
                                                                                  full_message=[
                                                                                      {'type': msg.type,
                                                                                       'content': msg.content}
                                                                                      for msg in guess_msg],
                                                                                  reasoning_content=full_thinking_text,
                                                                                  token_usage=token_usage)
        self.record = save_recommend_question_answer(session=self.session, record_id=self.record.id,
                                                     current_user=self.current_user,
                                                     answer={'content': full_guess_text})

        yield {'recommended_question': self.record.recommended_question}

    def select_datasource(self):
        datasource_msg: List[Union[BaseMessage, dict[str, Any]]] = []
        datasource_msg.append(SystemMessage(self.chat_question.datasource_sys_question()))
        if self.current_assistant and self.current_assistant.type != 4:
            _ds_list = get_assistant_ds(session=self.session, llm_service=self)
        else:
            stmt = select(CoreDatasource.id, CoreDatasource.name, CoreDatasource.description).where(
                and_(CoreDatasource.oid == self.current_user.oid))
            _ds_list = [
                {
                    "id": ds.id,
                    "name": ds.name,
                    "description": ds.description
                }
                for ds in self.session.exec(stmt)
            ]
            """ _ds_list = self.session.exec(select(CoreDatasource).options(
                load_only(CoreDatasource.id, CoreDatasource.name, CoreDatasource.description))).all() """
        if not _ds_list:
            raise SingleMessageError('No available datasource configuration found')
        ignore_auto_select = _ds_list and len(_ds_list) == 1
        # ignore auto select ds

        full_thinking_text = ''
        full_text = ''
        trace = self._trace_start(
            node_key="datasource_selection",
            node_name="数据源选择",
            input_payload={
                "question": self.chat_question.question,
                "candidate_count": len(_ds_list),
                "candidate_ids": [item.get("id") for item in _ds_list if isinstance(item, dict)],
            }
        )
        if not ignore_auto_select:
            if settings.TABLE_EMBEDDING_ENABLED:
                ds = get_ds_embedding(self.session, self.current_user, _ds_list, self.out_ds_instance,
                                      self.chat_question.question, self.current_assistant)
                yield {'content': '{"id":' + str(ds.get('id')) + '}'}
            else:
                _ds_list_dict = []
                for _ds in _ds_list:
                    _ds_list_dict.append(_ds)
                datasource_msg.append(
                    HumanMessage(self.chat_question.datasource_user_question(orjson.dumps(_ds_list_dict).decode())))

                self.current_logs[OperationEnum.CHOOSE_DATASOURCE] = start_log(session=self.session,
                                                                               ai_modal_id=self.chat_question.ai_modal_id,
                                                                               ai_modal_name=self.chat_question.ai_modal_name,
                                                                               operate=OperationEnum.CHOOSE_DATASOURCE,
                                                                               record_id=self.record.id,
                                                                               full_message=[{'type': msg.type,
                                                                                              'content': msg.content}
                                                                                             for
                                                                                             msg in datasource_msg])

                token_usage = {}
                res = process_stream(self.llm.stream(datasource_msg), token_usage)
                for chunk in res:
                    if chunk.get('content'):
                        full_text += chunk.get('content')
                    if chunk.get('reasoning_content'):
                        full_thinking_text += chunk.get('reasoning_content')
                    yield chunk
                datasource_msg.append(AIMessage(full_text))

                self.current_logs[OperationEnum.CHOOSE_DATASOURCE] = end_log(session=self.session,
                                                                             log=self.current_logs[
                                                                                 OperationEnum.CHOOSE_DATASOURCE],
                                                                             full_message=[
                                                                                 {'type': msg.type,
                                                                                  'content': msg.content}
                                                                                 for msg in datasource_msg],
                                                                             reasoning_content=full_thinking_text,
                                                                             token_usage=token_usage)

                json_str = extract_nested_json(full_text)
                if json_str is None:
                    raise SingleMessageError(f'Cannot parse datasource from answer: {full_text}')
                ds = orjson.loads(json_str)

        _error: Exception | None = None
        _datasource: int | None = None
        _engine_type: str | None = None
        try:
            data: dict = _ds_list[0] if ignore_auto_select else ds

            if data.get('id') and data.get('id') != 0:
                _datasource = data['id']
                _chat = self.session.get(Chat, self.record.chat_id)
                _chat.datasource = _datasource
                if self.current_assistant and self.current_assistant.type in dynamic_ds_types:
                    _ds = self.out_ds_instance.get_ds(data['id'])
                    self.ds = _ds
                    self.chat_question.engine = _ds.type + get_version(self.ds)
                    self.chat_question.db_schema = self.out_ds_instance.get_db_schema(self.ds.id)
                    _engine_type = self.chat_question.engine
                    _chat.engine_type = _ds.type
                else:
                    _ds = self.session.get(CoreDatasource, _datasource)
                    if not _ds:
                        _datasource = None
                        raise SingleMessageError(f"Datasource configuration with id {_datasource} not found")
                    self.ds = CoreDatasource(**_ds.model_dump())
                    self.chat_question.engine = (_ds.type_name if _ds.type != 'excel' else 'PostgreSQL') + get_version(
                        self.ds)
                    self.chat_question.db_schema = self._resolve_db_schema_for_sql(
                        question=self.chat_question.question or "",
                        embedding=True
                    )
                    _engine_type = self.chat_question.engine
                    _chat.engine_type = _ds.type_name
                # save chat
                with self.session.begin_nested():
                    # 为了能继续记日志，先单独处理下事务
                    try:
                        self.session.add(_chat)
                        self.session.flush()
                        self.session.refresh(_chat)
                        self.session.commit()
                    except Exception as e:
                        self.session.rollback()
                        raise e

            elif data['fail']:
                raise SingleMessageError(data['fail'])
            else:
                raise SingleMessageError('No available datasource configuration found')

        except Exception as e:
            _error = e

        if not ignore_auto_select and not settings.TABLE_EMBEDDING_ENABLED:
            self.record = save_select_datasource_answer(session=self.session, record_id=self.record.id,
                                                        current_user=self.current_user,
                                                        answer=orjson.dumps({'content': full_text}).decode(),
                                                        datasource=_datasource,
                                                        engine_type=_engine_type)
        if self.ds:
            oid = self.ds.oid if isinstance(self.ds, CoreDatasource) else 1
            ds_id = self.ds.id if isinstance(self.ds, CoreDatasource) else None

            self.chat_question.terminologies = get_terminology_template(self.session, self.chat_question.question, oid,
                                                                        ds_id)
            self.chat_question.data_training = get_training_template(self.session, self.chat_question.question, ds_id,
                                                                     oid)
            if SQLBotLicenseUtil.valid() and find_custom_prompts is not None:
                self.chat_question.custom_prompt = find_custom_prompts(self.session, CustomPromptTypeEnum.GENERATE_SQL,
                                                                   oid, ds_id)

            self.init_messages()

        if _error:
            self._trace_end(
                trace,
                status=ChatExecutionTraceStatus.ERROR,
                error_message=str(_error),
                output_payload={"selected_datasource": _datasource, "engine_type": _engine_type},
            )
            raise _error
        self._trace_end(
            trace,
            output_payload={
                "selected_datasource": _datasource,
                "engine_type": _engine_type,
                "response_text": full_text,
                "reasoning_content": full_thinking_text,
            }
        )

    def generate_rewrite_question(self) -> tuple[str, str]:
        """执行独立问题重写节点，返回 (重写后问题, 原始模型输出)。"""
        rewrite_messages = list(self.rewrite_messages)
        rewrite_messages.append(HumanMessage(
            self.chat_question.sql_user_question(current_time=self._get_effective_current_time())
        ))
        trace = self._trace_start(
            node_key="question_rewrite",
            node_name="问题重写",
            input_payload={
                "messages": self._serialize_messages_for_trace(rewrite_messages),
            }
        )

        token_usage = {}
        res = process_stream(
            self._stream_with_response_format(
                rewrite_messages,
                self._rewrite_response_format(),
                "问题重写结构化输出"
            ),
            token_usage
        )
        full_text = ""
        full_reasoning_text = ""
        for chunk in res:
            if chunk.get("content"):
                full_text += chunk.get("content")
            if chunk.get("reasoning_content"):
                full_reasoning_text += chunk.get("reasoning_content")

        rewritten_question = ""
        try:
            rewrite_json = extract_nested_json(full_text) or full_text
            rewrite_data = orjson.loads(rewrite_json)
            if isinstance(rewrite_data, dict) and isinstance(rewrite_data.get("question_rewrite"), str):
                rewritten_question = rewrite_data.get("question_rewrite").strip()
        except Exception:
            pass

        if not rewritten_question:
            # 回退解析：兼容网关偶发未按 json_schema 输出的场景
            match = re.search(r'"question_rewrite"\s*:\s*"((?:[^"\\]|\\.)*)"', full_text, re.DOTALL)
            if match:
                try:
                    rewritten_question = orjson.loads(f'"{match.group(1)}"').strip()
                except Exception:
                    rewritten_question = match.group(1).strip()

        if not rewritten_question:
            self._trace_end(
                trace,
                status=ChatExecutionTraceStatus.ERROR,
                output_payload={
                    "response_text": full_text,
                    "reasoning_content": full_reasoning_text,
                    "token_usage": token_usage,
                },
                error_message="cannot parse question_rewrite"
            )
            raise SingleMessageError("Rewrite question failed: cannot parse question_rewrite")

        self.rewrite_messages = rewrite_messages + [AIMessage(content=full_text)]
        self._trace_end(
            trace,
            output_payload={
                "rewritten_question": rewritten_question,
                "response_text": full_text,
                "reasoning_content": full_reasoning_text,
                "token_usage": token_usage,
            }
        )
        return rewritten_question, full_text

    def generate_sql(self):
        # append current question（法人户数且未指定时间时 current_time 取自产权表压减=是的最新创建时间）
        self.sql_message.append(HumanMessage(
            self.chat_question.sql_user_question(current_time=self._get_effective_current_time())))
        trace = self._trace_start(
            node_key="sql_generation",
            node_name="SQL生成",
            input_payload={
                "messages": self._serialize_messages_for_trace(self.sql_message),
            }
        )

        self.current_logs[OperationEnum.GENERATE_SQL] = start_log(session=self.session,
                                                                  ai_modal_id=self.chat_question.ai_modal_id,
                                                                  ai_modal_name=self.chat_question.ai_modal_name,
                                                                  operate=OperationEnum.GENERATE_SQL,
                                                                  record_id=self.record.id,
                                                                  full_message=[
                                                                      {'type': msg.type, 'content': msg.content} for msg
                                                                      in self.sql_message])
        full_thinking_text = ''
        full_sql_text = ''
        token_usage = {}
        res = process_stream(
            self._stream_with_response_format(
                self.sql_message,
                self._sql_json_response_format(),
                "SQL结构化输出"
            ),
            token_usage
        )
        for chunk in res:
            if chunk.get('content'):
                full_sql_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')
            yield chunk

        self.sql_message.append(AIMessage(full_sql_text))

        self.current_logs[OperationEnum.GENERATE_SQL] = end_log(session=self.session,
                                                                log=self.current_logs[OperationEnum.GENERATE_SQL],
                                                                full_message=[{'type': msg.type, 'content': msg.content}
                                                                              for msg in self.sql_message],
                                                                reasoning_content=full_thinking_text,
                                                                token_usage=token_usage)
        self.record = save_sql_answer(session=self.session, record_id=self.record.id,
                                      answer=orjson.dumps({'content': full_sql_text}).decode(),
                                      current_user=self.current_user)
        self._trace_end(
            trace,
            output_payload={
                "response_text": full_sql_text,
                "reasoning_content": full_thinking_text,
                "token_usage": token_usage,
            }
        )


    def double_check_straight_sql_info(self, matched_id, infos, training_data):
        matched_data = None
        for i in training_data:
            if int(matched_id) == int(i["id"]):
                matched_data = i
        if not matched_data:
            return False
        template_id = matched_data["id"]
        template_question = matched_data["question"] or ""
        sql_template = matched_data["sql-template"]
        sql_info = matched_data["sql-info"]
        user_sql_info = json.dumps(infos)
        user_question = (self.chat_question.question or "").strip()
        trace = self._trace_start(
            node_key="quick_template_double_check",
            node_name="快速模板二次校验",
            input_payload={
                "matched_id": matched_id,
                "template_question": template_question,
                "user_question": user_question,
                "infos": infos,
            }
        )

        # 实体类型预检：地区 vs 公司/产业互斥，避免海南省误用集团各产业模板
        user_type = infer_entity_type(user_question)
        template_type = infer_entity_type(template_question)
        if user_type != template_type and user_type in (ENTITY_REGION, ENTITY_COMPANY_INDUSTRY) and template_type in (ENTITY_REGION, ENTITY_COMPANY_INDUSTRY):
            _async_log_util.info(
                f"[快速模板匹配] 二次校验预检不通过 - 模板ID: {template_id}, 模板问题: {template_question[:80]}, "
                f"实体类型冲突: 用户={user_type}, 模板={template_type}"
            )
            self._trace_end(
                trace,
                output_payload={"matched": False, "reason": "entity_type_conflict"},
            )
            return False

        if getattr(settings, "LEGACY_DOMAIN_RULES_ENABLED", False):
            # 范围限定词预检：模板含「海外/境外/境内/国内」而用户未提及，直接判不匹配
            _SCOPE_KEYWORDS = ("海外", "境外", "境内", "国内")
            for kw in _SCOPE_KEYWORDS:
                if kw in template_question and kw not in user_question:
                    _async_log_util.info(
                        f"[快速模板匹配] 二次校验预检不通过 - 模板ID: {template_id}, 模板问题: {template_question[:80]}, "
                        f"模板含「{kw}」而用户问题未提及，直接判不匹配"
                    )
                    self._trace_end(
                        trace,
                        output_payload={"matched": False, "reason": f"scope_keyword_missing:{kw}"},
                    )
                    return False

            # 统计口径预检：并表 vs 非并表 互斥（SQL 筛选条件不同，不能混用）
            _CONSOLIDATED_MARKERS = ("并表", "合并")
            user_has_consolidated = any(m in user_question for m in _CONSOLIDATED_MARKERS)
            template_has_consolidated = any(m in template_question for m in _CONSOLIDATED_MARKERS)
            if user_has_consolidated != template_has_consolidated:
                _async_log_util.info(
                    f"[快速模板匹配] 二次校验预检不通过 - 模板ID: {template_id}, 模板问题: {template_question[:80]}, "
                    f"并表口径不一致: 用户含并表={user_has_consolidated}, 模板含并表={template_has_consolidated}"
                )
                self._trace_end(
                    trace,
                    output_payload={"matched": False, "reason": "consolidated_mismatch"},
                )
                return False

            # 存续口径预检：模板含「存续」而用户未提、或用户含「存续」而模板未提，均不匹配（SQL 筛选不同）
            if ("存续" in template_question) != ("存续" in user_question):
                _async_log_util.info(
                    f"[快速模板匹配] 二次校验预检不通过 - 模板ID: {template_id}, 模板问题: {template_question[:80]}, "
                    f"存续口径不一致: 模板含存续={'存续' in template_question}, 用户含存续={'存续' in user_question}"
                )
                self._trace_end(
                    trace,
                    output_payload={"matched": False, "reason": "survival_scope_mismatch"},
                )
                return False

        # 明细 vs 汇总预检：用户要「明细/列表/导出明细」而模板问句无明细/列表/导出，不匹配（SQL 结构不同）
        _DETAIL_MARKERS = ("明细", "列表", "导出", "详情", "清单")
        user_wants_detail = any(m in user_question for m in _DETAIL_MARKERS)
        template_has_detail = any(m in template_question for m in _DETAIL_MARKERS)
        if user_wants_detail and not template_has_detail:
            _async_log_util.info(
                f"[快速模板匹配] 二次校验预检不通过 - 模板ID: {template_id}, 模板问题: {template_question[:80]}, "
                f"用户要明细/列表而模板为汇总: 用户含明细类词=True, 模板含=False"
            )
            self._trace_end(
                trace,
                output_payload={"matched": False, "reason": "detail_mismatch"},
            )
            return False

        double_check_messages = [
            SystemMessage(
                self.chat_question.double_check_question(template_id, template_question, sql_template, sql_info, user_sql_info)),
            HumanMessage(
                self.chat_question.sql_user_question(current_time=self._get_effective_current_time()))]

        # 二次校验时用于排查：若用户问题与模板问题一致仍判 False，可对比此日志（模板问题完整输出便于对比）
        _async_log_util.info(
            f"[快速模板匹配] 二次校验入参 - 模板ID: {template_id}, 模板问题: {template_question}, 当前用户问题: {(self.chat_question.question or '')[:120]}"
        )
        token_usage = {}
        res = process_stream(
            self._stream_with_response_format(
                double_check_messages,
                self._double_check_response_format(),
                "快速模板二次校验结构化输出"
            ),
            token_usage
        )

        content_list = []
        for chunk in res:
            if chunk.get('content'):
                content_list.append(chunk.get('content'))
        content_str = "".join(content_list)

        # 优先解析结构化输出：{"matched": true/false}
        try:
            dc_json_str = extract_nested_json(content_str)
            if dc_json_str:
                dc_data = orjson.loads(dc_json_str)
                if isinstance(dc_data, dict) and isinstance(dc_data.get("matched"), bool):
                    matched = dc_data.get("matched")
                    self._trace_end(
                        trace,
                        output_payload={
                            "matched": matched,
                            "response_text": content_str[:800],
                            "decision_mode": "json_schema"
                        },
                    )
                    return matched
        except Exception:
            pass

        if settings.LOG_LEVEL == "DEBUG":
            _async_log_util.info("=" * 20 + " generate_straight_sql_info " + "=" * 20 + "\n")
            _async_log_util.info("DEBUG" * 20)
            _async_log_util.info(f"double_check_messages: {double_check_messages}")
            _async_log_util.info(f"content_str: {content_str}")
            _async_log_util.info("=" * 20 + " generate_straight_sql_info " + "=" * 20 + "\n")
        # 解析二次校验结果：要求模型最后一行只返回 True 或 False
        lines = [line.strip() for line in content_str.split("\n") if line.strip()]
        # 收集所有单独一行的 true/false 及其出现顺序
        bool_lines = []
        for i, line in enumerate(lines):
            normalized = line.strip().rstrip(".").rstrip("。").lower()
            if normalized == "true":
                bool_lines.append((i, True))
            elif normalized == "false":
                bool_lines.append((i, False))

        if not bool_lines:
            _async_log_util.info(
                f"[快速模板匹配] 二次校验无法解析True/False - 模板ID: {template_id}, 模板问题: {template_question[:80]}, "
                f"可能模型未按「最后一行只返回True或False」输出，模型完整回复:\n{content_str[:800]}"
            )
            self._trace_end(
                trace,
                output_payload={"matched": False, "reason": "cannot_parse_boolean", "response_text": content_str[:800]},
            )
            return False

        # 若模型同时输出 True 和 False（格式混乱），根据推理内容判断：推理含「一致」「匹配」「可以使用」等则取 True
        if len(bool_lines) > 1:
            reasoning_text = " ".join(lines[: bool_lines[0][0]]) if bool_lines else ""
            if any(kw in reasoning_text for kw in ("一致", "匹配", "可以使用", "符合", "满足")):
                _async_log_util.info(
                    f"[快速模板匹配] 二次校验通过 - 模板ID: {template_id}, 模板问题: {template_question[:80]}, "
                    f"模型同时输出True/False，推理含匹配表述，取True"
                )
                self._trace_end(
                    trace,
                    output_payload={"matched": True, "response_text": content_str[:800], "decision_mode": "reasoning_positive"},
                )
                return True
            # 推理含否定表述则取 False
            if any(kw in reasoning_text for kw in ("不一致", "不匹配", "不符合", "不满足", "不能使用")):
                _async_log_util.info(
                    f"[快速模板匹配] 二次校验返回False - 模板ID: {template_id}, 模板问题: {template_question[:80]}, 模型推理含不匹配表述"
                )
                self._trace_end(
                    trace,
                    output_payload={"matched": False, "response_text": content_str[:800], "decision_mode": "reasoning_negative"},
                )
                return False
            # 无法从推理判断时，取最后一次出现（保持原逻辑）
            result = bool_lines[-1][1]
        else:
            result = bool_lines[0][1]

        if result:
            _async_log_util.info(f"[快速模板匹配] 二次校验通过 - 模板ID: {template_id}, 模板问题: {template_question[:80]}, 模型返回: True")
            self._trace_end(
                trace,
                output_payload={"matched": True, "response_text": content_str[:800]},
            )
            return True
        _async_log_util.info(
            f"[快速模板匹配] 二次校验返回False - 模板ID: {template_id}, 模板问题: {template_question[:80]}, 模型完整回复:\n{content_str[:500]}"
        )
        self._trace_end(
            trace,
            output_payload={"matched": False, "response_text": content_str[:800]},
        )
        return False


    def generate_straight_sql_info(self):
        self.straight_messages.append(HumanMessage(
            self.chat_question.sql_user_question(current_time=self._get_effective_current_time())))
        trace = self._trace_start(
            node_key="quick_template_match",
            node_name="快速模板匹配",
            input_payload={
                "messages": self._serialize_messages_for_trace(self.straight_messages),
            }
        )

        if settings.LOG_LEVEL == "DEBUG":
            _async_log_util.info("=" * 20 + " generate_straight_sql_info " + "=" * 20 + "\n")
            _async_log_util.info(f"datasource-result: {self.straight_messages}")
            _async_log_util.info("=" * 20 + " generate_straight_sql_info " + "=" * 20 + "\n")


        token_usage = {}
        res = process_stream(
            self._stream_with_response_format(
                self.straight_messages,
                self._sql_json_response_format(),
                "快速模板SQL结构化输出"
            ),
            token_usage
        )
        full_text = ""
        full_reasoning_text = ""
        for chunk in res:
            if chunk.get('content'):
                full_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_reasoning_text += chunk.get('reasoning_content')
            yield chunk
        # 将token_usage存储到实例变量中，以便外部访问
        self.straight_sql_token_usage = token_usage
        self._trace_end(
            trace,
            output_payload={
                "response_text": full_text,
                "reasoning_content": full_reasoning_text,
                "token_usage": token_usage,
            }
        )


    @staticmethod
    def generate_straight_sql(training_data, straight_dict_text, user_question: str = ""):
        import re
        import json  # 在函数开头导入 json，避免作用域问题
        if settings.LOG_LEVEL == "DEBUG":
            _async_log_util.info("="*20 +" generate_straight_sql " + "="*20 + "\n")
            # _async_log_util.info(f"training_data: {training_data}")
            # _async_log_util.info(f"straight_text: {straight_dict_text}")


        straight_dict_text = straight_dict_text.strip().strip("```").strip("json")
        straight_dict = json.loads(straight_dict_text)
        if "matched_id" not in straight_dict or straight_dict["matched_id"] is None:
            raise SingleMessageError(orjson.dumps({'message': 'match id not in response'}).decode())
        
        matched_id = straight_dict["matched_id"]
        if matched_id is None:
            raise SingleMessageError(orjson.dumps({'message': 'match id is None'}).decode())
        
        try:
            matched_id_int = int(matched_id)
        except (ValueError, TypeError):
            raise SingleMessageError(orjson.dumps({'message': f'invalid match id: {matched_id}'}).decode())
        
        # 检查 training_data 中的 id 是否有效
        valid_training_ids = []
        for i in training_data:
            if i.get("id") is not None:
                try:
                    valid_training_ids.append(int(i["id"]))
                except (ValueError, TypeError):
                    continue
        
        if matched_id_int not in valid_training_ids:
            raise SingleMessageError(orjson.dumps({'message': 'match id not in response'}).decode())

        matched_data = {}
        for i in training_data:
            if i.get("id") is not None:
                try:
                    if matched_id_int == int(i["id"]):
                        matched_data = i
                        break
                except (ValueError, TypeError):
                    continue

        # 如果没有找到匹配的数据，抛出异常以便回退到正常SQL生成
        if not matched_data:
            raise SingleMessageError(orjson.dumps({'message': 'match id not in training data'}).decode())

        # _async_log_util.info(f"straight_text: {matched_data}")
        #
        constructed_sql = matched_data.get("sql-template")
        default_kv = matched_data.get("sql-info")
        tables_str = matched_data.get("tables")

        template_id = matched_data.get("id", "unknown")
        template_question = (matched_data.get("question") or "")[:200]
        _async_log_util.info(f"[快速模板] 使用模板ID: {template_id}, 模板问题: {template_question}")

        # 隐式参数替换：模板中硬编码实体（如广东省）与用户问句实体（如四川省）对齐，无需改模板库
        if user_question and constructed_sql:
            replacements = extract_implicit_replacements(
                constructed_sql, template_question, user_question
            )
            if replacements:
                constructed_sql = apply_implicit_replacements(constructed_sql, replacements)

        # 检查必要字段是否存在
        if not constructed_sql:
            _async_log_util.error(f"[快速模板] 模板ID {template_id} 缺少sql-template字段")
            raise SingleMessageError(orjson.dumps({'message': 'sql-template not found in matched data'}).decode())
        if not tables_str:
            _async_log_util.error(f"[快速模板] 模板ID {template_id} 缺少tables字段")
            raise SingleMessageError(orjson.dumps({'message': 'tables not found in matched data'}).decode())

        # sql-info 允许为空对象（表示该模板无需额外填槽）；仅在缺失/格式异常时报错
        if default_kv is None:
            default_kv = {}
        if isinstance(default_kv, str):
            try:
                default_kv = json.loads(default_kv)
            except Exception:
                _async_log_util.error(f"[快速模板] 模板ID {template_id} 的sql-info不是合法JSON字符串")
                raise SingleMessageError(orjson.dumps({'message': 'invalid sql-info in matched data'}).decode())
        if not isinstance(default_kv, dict):
            _async_log_util.error(f"[快速模板] 模板ID {template_id} 的sql-info类型非法: {type(default_kv)}")
            raise SingleMessageError(orjson.dumps({'message': 'invalid sql-info type in matched data'}).decode())

        update_kv = straight_dict.get("infos", {})
        if update_kv is None or not isinstance(update_kv, dict):
            update_kv = {}
        _async_log_util.info(
            f"[快速模板] 模板ID {template_id} - 默认参数数量: {len(default_kv)}, 用户提供参数数量: {len(update_kv)}"
        )
        tables = [i.strip().strip("'").strip('"') for i in tables_str.split(",")]
        
        # 验证 update_kv 中的值是否完整（检查是否有明显的截断）
        for k, v in update_kv.items():
            if isinstance(v, str):
                v_str = str(v).strip()
                # 检查是否以不完整的引号或括号结尾（可能是截断）
                if v_str and (v_str.endswith("'") or v_str.endswith('"')) and len(v_str) > 1:
                    # 检查是否可能是截断的值（比如 '[202508' 而不是完整的值）
                    if v_str.startswith("'") and not v_str.endswith("';") and not v_str.endswith("'"):
                        _async_log_util.warning(f"Value for key '{k}' may be incomplete: {v_str}")
                    elif v_str.startswith('"') and not v_str.endswith('";') and not v_str.endswith('"'):
                        _async_log_util.warning(f"Value for key '{k}' may be incomplete: {v_str}")
        
        # 合并 default_kv 和 update_kv，update_kv 优先级更高
        merged_kv = {}
        for k, v in default_kv.items():
            merged_kv[k] = v
        for k, v in update_kv.items():
            # 如果值看起来不完整，尝试清理（移除可能的截断标记）
            if isinstance(v, str):
                v_cleaned = v.strip()
                # 如果值以不完整的引号结尾，尝试修复
                if v_cleaned.startswith("'") and v_cleaned.endswith("'") and len(v_cleaned) > 2:
                    # 可能是完整的值，保留
                    merged_kv[k] = v
                elif v_cleaned.startswith('"') and v_cleaned.endswith('"') and len(v_cleaned) > 2:
                    # 可能是完整的值，保留
                    merged_kv[k] = v
                else:
                    # 使用原始值
                    merged_kv[k] = v
            else:
                merged_kv[k] = v
        
        # 替换所有可能的占位符格式
        for k, v in merged_kv.items():
            v_str = str(v)
            
            # 生成所有可能的键名变体
            key_variants = [
                k,  # 原始键名
                k.upper(),  # 全大写
                k.lower(),  # 全小写
                k.capitalize(),  # 首字母大写
            ]
            
            # 如果键名包含下划线，生成不带下划线的变体
            if '_' in k:
                key_variants.append(k.lower().replace('_', ''))  # 小写无下划线
                key_variants.append(k.upper().replace('_', ''))  # 大写无下划线
            
            # 如果键名是驼峰式，生成下划线式
            if re.search(r'[a-z][A-Z]', k):  # 包含小写后跟大写
                snake_case = re.sub(r'(?<!^)(?=[A-Z])', '_', k).lower()
                key_variants.append(snake_case)
            
            # 对每个键名变体，替换所有可能的占位符格式
            for key_var in set(key_variants):  # 使用 set 去重
                # 1. [[KEY]] 格式（双方括号）
                constructed_sql = constructed_sql.replace(f"[[{key_var}]]", v_str)
                # 2. [KEY] 格式（单方括号）
                constructed_sql = constructed_sql.replace(f"[{key_var}]", v_str)

        # 兜底：模板若硬编码了时间（无 [[TIME]] 占位符），隐式提取可能未改到，用 merged_kv 覆盖模板默认时间字面量
        _TIME_KEYS = ("TIME", "DATE", "START_TIME", "END_TIME", "START_DATE", "END_DATE")
        for tk in _TIME_KEYS:
            if tk not in merged_kv:
                continue
            new_val = str(merged_kv[tk]).strip().strip("'").strip('"')
            old_val = (default_kv or {}).get(tk)
            if not old_val or new_val == str(old_val).strip().strip("'").strip('"'):
                continue
            old_str = str(old_val).strip()
            for quote in ("'", '"'):
                literal = quote + old_str + quote
                literal_like = quote + old_str + "%" + quote
                if literal in constructed_sql:
                    constructed_sql = constructed_sql.replace(literal, quote + new_val + quote)
                    _async_log_util.info(f"[快速模板] 模板ID {template_id} - 已用 merged_kv[{tk}]={new_val} 覆盖模板默认时间字面量 {literal}")
                    break
                if literal_like in constructed_sql:
                    constructed_sql = constructed_sql.replace(literal_like, quote + new_val + "%" + quote)
                    _async_log_util.info(f"[快速模板] 模板ID {template_id} - 已用 merged_kv[{tk}]={new_val} 覆盖模板默认时间 LIKE {literal_like}")
                    break

        _async_log_util.info(f"[快速模板] 模板ID {template_id} - 生成的SQL预览: {constructed_sql[:200]}...")
        
        # 验证 SQL 是否完整（检查是否有未闭合的引号或括号）
        if constructed_sql:
            sql_trimmed = constructed_sql.strip()
            
            # 检查 SQL 是否可能被截断
            # 1. 检查是否以不完整的引号结尾（比如 '[202508' 而不是 '[202508]' 或完整的值）
            if sql_trimmed:
                # 检查是否以单引号开头但未正确闭合
                single_quote_count = sql_trimmed.count("'")
                double_quote_count = sql_trimmed.count('"')
                
                # 如果引号数量是奇数，可能是未闭合的引号
                if single_quote_count % 2 != 0 or double_quote_count % 2 != 0:
                    # 检查最后几个字符是否看起来像截断的值
                    last_chars = sql_trimmed[-20:]
                    if "'" in last_chars or '"' in last_chars:
                        # 检查是否以不完整的引号结尾（比如 '... = '[202508'）
                        if (sql_trimmed.endswith("'") and not sql_trimmed.endswith("';") and 
                            not sql_trimmed.endswith("'") and sql_trimmed.count("'") > 1):
                            # 可能是截断，记录警告但继续处理
                            _async_log_util.warning(f"SQL may have unclosed quotes: {last_chars}")
                        elif (sql_trimmed.endswith('"') and not sql_trimmed.endswith('";') and 
                              not sql_trimmed.endswith('"') and sql_trimmed.count('"') > 1):
                            _async_log_util.warning(f"SQL may have unclosed quotes: {last_chars}")
                
                # 2. 检查是否包含基本的 SQL 关键字（验证 SQL 是否完整）
                sql_upper = sql_trimmed.upper()
                has_select = 'SELECT' in sql_upper
                has_from = 'FROM' in sql_upper
                
                # 如果包含 SELECT 但没有 FROM，可能是截断的 SQL
                if has_select and not has_from:
                    _async_log_util.warning(f"SQL may be incomplete (SELECT without FROM): {sql_trimmed[-100:]}")
                    # 不抛出异常，让后续的 check_sql 来处理
        
        sql_result = {
            "success": True,
            "sql": constructed_sql,
            "tables": tables,
        }
        sql_result["sql"] = sql_result["sql"].replace("\\n", "\n")

        # _async_log_util.info(f"straight_text: {default_kv}")
        # _async_log_util.info(f"straight_text: {update_kv}")

        _async_log_util.info(f"[快速模板] 模板ID {template_id} - SQL生成完成，SQL长度: {len(constructed_sql)}, 涉及表: {tables}")
        _async_log_util.info("=" * 20 + " generate_straight_sql " + "=" * 20 + "\n")
        # 使用 orjson 确保 JSON 序列化正确，特别是处理特殊字符
        try:
            return orjson.dumps(sql_result).decode('utf-8')
        except Exception as e:
            _async_log_util.error(f"[快速模板] 模板ID {template_id} - JSON序列化失败: {e}")
            # 如果序列化失败，尝试使用标准 json 库，并确保特殊字符被正确转义
            # json 已在函数开头导入，无需再次导入
            return json.dumps(sql_result, ensure_ascii=False)


    def generate_with_sub_sql(self, sql, sub_mappings: list):
        sub_query = json.dumps(sub_mappings, ensure_ascii=False)
        self.chat_question.sql = sql
        self.chat_question.sub_query = sub_query
        dynamic_sql_msg: List[Union[BaseMessage, dict[str, Any]]] = []
        dynamic_sql_msg.append(SystemMessage(content=self.chat_question.dynamic_sys_question()))
        dynamic_sql_msg.append(HumanMessage(content=self.chat_question.dynamic_user_question()))

        self.current_logs[OperationEnum.GENERATE_DYNAMIC_SQL] = start_log(session=self.session,
                                                                          ai_modal_id=self.chat_question.ai_modal_id,
                                                                          ai_modal_name=self.chat_question.ai_modal_name,
                                                                          operate=OperationEnum.GENERATE_DYNAMIC_SQL,
                                                                          record_id=self.record.id,
                                                                          full_message=[{'type': msg.type,
                                                                                         'content': msg.content}
                                                                                        for
                                                                                        msg in dynamic_sql_msg])

        full_thinking_text = ''
        full_dynamic_text = ''
        token_usage = {}
        res = process_stream(self.llm.stream(dynamic_sql_msg), token_usage)
        for chunk in res:
            if chunk.get('content'):
                full_dynamic_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')

        dynamic_sql_msg.append(AIMessage(full_dynamic_text))

        self.current_logs[OperationEnum.GENERATE_DYNAMIC_SQL] = end_log(session=self.session,
                                                                        log=self.current_logs[
                                                                            OperationEnum.GENERATE_DYNAMIC_SQL],
                                                                        full_message=[
                                                                            {'type': msg.type,
                                                                             'content': msg.content}
                                                                            for msg in dynamic_sql_msg],
                                                                        reasoning_content=full_thinking_text,
                                                                        token_usage=token_usage)

        # 优化：只在DEBUG模式下记录详细日志
        if settings.LOG_LEVEL == "DEBUG":
            _async_log_util.info(full_dynamic_text)
        return full_dynamic_text

    def generate_assistant_dynamic_sql(self, sql, tables: List):
        ds: AssistantOutDsSchema = self.ds
        sub_query = []
        result_dict = {}
        for table in ds.tables:
            if table.name in tables and table.sql:
                # sub_query.append({"table": table.name, "query": table.sql})
                result_dict[table.name] = table.sql
                sub_query.append({"table": table.name, "query": f'{dynamic_subsql_prefix}{table.name}'})
        if not sub_query:
            return None
        temp_sql_text = self.generate_with_sub_sql(sql=sql, sub_mappings=sub_query)
        result_dict['sqlbot_temp_sql_text'] = temp_sql_text
        return result_dict

    def build_table_filter(self, sql: str, filters: list):
        filter = json.dumps(filters, ensure_ascii=False)
        self.chat_question.sql = sql
        self.chat_question.filter = filter
        permission_sql_msg: List[Union[BaseMessage, dict[str, Any]]] = []
        permission_sql_msg.append(SystemMessage(content=self.chat_question.filter_sys_question()))
        permission_sql_msg.append(HumanMessage(content=self.chat_question.filter_user_question()))

        self.current_logs[OperationEnum.GENERATE_SQL_WITH_PERMISSIONS] = start_log(session=self.session,
                                                                                   ai_modal_id=self.chat_question.ai_modal_id,
                                                                                   ai_modal_name=self.chat_question.ai_modal_name,
                                                                                   operate=OperationEnum.GENERATE_SQL_WITH_PERMISSIONS,
                                                                                   record_id=self.record.id,
                                                                                   full_message=[
                                                                                       {'type': msg.type,
                                                                                        'content': msg.content} for
                                                                                       msg
                                                                                       in permission_sql_msg])
        full_thinking_text = ''
        full_filter_text = ''
        token_usage = {}
        res = process_stream(self.llm.stream(permission_sql_msg), token_usage)
        for chunk in res:
            if chunk.get('content'):
                full_filter_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')

        permission_sql_msg.append(AIMessage(full_filter_text))

        self.current_logs[OperationEnum.GENERATE_SQL_WITH_PERMISSIONS] = end_log(session=self.session,
                                                                                 log=self.current_logs[
                                                                                     OperationEnum.GENERATE_SQL_WITH_PERMISSIONS],
                                                                                 full_message=[
                                                                                     {'type': msg.type,
                                                                                      'content': msg.content}
                                                                                     for msg in permission_sql_msg],
                                                                                 reasoning_content=full_thinking_text,
                                                                                 token_usage=token_usage)

        # 优化：只在DEBUG模式下记录详细日志
        if settings.LOG_LEVEL == "DEBUG":
            _async_log_util.info(full_filter_text)
        return full_filter_text

    def generate_filter(self, sql: str, tables: List):
        filters = get_row_permission_filters(session=self.session, current_user=self.current_user, ds=self.ds,
                                             tables=tables)
        if not filters:
            return None
        return self.build_table_filter(sql=sql, filters=filters)

    def generate_assistant_filter(self, sql, tables: List):
        ds: AssistantOutDsSchema = self.ds
        filters = []
        for table in ds.tables:
            if table.name in tables and table.rule:
                filters.append({"table": table.name, "filter": table.rule})
        if not filters:
            return None
        return self.build_table_filter(sql=sql, filters=filters)

    def generate_chart(self, chart_type: Optional[str] = ''):
        # append current question
        self.chart_message.append(HumanMessage(self.chat_question.chart_user_question(chart_type)))

        # Token guard: prevent 400 error from context overflow
        model_max_tokens = getattr(settings, "MODEL_MAX_TOKENS", 65536)
        estimated = _estimate_tokens(self.chart_message)
        safety_limit = int(model_max_tokens * 0.8)  # 80% threshold
        if estimated > safety_limit:
            err_msg = f"上下文长度超出限制：当前输入约 {estimated} tokens（模型上限 {model_max_tokens}，安全阈值 {safety_limit}）。建议：开启新对话或缩短问题。"
            _async_log_util.warning(f"[Token守卫-图表] {err_msg}")
            raise SingleMessageError(err_msg)

        trace = self._trace_start(
            node_key="chart_generation",
            node_name="图表生成",
            input_payload={
                "chart_type_hint": chart_type,
                "messages": self._serialize_messages_for_trace(self.chart_message),
            }
        )

        self.current_logs[OperationEnum.GENERATE_CHART] = start_log(session=self.session,
                                                                    ai_modal_id=self.chat_question.ai_modal_id,
                                                                    ai_modal_name=self.chat_question.ai_modal_name,
                                                                    operate=OperationEnum.GENERATE_CHART,
                                                                    record_id=self.record.id,
                                                                    full_message=[
                                                                        {'type': msg.type, 'content': msg.content} for
                                                                        msg
                                                                        in self.chart_message])
        full_thinking_text = ''
        full_chart_text = ''
        token_usage = {}
        res = process_stream(
            self._stream_with_response_format(
                self.chart_message,
                self._chart_json_response_format(),
                "图表结构化输出"
            ),
            token_usage
        )
        for chunk in res:
            if chunk.get('content'):
                full_chart_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')
            yield chunk

        self.chart_message.append(AIMessage(full_chart_text))

        self.record = save_chart_answer(session=self.session, record_id=self.record.id,
                                        answer=orjson.dumps({'content': full_chart_text}).decode(),
                                        current_user=self.current_user)
        self.current_logs[OperationEnum.GENERATE_CHART] = end_log(session=self.session,
                                                                  log=self.current_logs[OperationEnum.GENERATE_CHART],
                                                                  full_message=[
                                                                      {'type': msg.type, 'content': msg.content}
                                                                      for msg in self.chart_message],
                                                                  reasoning_content=full_thinking_text,
                                                                  token_usage=token_usage)
        self._trace_end(
            trace,
            output_payload={
                "response_text": full_chart_text,
                "reasoning_content": full_thinking_text,
                "token_usage": token_usage,
            }
        )

    @staticmethod
    def check_sql(res: str) -> tuple[str, Optional[list]]:
        json_str = extract_nested_json(res)
        if json_str is None:
            # 尝试直接解析整个响应（可能没有额外的文本包装）
            try:
                json_str = res.strip().strip("```").strip("json").strip()
                # 验证是否是有效的 JSON
                test_data = orjson.loads(json_str)
                if 'success' in test_data and 'sql' in test_data:
                    json_str = res  # 使用原始响应
            except Exception:
                raise SingleMessageError(orjson.dumps({'message': 'Cannot parse sql from answer',
                                                       'traceback': "Cannot parse sql from answer:\n" + res[:500]}).decode())
        sql: str
        data: dict
        try:
            data = orjson.loads(json_str)
            if isinstance(data, list):
                # 兼容少数模型返回数组包装，尝试提取首个合法对象
                candidate = next((item for item in data if isinstance(item, dict)), None)
                if candidate is None:
                    raise SingleMessageError("Model response is a JSON list, not a SQL result object")
                data = candidate
            if not isinstance(data, dict):
                raise SingleMessageError("Model response JSON is not an object")

            if data.get('success', False):
                sql = data.get('sql', '')
                # 验证 SQL 是否完整
                if not sql or sql.strip() == '':
                    raise SingleMessageError("SQL query is empty in response")
                # 检查 SQL 是否可能被截断（以不完整的引号或括号结尾）
                sql_trimmed = sql.strip()
                if sql_trimmed and (sql_trimmed.endswith("'") or sql_trimmed.endswith('"')) and not sql_trimmed.endswith("';") and not sql_trimmed.endswith('";'):
                    # 检查是否是完整的 SQL 语句
                    if not any(keyword in sql_trimmed.upper() for keyword in ['SELECT', 'FROM', 'WHERE', 'GROUP', 'ORDER', 'HAVING', 'INSERT', 'UPDATE', 'DELETE']):
                        if settings.LOG_LEVEL == "DEBUG":
                            _async_log_util.warning(f"SQL may be incomplete: {sql_trimmed[-100:]}")
            else:
                message = data.get('message', 'Unknown error')
                raise SingleMessageError(message)
        except SingleMessageError as e:
            raise e
        except Exception as e:
            error_msg = f"Cannot parse sql from answer: {str(e)}"
            if settings.LOG_LEVEL == "DEBUG":
                _async_log_util.error(f"{error_msg}\nResponse: {res[:500]}")
            raise SingleMessageError(orjson.dumps({'message': 'Cannot parse sql from answer',
                                                   'traceback': f"{error_msg}\nResponse preview: {res[:500]}"}).decode())

        if sql.strip() == '':
            raise SingleMessageError("SQL query is empty")
        tables = data.get('tables')
        # 兜底：部分模型在修复后会漏掉 tables 字段，导致后续权限/图表链路信息不完整
        if not tables or not isinstance(tables, list):
            inferred = _parse_table_names_from_sql(sql)
            # 仅保留纯表名，避免 schema 前缀干扰后续 in_ 查询
            normalized = []
            for t in inferred:
                name = (t or "").split(".")[-1].strip().strip('"').strip("'")
                if name and name not in normalized:
                    normalized.append(name)
            tables = normalized
        return sql, tables


    @staticmethod
    def check_straight_sql(res: str) -> tuple[bool, Any, Dict[str, Any]]:
        status = False
        infos = None
        matched_id = None
        json_str = extract_nested_json(res)
        if json_str is None:
            # 尝试直接解析整个响应（可能没有额外的文本包装）
            try:
                json_str = res.strip().strip("```").strip("json").strip()
                # 验证是否是有效的 JSON
                orjson.loads(json_str)
            except Exception:
                _async_log_util.warning(f"[快速模板匹配] 无法解析LLM响应JSON: {res[:200]}")
                raise SingleMessageError(orjson.dumps({'message': 'Cannot parse sql from answer',
                                                       'traceback': "Cannot parse sql from answer:\n" + res[:500]}).decode())
        try:
            data = orjson.loads(json_str)

            if "success" not in data or not data['success'] or str(data['success']).lower() == "false":
                message = data.get('message', 'Unknown error')
                _async_log_util.info(f"[快速模板匹配] LLM返回匹配失败: {message}")
                return False, "", ""
            else:
                matched_id = data.get("matched_id")
                infos = data.get('infos', {})
                
                # 验证 matched_id 和 infos 是否有效
                if matched_id is None:
                    _async_log_util.warning(f"[快速模板匹配] matched_id为None，响应: {json_str[:200]}")
                    return False, "", ""
                
                # infos 允许为空（某些模板无需额外填槽即可直接使用默认 sql-info）
                if infos is None:
                    infos = {}
                if not isinstance(infos, dict):
                    _async_log_util.warning(f"[快速模板匹配] infos类型异常（已置空字典）: {type(infos)}, 响应: {json_str[:200]}")
                    infos = {}
                
                status = True
                _async_log_util.info(
                    f"[快速模板匹配] 匹配成功 - 模板ID: {matched_id}, 参数数量: {len(infos)}, "
                    f"empty_infos={'yes' if len(infos) == 0 else 'no'}"
                )

        except SingleMessageError as e:
            raise e
        except Exception as e:
            error_msg = f"Cannot parse sql from answer: {str(e)}"
            _async_log_util.error(f"[快速模板匹配] 解析响应失败: {error_msg}, 响应预览: {res[:500]}")
            raise SingleMessageError(orjson.dumps({'message': 'Cannot parse sql from answer',
                                                   'traceback': f"{error_msg}\nResponse preview: {res[:500]}"}).decode())

        return status, matched_id, infos

    @staticmethod
    def get_chart_type_from_sql_answer(res: str) -> Optional[str]:
        json_str = extract_nested_json(res)
        if json_str is None:
            return None

        chart_type: Optional[str]
        data: dict
        try:
            data = orjson.loads(json_str)

            if data['success']:
                chart_type = data.get('chart-type') or 'table'
            else:
                return None
        except Exception:
            return None

        return chart_type

    def check_save_sql(self, res: str) -> str:
        sql, *_ = self.check_sql(res=res)
        self._cqs_snapshot_time_notice = None
        save_sql(session=self.session, sql=sql, record_id=self.record.id, current_user=self.current_user)

        self.chat_question.sql = sql

        return sql

    @staticmethod
    def _normalize_field_token(token: str) -> str:
        token = (token or "").strip().strip('"').strip("'").strip("`").strip("[]")
        token = token.lower()
        token = re.sub(r"[\s\-_]+", "", token)
        return token

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text or ""))

    def _resolve_chart_display_name(self, model_name: Optional[str], resolved_value: str, chart_type: str) -> str:
        """
        在“保留模型生成能力”和“锚定 SQL 别名”间折中：
        - table 场景更保守，优先避免模型把列名泛化成无关词；
        - 非 table 场景保留模型命名（可读性优先）。
        """
        fallback = str(resolved_value or "").strip()
        if not fallback:
            return ""

        raw_name = str(model_name or "").strip().strip('"').strip("'").strip("`").strip("[]")
        if not raw_name:
            return fallback
        if self._normalize_field_token(raw_name) == self._normalize_field_token(fallback):
            return raw_name

        if chart_type != "table":
            return raw_name

        generic_names = {
            "企业数量", "数量", "企业类型", "类型", "值", "数值", "指标", "维度",
            "名称", "类别", "字段", "category", "value", "count", "type", "metric",
        }
        if raw_name.lower() in generic_names:
            return fallback
        if len(raw_name) <= 1:
            return fallback

        # 若 SQL 别名本身是中文，且模型名与其毫无中文重叠，则判定为偏差，回退到 SQL 别名。
        if self._contains_cjk(fallback):
            fallback_cjk = set(re.findall(r"[\u4e00-\u9fff]", fallback))
            raw_cjk = set(re.findall(r"[\u4e00-\u9fff]", raw_name))
            if not raw_cjk or not (fallback_cjk & raw_cjk):
                return fallback
        return raw_name

    def _resolve_chart_field(self, value: Optional[str], name: Optional[str], result_fields: List[str]) -> Optional[str]:
        if not result_fields:
            return None
        field_set = set(result_fields)
        lower_map = {str(f).lower(): str(f) for f in result_fields}
        norm_map: Dict[str, str] = {}
        for f in result_fields:
            key = self._normalize_field_token(str(f))
            if key and key not in norm_map:
                norm_map[key] = str(f)

        candidates = [value, name]
        for c in candidates:
            if not c:
                continue
            raw = str(c).strip().strip('"').strip("'").strip("`").strip("[]")
            if raw in field_set:
                return raw
            low = raw.lower()
            if low in lower_map:
                return lower_map[low]
            norm = self._normalize_field_token(raw)
            if norm in norm_map:
                return norm_map[norm]
        return None

    def _align_chart_with_result_fields(self, chart: Dict[str, Any], result_fields: List[str]) -> Dict[str, Any]:
        """
        保留模型映射意图，但把 value 校正为 SQL 实际字段键，避免前端显示 '-'。
        exec_sql(origin_column=False) 下列名会小写，此处以 result_fields 为唯一真值。
        """
        if not chart or not isinstance(chart, dict) or not result_fields:
            return chart
        corrected = 0
        fs = [str(f) for f in result_fields]
        field_set = set(fs)

        def _value_in_fields(val: Any) -> bool:
            if val is None or val == "":
                return False
            s = str(val).strip()
            return s in field_set or s.lower() in {x.lower() for x in fs}

        if chart.get("columns") and isinstance(chart.get("columns"), list):
            orig_cols = [c for c in chart.get("columns") if isinstance(c, dict)]
            fixed_columns: List[Dict[str, Any]] = []
            chart_type = str(chart.get("type") or "").strip().lower()
            for col in orig_cols:
                resolved = self._resolve_chart_field(col.get("value"), col.get("name"), result_fields)
                if resolved:
                    if col.get("value") != resolved:
                        corrected += 1
                    col["value"] = resolved
                    resolved_name = self._resolve_chart_display_name(col.get("name"), resolved, chart_type)
                    if col.get("name") != resolved_name:
                        corrected += 1
                    col["name"] = resolved_name
                    fixed_columns.append(col)
            # 任一列未解析成功或全部失配：用 SQL 返回字段直映射（柱状图/折线图等与 table 共用 columns 展示）
            if (not fixed_columns or len(fixed_columns) < len(orig_cols)) and fs:
                fixed_columns = [{"name": f, "value": f} for f in fs]
                corrected += len(fixed_columns)

            # table 强锚定：列集合与顺序必须严格对齐 SQL 真实返回字段，避免模型把别名漂移到错误语义。
            if chart_type == "table" and fs:
                by_value = {}
                for c in fixed_columns:
                    if not isinstance(c, dict):
                        continue
                    v = str(c.get("value") or "").strip()
                    if v:
                        by_value[v] = c
                anchored_columns: List[Dict[str, Any]] = []
                for f in fs:
                    mapped = by_value.get(f) or {}
                    model_name = mapped.get("name")
                    anchored_name = self._resolve_chart_display_name(model_name, f, chart_type)
                    anchored_columns.append({"name": anchored_name, "value": f})
                    if (mapped.get("value") != f) or (mapped.get("name") != anchored_name):
                        corrected += 1
                fixed_columns = anchored_columns
            chart["columns"] = fixed_columns

        # 确保 result_fields 的所有字段都在 columns 中，避免前端表格模式下丢失列
        # （非 table 类型 LLM 只选图表可视化需要的字段，切换表格视图时需补全）
        if fs and isinstance(chart.get("columns"), list):
            existing_values = {str(c.get("value", "")).strip().lower() for c in chart["columns"] if isinstance(c, dict)}
            for f in fs:
                if str(f).strip().lower() not in existing_values:
                    chart["columns"].append({"name": str(f), "value": str(f)})
                    corrected += 1

        if chart.get("axis") and isinstance(chart.get("axis"), dict):
            for axis_key in ("x", "y", "series"):
                axis_obj = chart.get("axis", {}).get(axis_key)
                if not isinstance(axis_obj, dict):
                    continue
                resolved = self._resolve_chart_field(axis_obj.get("value"), axis_obj.get("name"), result_fields)
                if resolved:
                    if axis_obj.get("value") != resolved:
                        corrected += 1
                    axis_obj["value"] = resolved
                    # 前端切换到 table 展示时也会使用 axis.name 作为列头，需同样锚定到 SQL 别名语义
                    axis_name = self._resolve_chart_display_name(axis_obj.get("name"), resolved, "table")
                    if axis_obj.get("name") != axis_name:
                        corrected += 1
                    axis_obj["name"] = axis_name
                elif axis_obj.get("value") is not None and not _value_in_fields(axis_obj.get("value")):
                    # 映射失败且当前 value 不是真实列名：按字段个数给默认轴，避免取数键为 undefined
                    idx = {"x": 0, "y": 1, "series": 2}.get(axis_key, 0)
                    if idx < len(fs):
                        axis_obj["value"] = fs[idx]
                        axis_obj["name"] = self._resolve_chart_display_name(axis_obj.get("name"), fs[idx], "table")
                        corrected += 1

        if corrected > 0:
            _async_log_util.info(f"[图表映射纠偏] 已按SQL字段自动校正 {corrected} 处映射")
        return chart

    def check_save_chart(self, res: str, result_fields: Optional[List[str]] = None) -> Dict[str, Any]:

        json_str = extract_nested_json(res)
        if json_str is None:
            raise SingleMessageError(orjson.dumps({'message': 'Cannot parse chart config from answer',
                                                   'traceback': "Cannot parse chart config from answer:\n" + res}).decode())
        data: dict

        chart: Dict[str, Any] = {}
        message = ''
        error = False

        try:
            data = orjson.loads(json_str)
            if data['type'] and data['type'] != 'error':
                # todo type check
                chart = data
                if chart.get('columns'):
                    for v in chart.get('columns'):
                        if isinstance(v, dict) and v.get('value') is not None:
                            v['value'] = str(v.get('value')).lower()
                if chart.get('axis'):
                    if chart.get('axis').get('x') and chart.get('axis').get('x').get('value') is not None:
                        chart.get('axis').get('x')['value'] = str(chart.get('axis').get('x').get('value')).lower()
                    if chart.get('axis').get('y') and chart.get('axis').get('y').get('value') is not None:
                        chart.get('axis').get('y')['value'] = str(chart.get('axis').get('y').get('value')).lower()
                    if chart.get('axis').get('series') and chart.get('axis').get('series').get('value') is not None:
                        chart.get('axis').get('series')['value'] = str(chart.get('axis').get('series').get('value')).lower()
            elif data['type'] == 'error':
                message = data['reason']
                error = True
            else:
                raise Exception('Chart is empty')
        except Exception:
            error = True
            message = orjson.dumps({'message': 'Cannot parse chart config from answer',
                                    'traceback': "Cannot parse chart config from answer:\n" + res}).decode()

        if error:
            raise SingleMessageError(message)

        if result_fields:
            chart = self._align_chart_with_result_fields(chart, [str(f) for f in result_fields])

        save_chart(session=self.session, chart=orjson.dumps(chart).decode(), record_id=self.record.id, current_user=self.current_user)

        return chart

    def check_save_predict_data(self, res: str) -> bool:

        json_str = extract_nested_json(res)

        if not json_str:
            json_str = ''

        save_predict_data(session=self.session, record_id=self.record.id, current_user=self.current_user, data=json_str)

        if json_str == '':
            return False

        return True

    def save_error(self, message: str):
        return save_error_message(session=self.session, record_id=self.record.id, message=message, current_user=self.current_user)

    def save_sql_data(self, data_obj: Dict[str, Any]):
        try:
            data_result = data_obj.get('data')
            if data_result:
                data_result = prepare_for_orjson(data_result)
                data_obj['data'] = data_result
            return save_sql_exec_data(session=self.session, record_id=self.record.id,
                                      data=orjson.dumps(data_obj).decode(), current_user=self.current_user)
        except Exception as e:
            raise e

    def finish(self):
        return finish_record(session=self.session, record_id=self.record.id, current_user=self.current_user)

    def execute_sql(self, sql: str):
        """Execute SQL query

        Args:
            ds: Data source instance
            sql: SQL query statement

        Returns:
            Query results
        """
        # 优化：只在DEBUG模式下记录SQL详情，INFO模式下只记录简要信息
        if settings.LOG_LEVEL == "DEBUG":
            _async_log_util.info(f"Executing SQL on ds_id {self.ds.id}: {sql}")
        else:
            _async_log_util.info(f"Executing SQL on ds_id {self.ds.id}")
        trace = self._trace_start(
            node_key="sql_execution",
            node_name="SQL执行",
            input_payload={
                "datasource_id": self.ds.id if self.ds else None,
                "sql": sql,
            }
        )
        try:
            result = exec_sql(ds=self.ds, sql=sql, origin_column=False)
            self._trace_end(
                trace,
                output_payload={
                    "fields": result.get("fields") if isinstance(result, dict) else None,
                    "row_count": len(result.get("data") or []) if isinstance(result, dict) else None,
                    "result_preview": (result.get("data") or [])[:5] if isinstance(result, dict) else None,
                }
            )
            return result
        except Exception as e:
            self._trace_end(
                trace,
                status=ChatExecutionTraceStatus.ERROR,
                error_message=str(e),
                output_payload={"error_type": e.__class__.__name__}
            )
            if isinstance(e, ParseSQLResultError):
                raise e
            else:
                err = traceback.format_exc(limit=1, chain=True)
                raise SQLBotDBError(err)

    def pop_chunk(self):
        try:
            chunk = self.chunk_list.pop(0)
            return chunk
        except IndexError as e:
            return None

    def await_result(self):
        while self.is_running():
            while True:
                chunk = self.pop_chunk()
                if chunk is not None:
                    yield chunk
                else:
                    break
        while True:
            chunk = self.pop_chunk()
            if chunk is None:
                break
            yield chunk

    def run_task_async(self, in_chat: bool = True, stream: bool = True,
                       finish_step: ChatFinishStep = ChatFinishStep.GENERATE_CHART):
        if in_chat:
            stream = True
        self.future = executor.submit(self.run_task_cache, in_chat, stream, finish_step)

    def run_task_cache(self, in_chat: bool = True, stream: bool = True,
                       finish_step: ChatFinishStep = ChatFinishStep.GENERATE_CHART):
        for chunk in self.run_task(in_chat, stream, finish_step):
            self.chunk_list.append(chunk)

    def run_task(self, in_chat: bool = True, stream: bool = True,
                 finish_step: ChatFinishStep = ChatFinishStep.GENERATE_CHART):
        json_result: Dict[str, Any] = {'success': True}
        # 单请求内问题增强只执行一次，避免 LLM 被重复调用 3 次及“过度增强”
        self._question_enhanced = False
        self._context_prompt_cache = None  # 同请求内避免重复执行智能上下文分析与仲裁者
        pipeline_status = ChatExecutionTraceStatus.SUCCESS
        pipeline_error_message: str | None = None
        self._pipeline_trace = self._trace_start(
            node_key="question_pipeline",
            node_name="问数主流程",
            input_payload={
                "question": self.chat_question.question,
                "chat_id": self.chat_question.chat_id,
                "record_id": self.record.id if self.record else None,
                "finish_step": finish_step.name if finish_step else None,
            }
        )
        try:
            training_data = []
            if self.ds:
                oid = self.ds.oid if isinstance(self.ds, CoreDatasource) else 1
                ds_id = self.ds.id if isinstance(self.ds, CoreDatasource) else None

                # Step 1: 上下文分析与问题增强（先于所有检索，保证术语/模板检索使用完整语义；有历史则一律先增强，避免「请导出25年5月的」等短句用残缺问句检索）
                if self.original_question is None:
                    self.original_question = self.chat_question.question
                if len(self.generate_sql_logs) > 0:
                    context_manager = ContextStateManager(self.session, self.current_user)
                    original_q = self.chat_question.question
                    enhanced = context_manager.enhance_question_with_history(
                        original_q,
                        self.generate_sql_logs
                    )
                    if enhanced and enhanced.strip():
                        self.chat_question.question = enhanced.strip()
                        self._question_enhanced = True
                        if enhanced.strip() != (original_q or '').strip():
                            _async_log_util.info(f"[问题增强-Step1] 检索前补全 - 原始: {original_q[:50]}, 增强后: {self.chat_question.question[:80]}")
                    self._trace_step(
                        node_key="question_enhancement",
                        node_name="问题增强",
                        input_payload={
                            "original_question": original_q,
                            "history_log_count": len(self.generate_sql_logs),
                        },
                        output_payload={
                            "enhanced_question": self.chat_question.question,
                            "question_changed": (self.chat_question.question or "").strip() != (original_q or "").strip(),
                        }
                    )

                # 步骤2：术语检索（使用增强后问题）
                if in_chat:
                    yield 'data:' + orjson.dumps({
                        'type': 'step-start',
                        'step': 'terminology-retrieval',
                        'step_name': '术语检索',
                        'description': '正在检索相关业务术语...'
                    }).decode() + '\n\n'
                
                # 使用增强后问题检索术语（Step1 已先执行问题增强）
                terminology_query = self.chat_question.question or ""
                terminology_template, terminology_data = get_terminology_template_with_data(
                    self.session, terminology_query, oid, ds_id)
                self.chat_question.terminologies = terminology_template
                self._trace_step(
                    node_key="terminology_retrieval",
                    node_name="术语检索",
                    input_payload={"query": terminology_query},
                    output_payload={
                        "count": len(terminology_data),
                        "items": [
                            {
                                "words": t.get("words", []) if isinstance(t, dict) else [],
                                "description": t.get("description", "") if isinstance(t, dict) else "",
                            }
                            for t in terminology_data[:5]
                        ],
                    }
                )
                
                if in_chat:
                    # terminology_data 是字典列表，每个字典包含 {'words': [], 'description': ...}
                    items = []
                    for t in terminology_data[:5]:  # 只返回前5个
                        if isinstance(t, dict):
                            # 提取所有words作为术语列表
                            words = t.get('words', [])
                            items.append({
                                'words': words,
                                'description': t.get('description', '')
                            })
                    yield 'data:' + orjson.dumps({
                        'type': 'step-complete',
                        'step': 'terminology-retrieval',
                        'step_name': '术语检索',
                        'description': f'检索已完成，共找到 {len(terminology_data)} 条相关术语',
                        'result': {
                            'count': len(terminology_data),
                            'items': items
                        }
                    }).decode() + '\n\n'
                
                # 步骤3：训练数据检索
                if in_chat:
                    yield 'data:' + orjson.dumps({
                        'type': 'step-start',
                        'step': 'training-retrieval',
                        'step_name': '训练数据检索',
                        'description': '正在检索相似SQL示例...'
                    }).decode() + '\n\n'
                
                # 使用增强后问题检索 SQL 模板（Step1 已先执行问题增强，此处直接复用）
                training_query = self.chat_question.question or ""
                training_template, sql_info_templates, training_data = get_training_template_with_data(self.session, training_query, ds_id, oid)
                # 实体类型一致性过滤：用户问「地区」时剔除「公司/集团」维度模板，避免误匹配（如海南省 vs 集团各产业）
                training_data_filtered = filter_training_data_by_entity_type(
                    training_data,
                    self.chat_question.question or "",
                )
                if training_data_filtered:
                    self.chat_question.data_training = get_base_data_training_template().format(
                        data_training=to_xml_string(training_data_filtered, need_template=True)
                    )
                    training_data = training_data_filtered
                else:
                    self.chat_question.data_training = get_base_data_training_template().format(
                        data_training=to_xml_string([], need_template=True)
                    )
                    training_data = []
                    _async_log_util.info("[快速模板匹配] 实体过滤后无兼容模板，将走正常SQL生成流程")

                # 记录embedding配置状态
                _async_log_util.info(f"[Embedding配置检查] EMBEDDING_ENABLED={settings.EMBEDDING_ENABLED}, EMBEDDING_DATA_TRAINING_SIMILARITY={settings.EMBEDDING_DATA_TRAINING_SIMILARITY}, EMBEDDING_DATA_TRAINING_TOP_COUNT={settings.EMBEDDING_DATA_TRAINING_TOP_COUNT}")
                _async_log_util.info(f"[Embedding配置检查] TABLE_EMBEDDING_ENABLED={settings.TABLE_EMBEDDING_ENABLED}, TABLE_EMBEDDING_COUNT={settings.TABLE_EMBEDDING_COUNT}")
                
                # 记录训练数据检索结果（含每个模板ID与模板问题）
                _async_log_util.info(f"[快速模板匹配] 检索到 {len(training_data)} 条相似SQL示例 - 检索用问题: {training_query[:100]}")
                if training_data:
                    template_ids = [str(t.get('id', 'unknown')) for t in training_data[:5]]
                    _async_log_util.info(f"[快速模板匹配] 前5个模板ID: {', '.join(template_ids)}")
                    for t in training_data[:10]:
                        tid, tq = t.get('id', 'unknown'), (t.get('question') or '')[:120]
                        _async_log_util.info(f"[快速模板匹配] 数据训练检索 - 模板ID: {tid}, 模板问题: {tq}")
                self._trace_step(
                    node_key="training_retrieval",
                    node_name="训练数据检索",
                    input_payload={"query": training_query},
                    output_payload={
                        "count": len(training_data),
                        "template_ids": [t.get("id") for t in training_data[:10]],
                        "questions": [t.get("question", "") for t in training_data[:5]],
                    }
                )
                
                if in_chat:
                    yield 'data:' + orjson.dumps({
                        'type': 'step-complete',
                        'step': 'training-retrieval',
                        'step_name': '训练数据检索',
                        'description': f'检索已完成，共找到 {len(training_data)} 条相似示例',
                        'result': {
                            'count': len(training_data),
                            'items': [{'question': t.get('question', '')} for t in training_data[:3]]  # 只返回前3个
                        }
                    }).decode() + '\n\n'
                
                if SQLBotLicenseUtil.valid() and find_custom_prompts is not None:
                    custom_prompt_result = find_custom_prompts(self.session, CustomPromptTypeEnum.GENERATE_SQL,
                                                                       oid, ds_id)
                    self.chat_question.custom_prompt = custom_prompt_result
                    custom_prompt_length = len(custom_prompt_result) if custom_prompt_result else 0
                    _async_log_util.info(f"[自定义提示词] 获取自定义提示词完成，类型: GENERATE_SQL, 长度: {custom_prompt_length} 字符")
                else:
                    _async_log_util.info(f"[自定义提示词] 未获取自定义提示词 (许可证有效: {SQLBotLicenseUtil.valid()}, find_custom_prompts可用: {find_custom_prompts is not None})")
                self._trace_step(
                    node_key="custom_prompt_load",
                    node_name="自定义提示词加载",
                    output_payload={
                        "loaded": bool(self.chat_question.custom_prompt),
                        "length": len(self.chat_question.custom_prompt or ""),
                    }
                )

            self.init_messages()
            self.init_straight_messages()
            self.init_rewrite_messages()
            self._trace_step(
                node_key="prompt_build",
                node_name="Prompt构建",
                output_payload={
                    "sql_message_count": len(self.sql_message),
                    "straight_message_count": len(self.straight_messages),
                    "rewrite_message_count": len(self.rewrite_messages),
                    "chart_message_count": len(self.chart_message),
                    "enhanced_question": self.chat_question.question,
                    "context_prompt_cached": bool(getattr(self, "_context_prompt_cache", None)),
                }
            )

            # return id
            if in_chat:
                yield 'data:' + orjson.dumps({'type': 'id', 'id': self.get_record().id}).decode() + '\n\n'
            if not stream:
                json_result['record_id'] = self.get_record().id

            # return title
            if self.change_title:
                if self.chat_question.question or self.chat_question.question.strip() != '':
                    brief = rename_chat(session=self.session,
                                        rename_object=RenameChat(id=self.get_record().chat_id,
                                                                 brief=self.chat_question.question.strip()[:20]),
                                        current_user=self.current_user)
                    if in_chat:
                        yield 'data:' + orjson.dumps({'type': 'brief', 'brief': brief}).decode() + '\n\n'
                    if not stream:
                        json_result['title'] = brief

                # 步骤1：数据源选择（如果数据源为空）
            if not self.ds:
                if in_chat:
                    yield 'data:' + orjson.dumps({
                        'type': 'step-start',
                        'step': 'datasource-select',
                        'step_name': '数据源选择',
                        'description': '正在选择数据源...'
                    }).decode() + '\n\n'
                
                ds_res = self.select_datasource()

                for chunk in ds_res:
                    # 优化：只在DEBUG模式下记录详细日志
                    if settings.LOG_LEVEL == "DEBUG":
                        _async_log_util.info(f"datasource-result: {chunk}")
                    if in_chat:
                        yield 'data:' + orjson.dumps(
                            {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                             'type': 'datasource-result'}).decode() + '\n\n'
                
                if in_chat:
                    yield 'data:' + orjson.dumps({
                        'type': 'step-complete',
                        'step': 'datasource-select',
                        'step_name': '数据源选择',
                        'description': f'已选择数据源：{self.ds.name}',
                        'result': {
                            'id': self.ds.id,
                            'name': self.ds.name,
                            'engine_type': self.ds.type_name or self.ds.type
                        }
                    }).decode() + '\n\n'
                    yield 'data:' + orjson.dumps({'id': self.ds.id, 'datasource_name': self.ds.name,
                                                  'engine_type': self.ds.type_name or self.ds.type,
                                                  'type': 'datasource'}).decode() + '\n\n'

                _async_log_util.info(f"[表结构获取] 开始获取表结构，TABLE_EMBEDDING_ENABLED={settings.TABLE_EMBEDDING_ENABLED}")
                # select_datasource() 内已对非动态数据源调用过 _resolve_db_schema_for_sql，此处避免重复 LLM 选表
                if not (self.chat_question.db_schema or "").strip():
                    self.chat_question.db_schema = self._resolve_db_schema_for_sql(
                        question=self.chat_question.question or "",
                        embedding=True
                    )
                else:
                    _async_log_util.info(
                        "[表结构获取] 数据源选择阶段已填充 db_schema，跳过重复的 _resolve_db_schema_for_sql"
                    )
                schema_length = len(self.chat_question.db_schema) if self.chat_question.db_schema else 0
                _async_log_util.info(f"[表结构获取] 表结构获取完成，schema长度: {schema_length} 字符")
                # 追问场景：确保上一轮 SQL 用到的表及其字段定义（含字段备注）被传入，避免漏传导致用错表或字段格式
                if (
                    len(self.generate_sql_logs) > 0 and self.chat_question.db_schema and self.ds
                    and not getattr(settings, "TABLE_SELECTOR_LLM_ENABLED", False)
                ):
                    latest_log = self.generate_sql_logs[-1]
                    if latest_log.pid:
                        try:
                            record = self.session.get(ChatRecord, latest_log.pid)
                            if record and record.sql and record.sql.strip():
                                history_table_names = _parse_table_names_from_sql(record.sql)
                                if history_table_names:
                                    schema_lower = (self.chat_question.db_schema or "").lower()
                                    missing = []
                                    for t in history_table_names:
                                        name = t.split(".")[-1].strip('"').lower() if t else ""
                                        if not name:
                                            continue
                                        # 表已在 schema 中：存在 "# table: ...name" 或 "# table: xxx.name"
                                        if re.search(rf"#\s*table:\s*[\w.]*{re.escape(name)}\b", schema_lower):
                                            continue
                                        missing.append(t)
                                    if missing:
                                        extra_schema = get_table_schema_for_tables(
                                            session=self.session,
                                            current_user=self.current_user,
                                            ds=self.ds,
                                            table_names=missing
                                        )
                                        if extra_schema:
                                            self.chat_question.db_schema = (self.chat_question.db_schema or "") + "\n" + extra_schema
                                            _async_log_util.info(f"[表结构获取] 追问补全：已合并上一轮涉及表的字段定义，表: {missing[:10]}")
                        except Exception as e:
                            _async_log_util.debug(f"[表结构获取] 追问补全表结构失败: {e}")
                self._trace_step(
                    node_key="schema_retrieval",
                    node_name="表结构获取",
                    output_payload={
                        "datasource_id": self.ds.id if self.ds else None,
                        "schema_length": len(self.chat_question.db_schema or ""),
                        "table_embedding_enabled": settings.TABLE_EMBEDDING_ENABLED,
                    }
                )
            else:
                self.validate_history_ds()
                preloaded_schema = (self.chat_question.db_schema or "").strip()
                if not preloaded_schema:
                    _async_log_util.warning(
                        "[表结构获取] 预加载 schema 为空，改为实时检索（含 LLM 选表）"
                    )
                    self.chat_question.db_schema = self._resolve_db_schema_for_sql(
                        question=self.chat_question.question or "",
                        embedding=True
                    )
                    self._trace_step(
                        node_key="schema_retrieval",
                        node_name="表结构获取",
                        output_payload={
                            "datasource_id": self.ds.id if self.ds else None,
                            "schema_length": len(self.chat_question.db_schema or ""),
                            "table_embedding_enabled": settings.TABLE_EMBEDDING_ENABLED,
                            "source": "realtime_fallback_from_empty_preloaded",
                        }
                    )
                else:
                    _async_log_util.info(
                        f"[表结构获取] 走 preloaded 分支，跳过实时选表 | "
                        f"schema_length={len(self.chat_question.db_schema or '')}"
                    )
                    self._trace_step(
                        node_key="schema_retrieval",
                        node_name="表结构获取",
                        output_payload={
                            "datasource_id": self.ds.id if self.ds else None,
                            "schema_length": len(self.chat_question.db_schema or ""),
                            "table_embedding_enabled": settings.TABLE_EMBEDDING_ENABLED,
                            "source": "preloaded",
                        }
                    )

            # 关键修复：schema_retrieval 完成后，必须重建 prompt 消息
            # 否则 init_messages 在前面已用空 schema 格式化，导致 {schema} 未注入到 SQL 生成节点
            self.init_messages()
            self.init_straight_messages()
            self.init_rewrite_messages()
            _async_log_util.info(
                f"[Prompt构建-重建] schema_retrieval后重建消息完成 | "
                f"schema_length={len(self.chat_question.db_schema or '')} | "
                f"sql_message_count={len(self.sql_message)} | "
                f"straight_message_count={len(self.straight_messages)} | "
                f"rewrite_message_count={len(self.rewrite_messages)}"
            )

            # check connection
            connected = check_connection(ds=self.ds, trans=None)
            if not connected:
                raise SQLBotDBConnectionError('Connect DB failed')

            # 步骤5：SQL生成
            if in_chat:
                yield 'data:' + orjson.dumps({
                    'type': 'step-start',
                    'step': 'sql-generation',
                    'step_name': 'SQL生成',
                    'description': '正在生成SQL语句...'
                }).decode() + '\n\n'

            # 启动快速模板匹配的日志记录（先启动，如果失败会在后面覆盖）
            straight_log = start_log(session=self.session,
                                    ai_modal_id=self.chat_question.ai_modal_id,
                                    ai_modal_name=self.chat_question.ai_modal_name,
                                    operate=OperationEnum.GENERATE_SQL,
                                    record_id=self.record.id,
                                    full_message=[
                                        {'type': msg.type, 'content': msg.content} for msg
                                        in self.straight_messages])

            # Token guard for SQL generation (warning only, chart path is the critical blocker)
            model_max_tokens = getattr(settings, "MODEL_MAX_TOKENS", 65536)
            estimated_sql_tokens = _estimate_tokens(self.straight_messages)
            safety_limit = int(model_max_tokens * 0.8)
            if estimated_sql_tokens > safety_limit:
                _async_log_util.warning(
                    f"[Token守卫-SQL] straight_messages token估计: {estimated_sql_tokens} > 安全阈值 {safety_limit}"
                )

            straight_sql_res = self.generate_straight_sql_info()
            full_straight_sql_text = ''
            full_straight_thinking_text = ''
            for chunk in straight_sql_res:
                if chunk.get('content'):
                    full_straight_sql_text += chunk.get('content')
                if chunk.get('reasoning_content'):
                    full_straight_thinking_text += chunk.get('reasoning_content')
                # 将chunk包装成正确的格式
                if in_chat:
                    yield 'data:' + orjson.dumps(
                        {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                         'type': 'sql-result'}).decode() + '\n\n'

            # 将AI响应添加到straight_messages中，用于日志记录
            self.straight_messages.append(AIMessage(content=full_straight_sql_text))

            # 获取token_usage（从generate_straight_sql_info设置的实例变量）
            token_usage = getattr(self, 'straight_sql_token_usage', {})
            status, matched_id, infos = self.check_straight_sql(full_straight_sql_text)

            # 记录快速模板匹配结果（含匹配到的模板问题）
            if status:
                template_question = ""
                for t in (training_data or []):
                    if str(t.get("id")) == str(matched_id):
                        template_question = (t.get("question") or "")[:120]
                        break
                _async_log_util.info(
                    f"[快速模板匹配] 匹配成功 - 用户问题: {(self.chat_question.question or '')[:100]}, 匹配模板ID: {matched_id}, "
                    f"模板问题: {template_question}, 匹配参数: {infos}"
                )
            else:
                _async_log_util.info(f"[快速模板匹配] 未匹配 - 用户问题: {(self.chat_question.question or '')[:100]}, 将使用正常SQL生成流程")
            
            double_check_ok = False
            if status:
                double_check_ok = self.double_check_straight_sql_info(matched_id, infos, training_data)
                tq_for_log = ""
                for t in (training_data or []):
                    if str(t.get("id")) == str(matched_id):
                        tq_for_log = (t.get("question") or "")[:80]
                        break
                if double_check_ok:
                    _async_log_util.info(f"[快速模板匹配] 二次校验通过 - 模板ID: {matched_id}, 模板问题: {tq_for_log}")
                else:
                    # 二次校验未通过则中断模板路径，走正常 SQL 生成，避免无依据默认公司/时间
                    _async_log_util.info(
                        f"[快速模板匹配] 二次校验未通过，中断快速模板，使用正常SQL生成流程 - 模板ID: {matched_id}, 模板问题: {tq_for_log}"
                    )
                    status = False
            self._trace_step(
                node_key="quick_template_result",
                node_name="快速模板结果",
                output_payload={
                    "matched": status,
                    "matched_id": matched_id,
                    "infos": infos,
                    "double_check_ok": double_check_ok,
                }
            )
            
            chart_type = ""
            if status:
                # 尝试使用快速模板（无论二次校验是否通过，只要首次匹配成功就尝试）
                try:
                    full_sql_text = self.generate_straight_sql(
                        training_data, full_straight_sql_text, self.chat_question.question or ""
                    )
                    _async_log_util.info(f"[快速模板] 成功使用快速模板生成SQL - 模板ID: {matched_id}, 用户问题: {self.chat_question.question[:100]}")
                    
                    # 快速模板匹配成功，保存日志
                    self.current_logs[OperationEnum.GENERATE_SQL] = end_log(session=self.session,
                                                                           log=straight_log,
                                                                           full_message=[{'type': msg.type, 'content': msg.content}
                                                                                        for msg in self.straight_messages],
                                                                           reasoning_content=full_straight_thinking_text,
                                                                           token_usage=token_usage)
                    
                    if in_chat:
                        yield 'data:' + orjson.dumps({
                            'type': 'step-start',
                            'step': 'sql-generation',
                            'step_name': '快速模板',
                            'description': '找到快速模板'
                        }).decode() + '\n\n'
                except Exception as e:
                    # 快速模板失败，回退到正常SQL生成
                    _async_log_util.warning(f"[快速模板] 快速模板生成失败，回退到正常SQL生成 - 模板ID: {matched_id}, 错误: {str(e)}, 用户问题: {self.chat_question.question[:100]}")
                    status = False  # 标记为失败，继续执行正常SQL生成流程
                    # 快速模板失败，不保存快速模板的日志，让正常SQL生成流程来保存日志
            
            if not status:
                # 未命中快速模板时，再执行问题重写，避免重写内容污染模板匹配输入
                if in_chat:
                    yield 'data:' + orjson.dumps({
                        'type': 'step-start',
                        'step': 'question-rewrite',
                        'step_name': '问题重写',
                        'description': '未命中快速模板，正在重写问题语义...'
                    }).decode() + '\n\n'

                original_question_before_rewrite = self.chat_question.question or ""
                rewritten_question = original_question_before_rewrite
                rewrite_raw_output = ""
                rewrite_changed = False
                rewrite_fallback_reason = ""
                try:
                    rewritten_question, rewrite_raw_output = self.generate_rewrite_question()
                    if rewritten_question and rewritten_question.strip():
                        rewritten_question = rewritten_question.strip()
                        if rewritten_question != original_question_before_rewrite:
                            self.chat_question.question = rewritten_question
                            # 重写完成后不再重复执行“历史追问增强”，避免重写结果被二次改写
                            self._question_enhanced = True
                            self.init_messages()
                            self.init_straight_messages()
                            self.init_rewrite_messages()
                            rewrite_changed = True
                            _async_log_util.info(
                                f"[问题重写] 已应用重写结果 | 原问题: {original_question_before_rewrite[:80]} | "
                                f"重写后: {rewritten_question[:120]}"
                            )
                    else:
                        rewrite_fallback_reason = "empty_rewrite_result"
                except Exception as rewrite_error:
                    rewrite_fallback_reason = str(rewrite_error)
                    _async_log_util.warning(
                        f"[问题重写] 重写失败，回退原问题继续执行SQL生成 | error={rewrite_fallback_reason}"
                    )

                self._trace_step(
                    node_key="question_rewrite_summary",
                    node_name="问题重写汇总",
                    input_payload={
                        "question_before": original_question_before_rewrite,
                    },
                    output_payload={
                        "question_after": self.chat_question.question,
                        "question_changed": rewrite_changed,
                        "rewrite_status": "fallback" if rewrite_fallback_reason else "success",
                        "fallback_reason": rewrite_fallback_reason,
                        "rewrite_output_preview": (rewrite_raw_output or "")[:500],
                    },
                    # 回退属于可预期降级，不应标记为主流程错误
                    status=ChatExecutionTraceStatus.SUCCESS,
                )

                if in_chat:
                    yield 'data:' + orjson.dumps({
                        'type': 'step-complete',
                        'step': 'question-rewrite',
                        'step_name': '问题重写',
                        'description': '问题重写已完成' if not rewrite_fallback_reason else '重写失败，已回退原问题',
                        'result': {
                            'rewritten_question': self.chat_question.question,
                            'changed': rewrite_changed
                        }
                    }).decode() + '\n\n'

                _async_log_util.info(f"[SQL生成] 使用正常SQL生成流程 - 用户问题: {self.chat_question.question[:100]}")
                # generate sql
                sql_res = self.generate_sql()
                full_sql_text = ''
                for chunk in sql_res:
                    full_sql_text += chunk.get('content') or ''
                    if in_chat:
                        yield 'data:' + orjson.dumps(
                            {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                             'type': 'sql-result'}).decode() + '\n\n'
                # 若为 no_data 或 LLM 返回「无法回答」等（success:false 的 JSON），补发 sql-generation 的 step-complete 并结束，避免前端一直转圈
                parsed = None
                if full_sql_text:
                    try:
                        parsed = orjson.loads(full_sql_text.strip())
                    except (orjson.JSONDecodeError, TypeError):
                        json_str = extract_nested_json(full_sql_text)
                        if json_str:
                            try:
                                parsed = orjson.loads(json_str)
                            except (orjson.JSONDecodeError, TypeError):
                                pass
                if isinstance(parsed, dict) and parsed.get('success') is False and parsed.get('message'):
                    if in_chat:
                        # 与图二一致：用 analysis-result 把提示输出到步骤下方主文区域（前端已有逻辑，无需改前端）
                        yield 'data:' + orjson.dumps({
                            'content': parsed.get('message'),
                            'reasoning_content': '',
                            'type': 'analysis-result'
                        }).decode() + '\n\n'
                        yield 'data:' + orjson.dumps({
                            'type': 'step-complete',
                            'step': 'sql-generation',
                            'step_name': 'SQL生成',
                            'description': '未生成SQL（已提示用户）',
                            'result': {'message': parsed.get('message')}
                        }).decode() + '\n\n'
                        yield 'data:' + orjson.dumps({'type': 'finish'}).decode() + '\n\n'
                    if not stream:
                        yield json_result
                    return
                # filter sql
                # 优化：只在DEBUG模式下记录完整SQL文本
                if getattr(settings, "SQL_AUTOFIX_ENABLED", True):
                    if in_chat:
                        yield 'data:' + orjson.dumps({
                            'type': 'step-start',
                            'step': 'sql-validation',
                            'step_name': 'SQL校验修复',
                            'description': '正在进行SQL语法校验...'
                        }).decode() + '\n\n'
                    full_sql_text, fixed_times = self._validate_and_autofix_sql_answer(
                        full_sql_text,
                        max_retries=int(getattr(settings, "SQL_AUTOFIX_MAX_RETRIES", 1) or 1)
                    )
                    if in_chat:
                        yield 'data:' + orjson.dumps({
                            'type': 'step-complete',
                            'step': 'sql-validation',
                            'step_name': 'SQL校验修复',
                            'description': 'SQL校验通过' if fixed_times == 0 else f'SQL自动修复完成（{fixed_times}次）',
                            'result': {'fixed_times': fixed_times}
                        }).decode() + '\n\n'
                chart_type = self.get_chart_type_from_sql_answer(full_sql_text)

            # if settings.LOG_LEVEL == "DEBUG":
            #     _async_log_util.info("DEBUG " * 100)
            #     _async_log_util.info(status)
            #     _async_log_util.info(full_sql_text)


            use_dynamic_ds: bool = self.current_assistant and self.current_assistant.type in dynamic_ds_types
            is_page_embedded: bool = self.current_assistant and self.current_assistant.type == 4
            dynamic_sql_result = None
            sqlbot_temp_sql_text = None
            assistant_dynamic_sql = None
            # todo row permission
            if ((not self.current_assistant or is_page_embedded) and is_normal_user(
                    self.current_user)) or use_dynamic_ds:
                sql, tables = self.check_sql(res=full_sql_text)
                sql_result = None

                if use_dynamic_ds:
                    dynamic_sql_result = self.generate_assistant_dynamic_sql(sql, tables)
                    sqlbot_temp_sql_text = dynamic_sql_result.get(
                        'sqlbot_temp_sql_text') if dynamic_sql_result else None
                    # sql_result = self.generate_assistant_filter(sql, tables)
                else:
                    sql_result = self.generate_filter(sql, tables)  # maybe no sql and tables

                if sql_result:
                    # 优化：只在DEBUG模式下记录详细日志
                    if settings.LOG_LEVEL == "DEBUG":
                        _async_log_util.info(sql_result)
                    sql = self.check_save_sql(res=sql_result)
                elif dynamic_sql_result and sqlbot_temp_sql_text:
                    assistant_dynamic_sql = self.check_save_sql(res=sqlbot_temp_sql_text)
                else:
                    sql = self.check_save_sql(res=full_sql_text)
            else:
                sql = self.check_save_sql(res=full_sql_text)

            # 优化：只在DEBUG模式下记录完整SQL
            if settings.LOG_LEVEL == "DEBUG":
                _async_log_util.info('sql: ' + sql)

            if not stream:
                json_result['sql'] = sql

            format_sql = sqlparse.format(sql, reindent=True)
            if in_chat:
                yield 'data:' + orjson.dumps({
                    'type': 'step-complete',
                    'step': 'sql-generation',
                    'step_name': 'SQL生成',
                    'description': 'SQL语句生成已完成',
                    'result': {
                        'sql_preview': format_sql[:200] + '...' if len(format_sql) > 200 else format_sql
                    }
                }).decode() + '\n\n'
                yield 'data:' + orjson.dumps({'content': format_sql, 'type': 'sql'}).decode() + '\n\n'
            else:
                if stream:
                    yield f'```sql\n{format_sql}\n```\n\n'

            # 步骤6：SQL执行
            if in_chat:
                yield 'data:' + orjson.dumps({
                    'type': 'step-start',
                    'step': 'sql-execution',
                    'step_name': 'SQL执行',
                    'description': '正在执行SQL查询...'
                }).decode() + '\n\n'
            
            # execute sql
            real_execute_sql = sql
            if sqlbot_temp_sql_text and assistant_dynamic_sql:
                dynamic_sql_result.pop('sqlbot_temp_sql_text')
                for origin_table, subsql in dynamic_sql_result.items():
                    assistant_dynamic_sql = assistant_dynamic_sql.replace(f'{dynamic_subsql_prefix}{origin_table}',
                                                                          subsql)
                real_execute_sql = assistant_dynamic_sql

            if finish_step.value <= ChatFinishStep.GENERATE_SQL.value:
                if in_chat:
                    yield 'data:' + orjson.dumps({'type': 'finish'}).decode() + '\n\n'
                if not stream:
                    yield json_result
                return

            # 执行SQL（在finish_step检查之后）
            try:
                result = self.execute_sql(sql=real_execute_sql)
            except Exception as e:
                err_msg = str(e) if str(e) else traceback.format_exc(limit=2)
                error_payload = orjson.dumps({
                    'message': 'Execute SQL Failed',
                    'traceback': err_msg,
                    'type': 'exec-sql-err'
                }).decode()
                if in_chat:
                    yield 'data:' + orjson.dumps({
                        'type': 'step-error',
                        'step': 'sql-execution',
                        'step_name': 'SQL执行'
                    }).decode() + '\n\n'
                    yield 'data:' + orjson.dumps({'content': error_payload, 'type': 'error'}).decode() + '\n\n'
                    yield 'data:' + orjson.dumps({'type': 'finish'}).decode() + '\n\n'
                if not stream:
                    json_result['success'] = False
                    json_result['message'] = err_msg
                    yield json_result
                return
            self.save_sql_data(data_obj=result)
            # SQL执行完成
            data_count = len(result.get('data', [])) if result.get('data') else 0
            field_count = len(result.get('fields', [])) if result.get('fields') else 0
            if in_chat:
                yield 'data:' + orjson.dumps({
                    'type': 'step-complete',
                    'step': 'sql-execution',
                    'step_name': 'SQL执行',
                    'description': f'SQL执行已完成，共查询到 {data_count} 行数据',
                    'result': {
                        'row_count': data_count,
                        'field_count': field_count,
                        'status': 'success'
                    }
                }).decode() + '\n\n'
                yield 'data:' + orjson.dumps({'content': 'execute-success', 'type': 'sql-data'}).decode() + '\n\n'
            if not stream:
                json_result['data'] = result.get('data')

            if finish_step.value <= ChatFinishStep.QUERY_DATA.value:
                if stream:
                    if in_chat:
                        yield 'data:' + orjson.dumps({'type': 'finish'}).decode() + '\n\n'
                    else:
                        data = []
                        _fields_list = []
                        _fields_skip = False
                        for _data in result.get('data'):
                            _row = []
                            for field in result.get('fields'):
                                _row.append(_data.get(field))
                                if not _fields_skip:
                                    _fields_list.append(field)
                            data.append(_row)
                            _fields_skip = True

                        if not data or not _fields_list:
                            yield 'The SQL execution result is empty.\n\n'
                        else:
                            df = pd.DataFrame(np.array(data), columns=_fields_list)
                            markdown_table = df.to_markdown(index=False)
                            yield markdown_table + '\n\n'
                else:
                    yield json_result
                return

            # 步骤7：图表生成（在数据分析之前）
            if in_chat:
                yield 'data:' + orjson.dumps({
                    'type': 'step-start',
                    'step': 'chart-generation',
                    'step_name': '图表生成',
                    'description': '正在生成图表配置...'
                }).decode() + '\n\n'

            # generate chart（包含 table），随后用 SQL 实际字段做自动纠偏
            chart_res = self.generate_chart(chart_type)
            full_chart_text = ''
            for chunk in chart_res:
                full_chart_text += chunk.get('content')
                if in_chat:
                    yield 'data:' + orjson.dumps(
                        {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                         'type': 'chart-result'}).decode() + '\n\n'
            # filter + align chart
            if settings.LOG_LEVEL == "DEBUG":
                _async_log_util.info(full_chart_text)
            _rf = result.get('fields') or []
            if not _rf and result.get("data") and isinstance(result["data"], list) and result["data"]:
                first_row = result["data"][0]
                if isinstance(first_row, dict):
                    _rf = list(first_row.keys())
            chart = self.check_save_chart(res=full_chart_text, result_fields=_rf)
            if settings.LOG_LEVEL == "DEBUG":
                _async_log_util.info(f"chart config: {chart}")
            
            # 图表生成完成
            if in_chat:
                chart_type_name = {'table': '表格', 'bar': '柱状图', 'line': '折线图', 'pie': '饼图'}.get(chart.get('type', 'table'), '图表')
                yield 'data:' + orjson.dumps({
                    'type': 'step-complete',
                    'step': 'chart-generation',
                    'step_name': '图表生成',
                    'description': f'{chart_type_name}配置生成已完成',
                    'result': {
                        'chart_type': chart.get('type', 'table')
                    }
                }).decode() + '\n\n'

            if not stream:
                json_result['chart'] = chart

            if in_chat:
                yield 'data:' + orjson.dumps(
                    {'content': orjson.dumps(chart).decode(), 'type': 'chart'}).decode() + '\n\n'
                
                # 步骤8：结果展示（在图表生成之后）
                # data_count在SQL执行步骤中已定义
                chart_type_name = {'table': '表格', 'bar': '柱状图', 'line': '折线图', 'pie': '饼图'}.get(chart.get('type', 'table'), '图表')
                yield 'data:' + orjson.dumps({
                    'type': 'step-complete',
                    'step': 'result-display',
                    'step_name': '结果展示',
                    'description': f'{chart_type_name}结果已生成',
                    'result': {
                        'chart_type': chart.get('type', 'table'),
                        'data_rows': data_count
                    }
                }).decode() + '\n\n'

            # 步骤9：数据分析（在结果展示之后）
            if in_chat:
                yield 'data:' + orjson.dumps({
                    'type': 'step-start',
                    'step': 'data-analysis',
                    'step_name': '数据分析',
                    'description': '正在分析数据...'
                }).decode() + '\n\n'
            
            # 生成文字分析（基于图表配置和SQL执行结果）
            analysis_res = self.generate_analysis()
            full_analysis_text = ''
            full_analysis_thinking = ''
            for chunk in analysis_res:
                if chunk.get('content'):
                    full_analysis_text += chunk.get('content')
                if chunk.get('reasoning_content'):
                    full_analysis_thinking += chunk.get('reasoning_content')
                if in_chat:
                    event_type = chunk.get('type') or 'analysis-result'
                    payload = {
                        'content': chunk.get('content'),
                        'reasoning_content': chunk.get('reasoning_content'),
                        'type': event_type,
                    }
                    # 透传 generate_analysis 额外字段（reflection_enabled/reflection_changed 等）
                    for k, v in chunk.items():
                        if k in ('type', 'content', 'reasoning_content'):
                            continue
                        payload[k] = v
                    yield 'data:' + orjson.dumps(payload).decode() + '\n\n'
            
            # 数据分析完成
            if in_chat:
                yield 'data:' + orjson.dumps({
                    'type': 'step-complete',
                    'step': 'data-analysis',
                    'step_name': '数据分析',
                    'description': '数据分析已完成'
                }).decode() + '\n\n'
                yield 'data:' + orjson.dumps({'type': 'analysis_finish'}).decode() + '\n\n'
            else:
                if stream:
                    data = []
                    _fields = {}
                    if chart.get('columns'):
                        for _column in chart.get('columns'):
                            if _column:
                                _fields[_column.get('value')] = _column.get('name')
                    if chart.get('axis'):
                        if chart.get('axis').get('x'):
                            _fields[chart.get('axis').get('x').get('value')] = chart.get('axis').get('x').get('name')
                        if chart.get('axis').get('y'):
                            _fields[chart.get('axis').get('y').get('value')] = chart.get('axis').get('y').get('name')
                        if chart.get('axis').get('series'):
                            _fields[chart.get('axis').get('series').get('value')] = chart.get('axis').get('series').get(
                                'name')
                    _fields_list = []
                    _fields_skip = False
                    for _data in result.get('data'):
                        _row = []
                        for field in result.get('fields'):
                            _row.append(_data.get(field))
                            if not _fields_skip:
                                _fields_list.append(field if not _fields.get(field) else _fields.get(field))
                        data.append(_row)
                        _fields_skip = True

                    if not data or not _fields_list:
                        yield 'The SQL execution result is empty.\n\n'
                    else:
                        df = pd.DataFrame(np.array(data), columns=_fields_list)
                        markdown_table = df.to_markdown(index=False)
                        yield markdown_table + '\n\n'

            if in_chat:
                yield 'data:' + orjson.dumps({'type': 'finish'}).decode() + '\n\n'
            else:
                # todo generate picture
                if chart['type'] != 'table':
                    yield '### generated chart picture\n\n'
                    image_url = request_picture(self.record.chat_id, self.record.id, chart, result)
                    # 优化：只在DEBUG模式下记录图片URL
                    if settings.LOG_LEVEL == "DEBUG":
                        _async_log_util.info(image_url)
                    if stream:
                        yield f'![{chart["type"]}]({image_url})'
                    else:
                        json_result['image_url'] = image_url

            if not stream:
                yield json_result

        except Exception as e:
            # 关键：异常后先恢复 Session，避免后续 finally 块中的 DB 操作触发 PendingRollbackError
            try:
                self.session.rollback()
            except Exception:
                pass
            _async_log_util.exception("LLM task failed")
            pipeline_status = ChatExecutionTraceStatus.ERROR
            error_msg: str
            if isinstance(e, SingleMessageError):
                single_msg = str(e)
                if any(k in single_msg for k in [
                    "SQL auto-fix failed",
                    "Database parser error",
                    "Cannot parse sql from answer",
                    "SQL query is empty",
                ]):
                    # 用户侧隐藏技术细节，详细原因放到 traceback，前端走“查看具体报错”弹窗
                    error_msg = orjson.dumps({
                        "message": "Execute SQL Failed",
                        "traceback": single_msg,
                        "type": "exec-sql-err"
                    }).decode()
                else:
                    error_msg = single_msg
            elif isinstance(e, SQLBotDBConnectionError):
                error_msg = orjson.dumps(
                    {'message': str(e), 'type': 'db-connection-err'}).decode()
            elif isinstance(e, SQLBotDBError):
                error_msg = orjson.dumps(
                    {'message': 'Execute SQL Failed', 'traceback': str(e), 'type': 'exec-sql-err'}).decode()
            else:
                error_msg = orjson.dumps({'message': str(e), 'traceback': traceback.format_exc(limit=1)}).decode()
            pipeline_error_message = error_msg
            self.save_error(message=error_msg)
            if in_chat:
                # 标记当前进行中的步骤为错误状态
                # 尝试推断当前步骤（简单实现，可以根据实际情况优化）
                yield 'data:' + orjson.dumps({
                    'type': 'step-error',
                    'step': 'current-step',  # 可以根据实际情况设置具体步骤
                    'step_name': '处理失败',
                    'error': error_msg
                }).decode() + '\n\n'
                yield 'data:' + orjson.dumps({'content': error_msg, 'type': 'error'}).decode() + '\n\n'
            else:
                if stream:
                    yield f'> &#x274c; **ERROR**\n\n> \n\n> {error_msg}。'
                else:
                    json_result['success'] = False
                    json_result['message'] = error_msg
                    yield json_result
        finally:
            self._trace_end(
                self._pipeline_trace,
                status=pipeline_status,
                error_message=pipeline_error_message,
                output_payload={
                    "record_id": self.record.id if self.record else None,
                    "chat_id": self.record.chat_id if self.record else None,
                    "sql_saved": bool(getattr(self.record, "sql_answer", None)) if getattr(self, "record", None) else False,
                    "finish_step": finish_step.name if finish_step else None,
                }
            )
            self.finish()
            self._close_session_safely()

    def run_recommend_questions_task_async(self):
        self.future = executor.submit(self.run_recommend_questions_task_cache)

    def run_recommend_questions_task_cache(self):
        for chunk in self.run_recommend_questions_task():
            self.chunk_list.append(chunk)

    def run_recommend_questions_task(self):
        res = self.generate_recommend_questions_task()

        for chunk in res:
            if chunk.get('recommended_question'):
                yield 'data:' + orjson.dumps(
                    {'content': chunk.get('recommended_question'), 'type': 'recommended_question'}).decode() + '\n\n'
            else:
                yield 'data:' + orjson.dumps(
                    {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                     'type': 'recommended_question_result'}).decode() + '\n\n'

    def run_analysis_or_predict_task_async(self, action_type: str, base_record: ChatRecord):
        self.set_record(save_analysis_predict_record(self.session, base_record, action_type))
        self.future = executor.submit(self.run_analysis_or_predict_task_cache, action_type)

    def run_analysis_or_predict_task_cache(self, action_type: str):
        for chunk in self.run_analysis_or_predict_task(action_type):
            self.chunk_list.append(chunk)

    def run_analysis_or_predict_task(self, action_type: str):
        try:

            yield 'data:' + orjson.dumps({'type': 'id', 'id': self.get_record().id}).decode() + '\n\n'

            if action_type == 'analysis':
                # generate analysis
                analysis_res = self.generate_analysis()
                for chunk in analysis_res:
                    event_type = chunk.get('type') or 'analysis-result'
                    payload = {
                        'content': chunk.get('content'),
                        'reasoning_content': chunk.get('reasoning_content'),
                        'type': event_type,
                    }
                    # 透传额外字段（用于调试/前端展示等）
                    for k, v in chunk.items():
                        if k in ('type', 'content', 'reasoning_content'):
                            continue
                        payload[k] = v
                    yield 'data:' + orjson.dumps(payload).decode() + '\n\n'
                yield 'data:' + orjson.dumps({'type': 'info', 'msg': 'analysis generated'}).decode() + '\n\n'

                yield 'data:' + orjson.dumps({'type': 'analysis_finish'}).decode() + '\n\n'

            elif action_type == 'predict':
                # generate predict
                analysis_res = self.generate_predict()
                full_text = ''
                for chunk in analysis_res:
                    yield 'data:' + orjson.dumps(
                        {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                         'type': 'predict-result'}).decode() + '\n\n'
                    full_text += chunk.get('content')
                yield 'data:' + orjson.dumps({'type': 'info', 'msg': 'predict generated'}).decode() + '\n\n'

                _data = self.check_save_predict_data(res=full_text)
                if _data:
                    yield 'data:' + orjson.dumps({'type': 'predict-success'}).decode() + '\n\n'
                else:
                    yield 'data:' + orjson.dumps({'type': 'predict-failed'}).decode() + '\n\n'

                yield 'data:' + orjson.dumps({'type': 'predict_finish'}).decode() + '\n\n'

            self.finish()
        except Exception as e:
            try:
                self.session.rollback()
            except Exception:
                pass
            error_msg: str
            if isinstance(e, SingleMessageError):
                error_msg = str(e)
            else:
                error_msg = orjson.dumps({'message': str(e), 'traceback': traceback.format_exc(limit=1)}).decode()
            self.save_error(message=error_msg)
            yield 'data:' + orjson.dumps({'content': error_msg, 'type': 'error'}).decode() + '\n\n'
        finally:
            self._close_session_safely()

    def validate_history_ds(self):
        _ds = self.ds
        if not self.current_assistant or self.current_assistant.type == 4:
            try:
                current_ds = self.session.get(CoreDatasource, _ds.id)
                if not current_ds:
                    raise SingleMessageError('chat.ds_is_invalid')
            except Exception as e:
                raise SingleMessageError("chat.ds_is_invalid")
        else:
            try:
                _ds_list: list[dict] = get_assistant_ds(session=self.session, llm_service=self)
                match_ds = any(item.get("id") == _ds.id for item in _ds_list)
                if not match_ds:
                    type = self.current_assistant.type
                    msg = f"[please check ds list and public ds list]" if type == 0 else f"[please check ds api]"
                    raise SingleMessageError(msg)
            except Exception as e:
                raise SingleMessageError(f"ds is invalid [{str(e)}]")


def execute_sql_with_db(db: SQLDatabase, sql: str) -> str:
    """Execute SQL query using SQLDatabase

    Args:
        db: SQLDatabase instance
        sql: SQL query statement

    Returns:
        str: Query results formatted as string
    """
    try:
        # Execute query
        result = db.run(sql)

        if not result:
            return "Query executed successfully but returned no results."

        # Format results
        return str(result)

    except Exception as e:
        error_msg = f"SQL execution failed: {str(e)}"
        _async_log_util.exception(error_msg)
        raise RuntimeError(error_msg)


def _request_picture_sync(chat_id: int, record_id: int, chart: dict, data: dict):
    """同步请求图片生成（在后台线程中执行）"""
    try:
        file_name = f'c_{chat_id}_r_{record_id}'

        columns = chart.get('columns') if chart.get('columns') else []
        x = None
        y = None
        series = None
        if chart.get('axis'):
            x = chart.get('axis').get('x')
            y = chart.get('axis').get('y')
            series = chart.get('axis').get('series')

        axis = []
        for v in columns:
            axis.append({'name': v.get('name'), 'value': v.get('value')})
        if x:
            axis.append({'name': x.get('name'), 'value': x.get('value'), 'type': 'x'})
        if y:
            axis.append({'name': y.get('name'), 'value': y.get('value'), 'type': 'y'})
        if series:
            axis.append({'name': series.get('name'), 'value': series.get('value'), 'type': 'series'})

        request_obj = {
            "path": os.path.join(settings.MCP_IMAGE_PATH, file_name),
            "type": chart['type'],
            "data": orjson.dumps(data.get('data') if data.get('data') else []).decode(),
            "axis": orjson.dumps(axis).decode(),
        }

        requests.post(url=settings.MCP_IMAGE_HOST, json=request_obj, timeout=10)
    except Exception as e:
        _async_log_util.error(f"Failed to request picture: {e}")


def request_picture(chat_id: int, record_id: int, chart: dict, data: dict):
    """异步请求图片生成，不阻塞主流程"""
    # 优化：使用线程池异步执行，立即返回URL，不等待请求完成
    executor.submit(_request_picture_sync, chat_id, record_id, chart, data)
    
    # 立即返回预期的图片URL，不等待实际生成完成
    file_name = f'c_{chat_id}_r_{record_id}'
    request_path = urllib.parse.urljoin(settings.SERVER_IMAGE_HOST, f"{file_name}.png")
    
    return request_path


def _stream_text_chunks(text: str, chunk_size: int = 80):
    """将整段文本按句或按长度分块 yield，用于分析反思结果的流式输出。"""
    if not text:
        return
    # 优先按句/段切（。！？\n），再按 chunk_size 长度
    parts = re.split(r"(?<=[。！？\n])", text)
    buf = ""
    for p in parts:
        buf += p
        if len(buf) >= chunk_size or (p.strip() and buf.endswith(("。", "！", "？", "\n"))):
            if buf.strip():
                yield buf
            buf = ""
    if buf.strip():
        yield buf


def get_token_usage(chunk: BaseMessageChunk, token_usage: dict = None):
    try:
        if chunk.usage_metadata:
            if token_usage is None:
                token_usage = {}
            token_usage['input_tokens'] = chunk.usage_metadata.get('input_tokens')
            token_usage['output_tokens'] = chunk.usage_metadata.get('output_tokens')
            token_usage['total_tokens'] = chunk.usage_metadata.get('total_tokens')
    except Exception:
        pass


def process_stream(res: Iterator[BaseMessageChunk],
                   token_usage: Dict[str, Any] = None,
                   enable_tag_parsing: bool = settings.PARSE_REASONING_BLOCK_ENABLED,
                   start_tag: str = settings.DEFAULT_REASONING_CONTENT_START,
                   end_tag: str = settings.DEFAULT_REASONING_CONTENT_END
                   ):
    if token_usage is None:
        token_usage = {}
    in_thinking_block = False  # 标记是否在思考过程块中
    current_thinking = ''  # 当前收集的思考过程内容
    pending_start_tag = ''  # 用于缓存可能被截断的开始标签部分

    chunk_count = 0  # 用于控制日志频率
    for chunk in res:
        chunk_count += 1
        # 优化：只在DEBUG模式下或每100个chunk记录一次日志，减少日志开销
        if settings.LOG_LEVEL == "DEBUG" and chunk_count % 100 == 0:
            _async_log_util.info(f"stream chunk {chunk_count}: {chunk.content[:100] if hasattr(chunk, 'content') else str(chunk)[:100]}...")
        reasoning_content_chunk = ''
        content = chunk.content if isinstance(chunk.content, str) else ("" if chunk.content is None else str(chunk.content))
        output_content = ''  # 实际要输出的内容

        # 检查additional_kwargs中的reasoning_content
        if 'reasoning_content' in chunk.additional_kwargs or 'reasoning' in chunk.additional_kwargs:
            reasoning_content = chunk.additional_kwargs.get('reasoning_content', '')
            if not reasoning_content:
                reasoning_content = chunk.additional_kwargs.get('reasoning', '')
            if reasoning_content is None:
                reasoning_content = ''

            # 累积additional_kwargs中的思考内容到current_thinking
            current_thinking += reasoning_content
            reasoning_content_chunk = reasoning_content

        # 只有当current_thinking不是空字符串时才跳过标签解析
        if not in_thinking_block and current_thinking.strip() != '':
            output_content = content  # 正常输出content
            yield {
                'content': output_content,
                'reasoning_content': reasoning_content_chunk
            }
            get_token_usage(chunk, token_usage)
            continue  # 跳过后续的标签解析逻辑

        # 如果没有有效的思考内容，并且启用了标签解析，才执行标签解析逻辑
        # 如果有缓存的开始标签部分，先拼接当前内容
        if pending_start_tag:
            content = pending_start_tag + content
            pending_start_tag = ''

        # 检查是否开始思考过程块（处理可能被截断的开始标签）
        if enable_tag_parsing and not in_thinking_block and start_tag:
            if start_tag in content:
                start_idx = content.index(start_tag)
                # 只有当开始标签前面没有其他文本时才认为是真正的思考块开始
                if start_idx == 0 or content[:start_idx].strip() == '':
                    # 完整标签存在且前面没有其他文本
                    output_content += content[:start_idx]  # 输出开始标签之前的内容
                    content = content[start_idx + len(start_tag):]  # 移除开始标签
                    in_thinking_block = True
                else:
                    # 开始标签前面有其他文本，不认为是思考块开始
                    output_content += content
                    content = ''
            else:
                # 检查是否可能有部分开始标签
                for i in range(1, len(start_tag)):
                    if content.endswith(start_tag[:i]):
                        # 只有当当前内容全是空白时才缓存部分标签
                        if content[:-i].strip() == '':
                            pending_start_tag = start_tag[:i]
                            content = content[:-i]  # 移除可能的部分标签
                            output_content += content
                            content = ''
                        break

        # 处理思考块内容
        if enable_tag_parsing and in_thinking_block and end_tag:
            if end_tag in content:
                # 找到结束标签
                end_idx = content.index(end_tag)
                current_thinking += content[:end_idx]  # 收集思考内容
                reasoning_content_chunk += current_thinking  # 添加到当前块的思考内容
                content = content[end_idx + len(end_tag):]  # 移除结束标签后的内容
                current_thinking = ''  # 重置当前思考内容
                in_thinking_block = False
                output_content += content  # 输出结束标签之后的内容
            else:
                # 在遇到结束标签前，持续收集思考内容
                current_thinking += content
                reasoning_content_chunk += content
                content = ''

        else:
            # 不在思考块中或标签解析未启用，正常输出
            output_content += content

        yield {
            'content': output_content,
            'reasoning_content': reasoning_content_chunk
        }
        get_token_usage(chunk, token_usage)


def get_lang_name(lang: str):
    if not lang:
        return '简体中文'
    normalized = lang.lower()
    if normalized.startswith('en'):
        return '英文'
    if normalized.startswith('ko'):
        return '韩语'
    return '简体中文'
