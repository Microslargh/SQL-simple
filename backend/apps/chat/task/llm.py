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

import numpy as np
import orjson
import pandas as pd
import requests
import sqlparse
from langchain.chat_models.base import BaseChatModel
from langchain_community.utilities import SQLDatabase
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, BaseMessageChunk
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
    get_last_execute_sql_error
from apps.chat.models.chat_model import ChatQuestion, ChatRecord, Chat, RenameChat, ChatLog, OperationEnum, \
    ChatFinishStep
from apps.chat.context import ContextStateManager
from apps.chat.context.question_enhancer import is_any_follow_up
from sqlbot_xpack.license.license_manage import SQLBotLicenseUtil
# 兼容旧版本 sqlbot-xpack，custom_prompt 模块可能不存在
try:
    from sqlbot_xpack.custom_prompt.curd.custom_prompt import find_custom_prompts
    from sqlbot_xpack.custom_prompt.models.custom_prompt_model import CustomPromptTypeEnum
except ImportError:
    # 如果模块不存在，定义占位函数和枚举
    find_custom_prompts = None
    CustomPromptTypeEnum = None
from apps.data_training.curd.data_training import get_training_template, get_training_template_with_data
from apps.datasource.crud.datasource import get_table_schema, get_table_schema_for_tables
from apps.datasource.crud.permission import get_row_permission_filters, is_normal_user
from apps.datasource.embedding.ds_embedding import get_ds_embedding
from apps.datasource.models.datasource import CoreDatasource
from apps.db.db import exec_sql, get_version, check_connection
from apps.system.crud.assistant import AssistantOutDs, AssistantOutDsFactory, get_assistant_ds
from apps.system.schemas.system_schema import AssistantOutDsSchema
from apps.terminology.curd.terminology import get_terminology_template, get_terminology_template_with_data
from apps.template.question_enhance.generator import get_question_enhance_template
from common.core.config import settings
from common.core.db import engine
from common.core.deps import CurrentAssistant, CurrentUser
from common.error import SingleMessageError, SQLBotDBError, ParseSQLResultError, SQLBotDBConnectionError
from common.utils.utils import SQLBotLogUtil, extract_nested_json, prepare_for_orjson, _async_log_util

warnings.filterwarnings("ignore")

base_message_count_limit = 6

executor = ThreadPoolExecutor(max_workers=200)

dynamic_ds_types = [1, 3]
dynamic_subsql_prefix = 'select * from sqlbot_dynamic_temp_table_'

session_maker = sessionmaker(bind=engine)
db_session = session_maker()


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
    chart_message: List[Union[BaseMessage, dict[str, Any]]] = []

    session: Session = db_session
    current_user: CurrentUser
    current_assistant: "CurrentAssistant | None" = None
    out_ds_instance: Optional[AssistantOutDs] = None
    change_title: bool = False

    generate_sql_logs: List[ChatLog] = []
    generate_chart_logs: List[ChatLog] = []

    current_logs: dict[OperationEnum, ChatLog] = {}

    chunk_list: List[str] = []
    future: Future

    last_execute_sql_error: str = None
    original_question: Optional[str] = None  # 保存原始问题，用于上下文分析

    def __init__(self, current_user: CurrentUser, chat_question: ChatQuestion,
                 current_assistant: "CurrentAssistant | None" = None, no_reasoning: bool = False,
                 embedding: bool = False, config: LLMConfig = None):
        self.chunk_list = []
        # engine = create_engine(str(settings.SQLALCHEMY_DATABASE_URI))
        # session_maker = sessionmaker(bind=engine)
        # self.session = session_maker()
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
                chat_question.db_schema = get_table_schema(session=self.session, current_user=current_user, ds=ds,
                                                           question=chat_question.question, embedding=embedding)

        self.generate_sql_logs = list_generate_sql_logs(session=self.session, chart_id=chat_id, current_user=current_user)
        self.generate_chart_logs = list_generate_chart_logs(session=self.session, chart_id=chat_id, current_user=current_user)

        self.change_title = len(self.generate_sql_logs) == 0

        chat_question.lang = get_lang_name(current_user.language)

        self.ds = (ds if isinstance(ds, AssistantOutDsSchema) else CoreDatasource(**ds.model_dump())) if ds else None
        self.chat_question = chat_question
        self.config = config
        if no_reasoning:
            # only work while using qwen
            if self.config.additional_params:
                if self.config.additional_params.get('extra_body'):
                    if self.config.additional_params.get('extra_body').get('enable_thinking'):
                        del self.config.additional_params['extra_body']['enable_thinking']

        self.chat_question.ai_modal_id = self.config.model_id
        self.chat_question.ai_modal_name = self.config.model_name

        # Create LLM instance through factory
        llm_instance = LLMFactory.create_llm(self.config)
        self.llm = llm_instance.llm

        # get last_execute_sql_error
        last_execute_sql_error = get_last_execute_sql_error(self.session, self.chat_question.chat_id, current_user)
        if last_execute_sql_error:
            self.chat_question.error_msg = f'''<error-msg>
{last_execute_sql_error}
</error-msg>'''
        else:
            self.chat_question.error_msg = ''

    @classmethod
    async def create(cls, *args, **kwargs):
        config: LLMConfig = await get_default_config()
        instance = cls(*args, **kwargs, config=config)
        return instance

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
            enhanced = (getattr(response, 'content', None) or '').strip()
            if enhanced and enhanced != current_question:
                _async_log_util.info(f"[问题增强-LLM] 原始: {current_question}")
                _async_log_util.info(f"[问题增强-LLM] 补充后: {enhanced}")
                return enhanced
        except Exception as e:
            _async_log_util.debug(f"[问题增强-LLM] 调用失败，将使用规则补充: {e}")
        return None

    def init_messages(self):
        """初始化SQL生成消息，使用智能上下文管理"""
        self.sql_message = []
        
        # 多轮对话：先尝试用大模型智能补充问题，再回退到规则补充
        if self.original_question is None:  # 只在第一次调用时保存原始问题
            self.original_question = self.chat_question.question
        original_question = self.original_question  # 使用保存的原始问题
        enhanced_question = self.chat_question.question
        if len(self.generate_sql_logs) > 0:
            context_manager = ContextStateManager(self.session, self.current_user)
            latest_log = self.generate_sql_logs[-1]
            history_question = None
            if latest_log.messages:
                for msg in latest_log.messages:
                    if msg.get('type') == 'human':
                        history_question = msg.get('content', '')
                        break
            if history_question:
                llm_enhanced = self._enhance_question_by_llm(self.chat_question.question, history_question)
                if llm_enhanced:
                    enhanced_question = llm_enhanced
                else:
                    enhanced_question = context_manager.enhance_question_with_history(
                        self.chat_question.question,
                        self.generate_sql_logs
                    )
            else:
                enhanced_question = context_manager.enhance_question_with_history(
                    self.chat_question.question,
                    self.generate_sql_logs
                )
            
            if enhanced_question != self.chat_question.question:
                _async_log_util.info(f"[问题增强] 原始问题: {self.chat_question.question}")
                _async_log_util.info(f"[问题增强] 增强后问题: {enhanced_question}")
                self.chat_question.question = enhanced_question
        
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
                    _async_log_util.info(f"[多轮对话] 已添加结构化上下文提示，长度: {len(context_prompt)} 字符")
                else:
                    _async_log_util.info(f"[多轮对话] 上下文提取完成，但无有效上下文内容")
            else:
                _async_log_util.info(f"[多轮对话] 当前问题无需上下文信息")
        else:
            _async_log_util.info(f"[多轮对话] 无历史SQL日志，这是第一轮对话")

        # 收集所有历史图表消息（从所有历史日志中）
        all_chart_messages: List[dict[str, Any]] = []
        if len(self.generate_chart_logs) > 0:
            for log in self.generate_chart_logs:
                if log.messages:
                    # 从每条日志的messages中提取human和ai消息（跳过system消息）
                    for msg in log.messages:
                        if msg.get('type') in ['human', 'ai']:
                            all_chart_messages.append(msg)

        self.chart_message = []
        # add sys prompt
        self.chart_message.append(SystemMessage(content=self.chat_question.chart_sys_question()))

        if all_chart_messages and len(all_chart_messages) > 0:
            # 图表消息通常不需要限制数量，因为每次对话通常只有一个图表
            _async_log_util.info(f"[多轮对话] 加载历史图表消息: 总共 {len(all_chart_messages)} 条")
            for chart_message in all_chart_messages:
                _msg: BaseMessage
                if chart_message.get('type') == 'human':
                    _msg = HumanMessage(content=chart_message.get('content'))
                    self.chart_message.append(_msg)
                elif chart_message.get('type') == 'ai':
                    _msg = AIMessage(content=chart_message.get('content'))
                    self.chart_message.append(_msg)
        else:
            _async_log_util.info(f"[多轮对话] 无历史图表消息")

    def init_straight_messages(self):
        """初始化快速模板匹配的消息列表，使用智能上下文管理"""
        self.straight_messages = []
        
        # 多轮对话：先尝试用大模型智能补充问题，再回退到规则补充（与 init_messages 一致，问题已在 init_messages 中可能被更新）
        enhanced_question = self.chat_question.question
        if len(self.generate_sql_logs) > 0:
            context_manager = ContextStateManager(self.session, self.current_user)
            latest_log = self.generate_sql_logs[-1]
            history_question = None
            if latest_log.messages:
                for msg in latest_log.messages:
                    if msg.get('type') == 'human':
                        history_question = msg.get('content', '')
                        break
            if history_question:
                llm_enhanced = self._enhance_question_by_llm(self.chat_question.question, history_question)
                if llm_enhanced:
                    enhanced_question = llm_enhanced
                else:
                    enhanced_question = context_manager.enhance_question_with_history(
                        self.chat_question.question,
                        self.generate_sql_logs
                    )
            else:
                enhanced_question = context_manager.enhance_question_with_history(
                    self.chat_question.question,
                    self.generate_sql_logs
                )
            
            if enhanced_question != self.chat_question.question:
                _async_log_util.info(f"[问题增强-快速模板] 原始问题: {self.chat_question.question}")
                _async_log_util.info(f"[问题增强-快速模板] 增强后问题: {enhanced_question}")
                self.chat_question.question = enhanced_question
        
        # add straight prompt
        self.straight_messages.append(SystemMessage(content=self.chat_question.sql_straight_question()))
        
        # 使用上下文管理器进行智能上下文提取（与init_messages()相同的逻辑）
        if len(self.generate_sql_logs) > 0:
            _async_log_util.info(f"[多轮对话-快速模板] 开始智能上下文分析，历史日志数量: {len(self.generate_sql_logs)}")
            
            context_manager = ContextStateManager(self.session, self.current_user)
            # 使用原始问题检测时间追问等模式，但使用扩展后的问题进行上下文提取
            original_question = self.original_question if self.original_question else enhanced_question
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
                    self.straight_messages.append(HumanMessage(content=context_message))
                    _async_log_util.info(f"[多轮对话-快速模板] 已添加结构化上下文提示，长度: {len(context_prompt)} 字符")
                else:
                    _async_log_util.info(f"[多轮对话-快速模板] 上下文提取完成，但无有效上下文内容")
            else:
                _async_log_util.info(f"[多轮对话-快速模板] 当前问题无需上下文信息")
        else:
            _async_log_util.info(f"[多轮对话-快速模板] 无历史SQL日志，这是第一轮对话")


    def init_record(self) -> ChatRecord:
        self.record = save_question(session=self.session, current_user=self.current_user, question=self.chat_question)
        return self.record

    def get_record(self):
        return self.record

    def set_record(self, record: ChatRecord):
        self.record = record

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
        for chunk in res:
            if chunk.get('content'):
                full_analysis_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')
            yield chunk

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
        self.chat_question.data = orjson.dumps(data.get('data')).decode()
        
        # 传递SQL信息给数据分析模块，用于正确识别时间范围
        if self.record and self.record.sql:
            self.chat_question.sql = self.record.sql
        
        analysis_msg: List[Union[BaseMessage, dict[str, Any]]] = []

        ds_id = self.ds.id if isinstance(self.ds, CoreDatasource) else None
        self.chat_question.terminologies = get_terminology_template(self.session, self.chat_question.question,
                                                                    self.current_user.oid, ds_id)
        if SQLBotLicenseUtil.valid() and find_custom_prompts is not None:
            self.chat_question.custom_prompt = find_custom_prompts(self.session, CustomPromptTypeEnum.ANALYSIS,
                                                               self.current_user.oid, ds_id)

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
        full_thinking_text = ''
        full_analysis_text = ''
        token_usage = {}
        res = process_stream(self.llm.stream(analysis_msg), token_usage)
        for chunk in res:
            if chunk.get('content'):
                full_analysis_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')
            yield chunk

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

    def generate_predict(self):
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

    def generate_recommend_questions_task(self):

        # get schema
        if self.ds and not self.chat_question.db_schema:
            self.chat_question.db_schema = self.out_ds_instance.get_db_schema(
                self.ds.id) if self.out_ds_instance else get_table_schema(session=self.session,
                                                                          current_user=self.current_user, ds=self.ds,
                                                                          question=self.chat_question.question,
                                                                          embedding=False)

        guess_msg: List[Union[BaseMessage, dict[str, Any]]] = []
        guess_msg.append(SystemMessage(content=self.chat_question.guess_sys_question()))

        old_questions = list(map(lambda q: q.strip(), get_old_questions(self.session, self.record.datasource, self.current_user)))
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
                    self.chat_question.db_schema = get_table_schema(session=self.session,
                                                                    current_user=self.current_user, ds=self.ds,
                                                                    question=self.chat_question.question)
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
            raise _error

    def generate_sql(self):
        # append current question
        self.sql_message.append(HumanMessage(
            self.chat_question.sql_user_question(current_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))

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
        res = process_stream(self.llm.stream(self.sql_message), token_usage)
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


    def double_check_straight_sql_info(self,matched_id, infos, training_data):
        matched_data = None
        for i in training_data:
            if int(matched_id) == int(i["id"]):
                matched_data = i
        if not matched_data:
            return False
        template_id = matched_data["id"]
        template_question = matched_data["question"]
        sql_template = matched_data["sql-template"]
        sql_info = matched_data["sql-info"]
        user_sql_info = json.dumps(infos)

        double_check_messages = [
            SystemMessage(
                self.chat_question.double_check_question(template_id, template_question, sql_template, sql_info, user_sql_info)),
            HumanMessage(
                self.chat_question.sql_user_question(current_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S')))]

        # 二次校验时用于排查：若用户问题与模板问题一致仍判 False，可对比此日志
        _async_log_util.info(
            f"[快速模板匹配] 二次校验入参 - 模板ID: {template_id}, 模板问题: {template_question[:80]}, 当前用户问题: {self.chat_question.question[:80]}"
        )
        token_usage = {}
        res = process_stream(self.llm.stream(double_check_messages), token_usage)

        content_list = []
        for chunk in res:
            if chunk.get('content'):
                content_list += chunk.get('content')
        content_str = "".join(content_list)

        if settings.LOG_LEVEL == "DEBUG":
            _async_log_util.info("=" * 20 + " generate_straight_sql_info " + "=" * 20 + "\n")
            _async_log_util.info("DEBUG" * 20)
            _async_log_util.info(f"double_check_messages: {double_check_messages}")
            _async_log_util.info(f"content_str: {content_str}")
            _async_log_util.info("=" * 20 + " generate_straight_sql_info " + "=" * 20 + "\n")
        # 解析二次校验结果：要求模型最后一行只返回 True 或 False
        lines = [line.strip() for line in content_str.split("\n") if line.strip()]
        # 先看最后一行（模型按要求应在最后一行返回 True/False）
        last_line = (lines[-1] if lines else "").strip().rstrip(".").rstrip("。").lower()
        if last_line == "true":
            _async_log_util.info(f"[快速模板匹配] 二次校验通过 - 模型最后一行: {lines[-1] if lines else '(空)'}")
            return True
        if last_line == "false":
            _async_log_util.info(f"[快速模板匹配] 二次校验返回False - 模型完整回复:\n{content_str[:500]}")
            return False
        # 最后一行不是 true/false 时，可能是格式问题：在全文找单独一行的 true/false（取最后一次出现）
        for line in reversed(lines):
            normalized = line.strip().rstrip(".").rstrip("。").lower()
            if normalized == "true":
                _async_log_util.info(f"[快速模板匹配] 二次校验通过（从全文解析） - 找到True的行: {line}")
                return True
            if normalized == "false":
                _async_log_util.info(f"[快速模板匹配] 二次校验解析到False - 模型完整回复:\n{content_str[:500]}")
                return False
        # 无法解析出 True/False 时视为未通过，并打出完整回复便于排查
        _async_log_util.info(
            f"[快速模板匹配] 二次校验无法解析True/False（可能模型未按「最后一行只返回True或False」输出） - 模型完整回复:\n{content_str[:800]}"
        )
        return False


    def generate_straight_sql_info(self):
        self.straight_messages.append(HumanMessage(
            self.chat_question.sql_user_question(current_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))))

        # 打印发送给模型的完整 prompt（请求体）
        prompt_content = []
        for msg in self.straight_messages:
            if isinstance(msg, SystemMessage):
                prompt_content.append({
                    "role": "system",
                    "content": msg.content
                })
            elif isinstance(msg, HumanMessage):
                prompt_content.append({
                    "role": "user",
                    "content": msg.content
                })
            elif isinstance(msg, dict):
                prompt_content.append(msg)
            else:
                # 处理其他类型的消息
                prompt_content.append({
                    "type": type(msg).__name__,
                    "content": str(msg.content) if hasattr(msg, 'content') else str(msg)
                })
        
        # 格式化输出 prompt
        prompt_json = orjson.dumps(prompt_content, option=orjson.OPT_INDENT_2).decode('utf-8')
        _async_log_util.info("=" * 80)
        _async_log_util.info("[快速模板匹配] 发送给模型的 Prompt（请求体）:")
        _async_log_util.info("=" * 80)
        _async_log_util.info(prompt_json)
        _async_log_util.info("=" * 80)

        if settings.LOG_LEVEL == "DEBUG":
            _async_log_util.info("=" * 20 + " generate_straight_sql_info " + "=" * 20 + "\n")
            _async_log_util.info(f"datasource-result: {self.straight_messages}")
            _async_log_util.info("=" * 20 + " generate_straight_sql_info " + "=" * 20 + "\n")


        token_usage = {}
        res = process_stream(self.llm.stream(self.straight_messages), token_usage)
        for chunk in res:
            yield chunk
        # 将token_usage存储到实例变量中，以便外部访问
        self.straight_sql_token_usage = token_usage


    @staticmethod
    def generate_straight_sql(training_data, straight_dict_text):
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
        
        # 记录匹配到的模板信息
        template_id = matched_data.get("id", "unknown")
        template_question = matched_data.get("question", "")[:100]
        _async_log_util.info(f"[快速模板] 使用模板ID: {template_id}, 模板问题: {template_question}")
        
        # 检查必要字段是否存在
        if not constructed_sql:
            _async_log_util.error(f"[快速模板] 模板ID {template_id} 缺少sql-template字段")
            raise SingleMessageError(orjson.dumps({'message': 'sql-template not found in matched data'}).decode())
        if not default_kv:
            _async_log_util.error(f"[快速模板] 模板ID {template_id} 缺少sql-info字段")
            raise SingleMessageError(orjson.dumps({'message': 'sql-info not found in matched data'}).decode())
        if not tables_str:
            _async_log_util.error(f"[快速模板] 模板ID {template_id} 缺少tables字段")
            raise SingleMessageError(orjson.dumps({'message': 'tables not found in matched data'}).decode())
        
        update_kv = straight_dict.get("infos", {})
        _async_log_util.info(f"[快速模板] 模板ID {template_id} - 默认参数数量: {len(default_kv) if isinstance(default_kv, dict) else 0}, 用户提供参数数量: {len(update_kv) if isinstance(update_kv, dict) else 0}")
        tables = [i.strip().strip("'").strip('"') for i in tables_str.split(",")]
        if isinstance(default_kv, str):
            default_kv = json.loads(default_kv)
        
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
        res = process_stream(self.llm.stream(self.chart_message), token_usage)
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
        return sql, data.get('tables')


    @staticmethod
    def check_straight_sql(res: str) -> tuple[bool, str, str]:
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
                
                # 验证 infos 是否为空或无效
                if not infos or (isinstance(infos, dict) and len(infos) == 0):
                    _async_log_util.warning(f"[快速模板匹配] infos为空，响应: {json_str[:200]}")
                    return False, "", ""
                
                status = True
                _async_log_util.info(f"[快速模板匹配] 匹配成功 - 模板ID: {matched_id}, 参数数量: {len(infos) if isinstance(infos, dict) else 0}")

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
                chart_type = data['chart-type']
            else:
                return None
        except Exception:
            return None

        return chart_type

    def check_save_sql(self, res: str) -> str:
        sql, *_ = self.check_sql(res=res)
        save_sql(session=self.session, sql=sql, record_id=self.record.id, current_user=self.current_user)

        self.chat_question.sql = sql

        return sql

    def check_save_chart(self, res: str) -> Dict[str, Any]:

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
                        v['value'] = v.get('value').lower()
                if chart.get('axis'):
                    if chart.get('axis').get('x'):
                        chart.get('axis').get('x')['value'] = chart.get('axis').get('x').get('value').lower()
                    if chart.get('axis').get('y'):
                        chart.get('axis').get('y')['value'] = chart.get('axis').get('y').get('value').lower()
                    if chart.get('axis').get('series'):
                        chart.get('axis').get('series')['value'] = chart.get('axis').get('series').get('value').lower()
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
        try:
            return exec_sql(ds=self.ds, sql=sql, origin_column=False)
        except Exception as e:
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
        try:
            training_data = []
            if self.ds:
                oid = self.ds.oid if isinstance(self.ds, CoreDatasource) else 1
                ds_id = self.ds.id if isinstance(self.ds, CoreDatasource) else None
                
                # 步骤2：术语检索
                if in_chat:
                    yield 'data:' + orjson.dumps({
                        'type': 'step-start',
                        'step': 'terminology-retrieval',
                        'step_name': '术语检索',
                        'description': '正在检索相关业务术语...'
                    }).decode() + '\n\n'
                
                # 多轮对话增强：指代词或任意追问（时间/地区/指标/公司主体）时，结合历史问题检索术语
                terminology_query = self.chat_question.question
                if len(self.generate_sql_logs) > 0:
                    reference_words = ['这些', '它们', '上述', '上面', '刚才', '之前', '上一轮', '刚才的', '那些']
                    has_reference = any(word in self.chat_question.question for word in reference_words)
                    is_followup = is_any_follow_up(self.chat_question.question)
                    
                    if has_reference or is_followup:
                        latest_log = self.generate_sql_logs[-1]
                        if latest_log.messages:
                            for msg in latest_log.messages:
                                if msg.get('type') == 'human':
                                    history_question = msg.get('content', '')
                                    if history_question:
                                        terminology_query = f"{history_question} {self.chat_question.question}"
                                        trigger_type = "追问" if is_followup else "指代词"
                                        _async_log_util.info(f"[术语检索增强] 检测到{trigger_type}，结合历史问题检索术语: {terminology_query[:200]}")
                                        break
                
                terminology_template, terminology_data = get_terminology_template_with_data(
                    self.session, terminology_query, oid, ds_id)
                self.chat_question.terminologies = terminology_template
                
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
                
                training_template, sql_info_templates, training_data = get_training_template_with_data(self.session, self.chat_question.question, ds_id, oid)
                self.chat_question.data_training = training_template
                
                # 记录embedding配置状态
                _async_log_util.info(f"[Embedding配置检查] EMBEDDING_ENABLED={settings.EMBEDDING_ENABLED}, EMBEDDING_DATA_TRAINING_SIMILARITY={settings.EMBEDDING_DATA_TRAINING_SIMILARITY}, EMBEDDING_DATA_TRAINING_TOP_COUNT={settings.EMBEDDING_DATA_TRAINING_TOP_COUNT}")
                _async_log_util.info(f"[Embedding配置检查] TABLE_EMBEDDING_ENABLED={settings.TABLE_EMBEDDING_ENABLED}, TABLE_EMBEDDING_COUNT={settings.TABLE_EMBEDDING_COUNT}")
                
                # 记录训练数据检索结果
                _async_log_util.info(f"[快速模板匹配] 检索到 {len(training_data)} 条相似SQL示例 - 用户问题: {self.chat_question.question[:100]}")
                if training_data:
                    template_ids = [str(t.get('id', 'unknown')) for t in training_data[:5]]
                    _async_log_util.info(f"[快速模板匹配] 前5个模板ID: {', '.join(template_ids)}")
                
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

            self.init_messages()
            self.init_straight_messages()

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
                self.chat_question.db_schema = self.out_ds_instance.get_db_schema(
                    self.ds.id) if self.out_ds_instance else get_table_schema(session=self.session,
                                                                              current_user=self.current_user,
                                                                              ds=self.ds,
                                                                              question=self.chat_question.question,
                                                                              embedding=True)
                schema_length = len(self.chat_question.db_schema) if self.chat_question.db_schema else 0
                _async_log_util.info(f"[表结构获取] 表结构获取完成，schema长度: {schema_length} 字符")
                # 追问场景：确保上一轮 SQL 用到的表及其字段定义（含字段备注）被传入，避免漏传导致用错表或字段格式
                if len(self.generate_sql_logs) > 0 and self.chat_question.db_schema and self.ds:
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
            else:
                self.validate_history_ds()

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

            straight_sql_res = self.generate_straight_sql_info()
            # if settings.LOG_LEVEL == "DEBUG":
            #     _async_log_util.info("DEBUG " * 100)
            #     _async_log_util.info(self.straight_messages)
            #     for i in straight_sql_res:
            #         _async_log_util.info(i)
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
            
            # if settings.LOG_LEVEL == "DEBUG":
            #     _async_log_util.info("DEBUG " * 100)
            #     _async_log_util.info(full_straight_sql_text)
            status, matched_id, infos = self.check_straight_sql(full_straight_sql_text)
            
            # 记录快速模板匹配结果
            if status:
                _async_log_util.info(f"[快速模板匹配] 匹配成功 - 用户问题: {self.chat_question.question[:100]}, 匹配模板ID: {matched_id}, 匹配参数: {infos}")
            else:
                _async_log_util.info(f"[快速模板匹配] 未匹配 - 用户问题: {self.chat_question.question[:100]}, 将使用正常SQL生成流程")
            
            double_check_ok = False
            if status:
                double_check_ok = self.double_check_straight_sql_info(matched_id, infos, training_data)
                if double_check_ok:
                    _async_log_util.info(f"[快速模板匹配] 二次校验通过 - 模板ID: {matched_id}")
                else:
                    # 二次校验未通过时仍尝试使用快速模板生成SQL，只有实际生成失败才回退到正常流程
                    _async_log_util.info(f"[快速模板匹配] 二次校验未通过，仍尝试使用快速模板生成SQL - 模板ID: {matched_id}")
            
            chart_type = ""
            if status:
                # 尝试使用快速模板（无论二次校验是否通过，只要首次匹配成功就尝试）
                try:
                    full_sql_text = self.generate_straight_sql(training_data, full_straight_sql_text)
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
                _async_log_util.info(f"[SQL生成] 使用正常SQL生成流程 - 用户问题: {self.chat_question.question[:100]}")
                # generate sql
                sql_res = self.generate_sql()
                full_sql_text = ''
                for chunk in sql_res:
                    full_sql_text += chunk.get('content')
                    if in_chat:
                        yield 'data:' + orjson.dumps(
                            {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                             'type': 'sql-result'}).decode() + '\n\n'
                # filter sql
                # 优化：只在DEBUG模式下记录完整SQL文本
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
            result = self.execute_sql(sql=real_execute_sql)
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
            
            # generate chart
            chart_res = self.generate_chart(chart_type)
            full_chart_text = ''
            for chunk in chart_res:
                full_chart_text += chunk.get('content')
                if in_chat:
                    yield 'data:' + orjson.dumps(
                        {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                         'type': 'chart-result'}).decode() + '\n\n'
            # filter chart
            # 优化：只在DEBUG模式下记录详细日志
            if settings.LOG_LEVEL == "DEBUG":
                _async_log_util.info(full_chart_text)
            chart = self.check_save_chart(res=full_chart_text)
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
                    yield 'data:' + orjson.dumps(
                        {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                         'type': 'analysis-result'}).decode() + '\n\n'
            
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
            _async_log_util.exception("LLM task failed")
            error_msg: str
            if isinstance(e, SingleMessageError):
                error_msg = str(e)
            elif isinstance(e, SQLBotDBConnectionError):
                error_msg = orjson.dumps(
                    {'message': str(e), 'type': 'db-connection-err'}).decode()
            elif isinstance(e, SQLBotDBError):
                error_msg = orjson.dumps(
                    {'message': 'Execute SQL Failed', 'traceback': str(e), 'type': 'exec-sql-err'}).decode()
            else:
                error_msg = orjson.dumps({'message': str(e), 'traceback': traceback.format_exc(limit=1)}).decode()
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
            self.finish()

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
                    yield 'data:' + orjson.dumps(
                        {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                         'type': 'analysis-result'}).decode() + '\n\n'
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
            error_msg: str
            if isinstance(e, SingleMessageError):
                error_msg = str(e)
            else:
                error_msg = orjson.dumps({'message': str(e), 'traceback': traceback.format_exc(limit=1)}).decode()
            self.save_error(message=error_msg)
            yield 'data:' + orjson.dumps({'content': error_msg, 'type': 'error'}).decode() + '\n\n'
        finally:
            # end
            pass

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
        content = chunk.content
        output_content = ''  # 实际要输出的内容

        # 检查additional_kwargs中的reasoning_content
        if 'reasoning_content' in chunk.additional_kwargs:
            reasoning_content = chunk.additional_kwargs.get('reasoning_content', '')
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
