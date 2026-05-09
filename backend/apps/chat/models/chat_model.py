from datetime import datetime
from enum import Enum
from typing import List, Optional

from fastapi import Body
from pydantic import BaseModel
from sqlalchemy import Column, Integer, Text, BigInteger, DateTime, Identity, Boolean
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel, Field

from apps.template.filter.generator import get_permissions_template
from apps.template.generate_analysis.generator import get_analysis_template
from apps.template.generate_chart.generator import get_chart_template
from apps.template.generate_dynamic.generator import get_dynamic_template
from apps.template.generate_guess_question.generator import get_guess_question_template
from apps.template.generate_predict.generator import get_predict_template
from apps.template.generate_sql.generator import get_sql_template
from apps.template.select_datasource.generator import get_datasource_template


def enum_values(enum_class: type[Enum]) -> list:
    """Get values for enum."""
    return [status.value for status in enum_class]


class TypeEnum(Enum):
    CHAT = "0"


#     TODO other usage

class OperationEnum(Enum):
    GENERATE_SQL = '0'
    GENERATE_CHART = '1'
    ANALYSIS = '2'
    PREDICT_DATA = '3'
    GENERATE_RECOMMENDED_QUESTIONS = '4'
    GENERATE_SQL_WITH_PERMISSIONS = '5'
    CHOOSE_DATASOURCE = '6'
    GENERATE_DYNAMIC_SQL = '7'


class ChatFinishStep(Enum):
    GENERATE_SQL = 1
    QUERY_DATA = 2
    GENERATE_CHART = 3


#     TODO choose table / check connection / generate description

class ChatLog(SQLModel, table=True):
    __tablename__ = "chat_log"
    id: Optional[int] = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    type: TypeEnum = Field(
        sa_column=Column(SQLAlchemyEnum(TypeEnum, native_enum=False, values_callable=enum_values, length=3)))
    operate: OperationEnum = Field(
        sa_column=Column(SQLAlchemyEnum(OperationEnum, native_enum=False, values_callable=enum_values, length=3)))
    pid: Optional[int] = Field(sa_column=Column(BigInteger, nullable=True))
    ai_modal_id: Optional[int] = Field(sa_column=Column(BigInteger))
    base_modal: Optional[str] = Field(max_length=255)
    messages: Optional[list[dict]] = Field(sa_column=Column(JSONB))
    reasoning_content: Optional[str | None] = Field(sa_column=Column(Text, nullable=True))
    start_time: datetime = Field(sa_column=Column(DateTime(timezone=False), nullable=True))
    finish_time: datetime = Field(sa_column=Column(DateTime(timezone=False), nullable=True))
    token_usage: Optional[dict | None | int] = Field(sa_column=Column(JSONB))


class Chat(SQLModel, table=True):
    __tablename__ = "chat"
    id: Optional[int] = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    oid: Optional[int] = Field(sa_column=Column(BigInteger, nullable=True, default=1))
    create_time: datetime = Field(sa_column=Column(DateTime(timezone=False), nullable=True))
    create_by: int = Field(sa_column=Column(BigInteger, nullable=True))
    brief: str = Field(max_length=64, nullable=True)
    chat_type: str = Field(max_length=20, default="chat")  # chat, datasource
    datasource: int = Field(sa_column=Column(BigInteger, nullable=True))
    engine_type: str = Field(max_length=64)
    origin: Optional[int] = Field(
        sa_column=Column(Integer, nullable=False, default=0))  # 0: default, 1: mcp, 2: assistant


class ChatRecord(SQLModel, table=True):
    __tablename__ = "chat_record"
    id: Optional[int] = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    chat_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    ai_modal_id: Optional[int] = Field(sa_column=Column(BigInteger))
    first_chat: bool = Field(sa_column=Column(Boolean, nullable=True, default=False))
    create_time: datetime = Field(sa_column=Column(DateTime(timezone=False), nullable=True))
    finish_time: datetime = Field(sa_column=Column(DateTime(timezone=False), nullable=True))
    create_by: int = Field(sa_column=Column(BigInteger, nullable=True))
    datasource: int = Field(sa_column=Column(BigInteger, nullable=True))
    engine_type: str = Field(max_length=64, nullable=True)
    question: str = Field(sa_column=Column(Text, nullable=True))
    sql_answer: str = Field(sa_column=Column(Text, nullable=True))
    sql: str = Field(sa_column=Column(Text, nullable=True))
    sql_exec_result: str = Field(sa_column=Column(Text, nullable=True))
    data: str = Field(sa_column=Column(Text, nullable=True))
    chart_answer: str = Field(sa_column=Column(Text, nullable=True))
    chart: str = Field(sa_column=Column(Text, nullable=True))
    analysis: str = Field(sa_column=Column(Text, nullable=True))
    predict: str = Field(sa_column=Column(Text, nullable=True))
    predict_data: str = Field(sa_column=Column(Text, nullable=True))
    recommended_question_answer: str = Field(sa_column=Column(Text, nullable=True))
    recommended_question: str = Field(sa_column=Column(Text, nullable=True))
    datasource_select_answer: str = Field(sa_column=Column(Text, nullable=True))
    finish: bool = Field(sa_column=Column(Boolean, nullable=True, default=False))
    error: str = Field(sa_column=Column(Text, nullable=True))
    analysis_record_id: int = Field(sa_column=Column(BigInteger, nullable=True))
    predict_record_id: int = Field(sa_column=Column(BigInteger, nullable=True))


# 点踩反馈原因：查询无结果 / 数据不准确 / 分析过程有误
class ErrorQueryFeedbackReason:
    NO_RESULT = "no_result"           # 查询无结果
    INACCURATE_DATA = "inaccurate_data"  # 查询到的数据不准确
    WRONG_ANALYSIS = "wrong_analysis"     # 分析过程有误


class ErrorQueryRecord(SQLModel, table=True):
    """反馈空间：用户点踩后写入，供运维在系统管理中查看与标记处理状态。"""
    __tablename__ = "error_query_record"
    id: Optional[int] = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    record_id: int = Field(sa_column=Column(BigInteger, nullable=False), description="关联的 chat_record.id")
    chat_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    question: str = Field(sa_column=Column(Text, nullable=True), description="用户提出的问题")
    sql: str = Field(sa_column=Column(Text, nullable=True), description="生成的 SQL（仅当反馈原因为查询无结果/数据不准确时记录）")
    analysis_text: str = Field(sa_column=Column(Text, nullable=True), description="数据分析内容（仅当反馈原因为分析过程有误时记录）")
    error_message: str = Field(sa_column=Column(Text, nullable=True), description="报错信息")
    feedback_reason: str = Field(sa_column=Column(Text, nullable=True), description="点踩原因: no_result | inaccurate_data | wrong_analysis")
    status: str = Field(sa_column=Column(Text, nullable=True, default="pending"), description="处理状态: pending | resolved")
    create_by: int = Field(sa_column=Column(BigInteger, nullable=True))
    create_time: datetime = Field(sa_column=Column(DateTime(timezone=False), nullable=True))


class ChatExecutionTraceStatus:
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


class ChatExecutionTrace(SQLModel, table=True):
    __tablename__ = "chat_execution_trace"
    id: Optional[int] = Field(sa_column=Column(BigInteger, Identity(always=False), primary_key=True))
    record_id: int = Field(sa_column=Column(BigInteger, nullable=False), description="关联的 chat_record.id")
    chat_id: int = Field(sa_column=Column(BigInteger, nullable=False), description="关联的 chat.id")
    create_by: int = Field(sa_column=Column(BigInteger, nullable=True), description="触发该链路的用户")
    trace_group: str = Field(max_length=64, nullable=False, description="同一次请求链路的分组标识")
    node_key: str = Field(max_length=64, nullable=False, description="节点英文标识")
    node_name: str = Field(max_length=128, nullable=False, description="节点展示名称")
    status: str = Field(max_length=20, nullable=False, default=ChatExecutionTraceStatus.RUNNING)
    input_payload: Optional[dict] = Field(sa_column=Column(JSONB, nullable=True))
    output_payload: Optional[dict] = Field(sa_column=Column(JSONB, nullable=True))
    extra_data: Optional[dict] = Field(sa_column=Column(JSONB, nullable=True))
    error_message: Optional[str] = Field(sa_column=Column(Text, nullable=True))
    start_time: datetime = Field(sa_column=Column(DateTime(timezone=False), nullable=True))
    finish_time: Optional[datetime] = Field(sa_column=Column(DateTime(timezone=False), nullable=True))
    duration_ms: Optional[int] = Field(sa_column=Column(Integer, nullable=True))


class ChatRecordResult(BaseModel):
    id: Optional[int] = None
    chat_id: Optional[int] = None
    ai_modal_id: Optional[int] = None
    first_chat: bool = False
    create_time: Optional[datetime] = None
    finish_time: Optional[datetime] = None
    question: Optional[str] = None
    sql_answer: Optional[str] = None
    sql: Optional[str] = None
    data: Optional[str] = None
    chart_answer: Optional[str] = None
    chart: Optional[str] = None
    analysis: Optional[str] = None
    predict: Optional[str] = None
    predict_data: Optional[str] = None
    recommended_question: Optional[str] = None
    datasource_select_answer: Optional[str] = None
    finish: Optional[bool] = None
    error: Optional[str] = None
    analysis_record_id: Optional[int] = None
    predict_record_id: Optional[int] = None
    sql_reasoning_content: Optional[str] = None
    chart_reasoning_content: Optional[str] = None
    analysis_reasoning_content: Optional[str] = None
    predict_reasoning_content: Optional[str] = None


class CreateChat(BaseModel):
    id: int = None
    question: str = None
    datasource: int = None
    origin: Optional[int] = 0  # 0是页面上，mcp是1，小助手是2


class RenameChat(BaseModel):
    id: int = None
    brief: str = ''


class ChatInfo(BaseModel):
    id: Optional[int] = None
    create_time: datetime = None
    create_by: int = None
    brief: str = ''
    chat_type: str = "chat"
    datasource: Optional[int] = None
    engine_type: str = ''
    ds_type: str = ''
    datasource_name: str = ''
    datasource_exists: bool = True
    records: List[ChatRecord | dict] = []


class ChatExecutionTraceResult(BaseModel):
    id: Optional[int] = None
    record_id: int
    chat_id: int
    create_by: Optional[int] = None
    trace_group: str
    node_key: str
    node_name: str
    status: str
    input_payload: Optional[dict] = None
    output_payload: Optional[dict] = None
    extra_data: Optional[dict] = None
    error_message: Optional[str] = None
    start_time: Optional[datetime] = None
    finish_time: Optional[datetime] = None
    duration_ms: Optional[int] = None


class AiModelQuestion(BaseModel):
    question: str = None
    ai_modal_id: int = None
    ai_modal_name: str = None  # Specific model name
    engine: str = ""
    db_schema: str = ""
    sql: str = ""
    rule: str = ""
    fields: str = ""
    data: str = ""
    lang: str = "简体中文"
    filter: str = []
    sub_query: Optional[list[dict]] = None
    terminologies: str = ""
    data_training: str = ""
    custom_prompt: str = ""
    error_msg: str = ""
    data_total_rows: str = ""  # 数据分析用：SQL 结果总行数，避免上下文截断导致模型误计
    data_summary: str = ""  # 数据分析用：系统预计算的统计量（总数、分项明细等），避免模型遗漏

    def sql_sys_question(self):
        return get_sql_template()['system'].format(engine=self.engine, schema=self.db_schema, question=self.question,
                                                   lang=self.lang, terminologies=self.terminologies,
                                                   data_training=self.data_training, custom_prompt=self.custom_prompt)

    def sql_straight_question(self):
        return get_sql_template()['straight'].format(engine=self.engine, schema=self.db_schema, question=self.question,
                                                   lang=self.lang, terminologies=self.terminologies,
                                                   data_training=self.data_training, custom_prompt=self.custom_prompt)

    def sql_rewrite_question(self):
        return get_sql_template()['rewrite'].format(
            engine=self.engine,
            schema=self.db_schema,
            question=self.question,
            lang=self.lang,
            terminologies=self.terminologies,
            custom_prompt=self.custom_prompt
        )
    @staticmethod
    def double_check_question(template_id, template_question, sql_template, sql_info, user_sql_info):
        return get_sql_template()['double_check'].format(
            template_id=template_id,
            template_question=template_question,
            sql_template=sql_template,
            sql_info=sql_info,
            user_sql_info=user_sql_info,
        )


    def sql_straight_template_question(self):
        return get_sql_template()['straight_template'].format(question=self.question)

    def sql_user_question(self, current_time: str):
        return get_sql_template()['user'].format(engine=self.engine, schema=self.db_schema, question=self.question,
                                                 rule=self.rule, current_time=current_time, error_msg=self.error_msg)

    def chart_sys_question(self):
        return get_chart_template()['system'].format(sql=self.sql, question=self.question, lang=self.lang)

    def chart_user_question(self, chart_type: Optional[str] = None):
        return get_chart_template()['user'].format(sql=self.sql, question=self.question, rule=self.rule,
                                                   chart_type=chart_type)

    def analysis_sys_question(self):
        return get_analysis_template()['system'].format(lang=self.lang, terminologies=self.terminologies,
                                                        custom_prompt=self.custom_prompt)

    def analysis_user_question(self):
        # 构建SQL和时间范围信息（如果有）
        sql_info = ""
        if self.sql:
            sql_info = f"\n<sql>\n{self.sql}\n</sql>"
        data_total_rows = self.data_total_rows if self.data_total_rows else ""
        data_summary = self.data_summary if self.data_summary else ""
        return get_analysis_template()['user'].format(
            question=self.question, fields=self.fields, data=self.data, sql_info=sql_info,
            data_total_rows=data_total_rows, data_summary=data_summary
        )

    def predict_sys_question(self):
        return get_predict_template()['system'].format(lang=self.lang, custom_prompt=self.custom_prompt)

    def predict_user_question(self):
        return get_predict_template()['user'].format(fields=self.fields, data=self.data)

    def datasource_sys_question(self):
        return get_datasource_template()['system'].format(lang=self.lang)

    def datasource_user_question(self, datasource_list: str = "[]"):
        return get_datasource_template()['user'].format(question=self.question, data=datasource_list)

    def guess_sys_question(self):
        # 容错：system 模板中若包含 {schema}/{question}/{old_questions} 等占位符，避免 KeyError 中断
        class _SafeDict(dict):
            def __missing__(self, key):
                return "{" + key + "}"

        tpl = get_guess_question_template()['system']
        try:
            return tpl.format_map(_SafeDict(lang=self.lang))
        except Exception:
            # 未转义花括号等模板语法问题兜底
            return tpl

    def guess_user_question(self, old_questions: str = "[]"):
        tpl = get_guess_question_template()['user']
        try:
            return tpl.format(question=self.question, schema=self.db_schema, old_questions=old_questions)
        except Exception:
            # 兜底：避免模板格式问题导致猜你想问失败
            return (tpl
                    .replace("{question}", self.question or "")
                    .replace("{schema}", self.db_schema or "")
                    .replace("{old_questions}", old_questions or "[]"))

    def filter_sys_question(self):
        return get_permissions_template()['system'].format(lang=self.lang, engine=self.engine)

    def filter_user_question(self):
        return get_permissions_template()['user'].format(sql=self.sql, filter=self.filter)

    def dynamic_sys_question(self):
        return get_dynamic_template()['system'].format(lang=self.lang, engine=self.engine)

    def dynamic_user_question(self):
        return get_dynamic_template()['user'].format(sql=self.sql, sub_query=self.sub_query)


class ChatQuestion(AiModelQuestion):
    chat_id: int


class ChatMcp(ChatQuestion):
    token: str


class ChatStart(BaseModel):
    username: str = Body(description='用户名')
    password: str = Body(description='密码')


class McpQuestion(BaseModel):
    question: str = Body(description='用户提问')
    chat_id: int = Body(description='会话ID')
    token: str = Body(description='token')
    stream: Optional[bool] = Body(description='是否流式输出，默认为true开启, 关闭false则返回JSON对象', default=True)


class AxisObj(BaseModel):
    name: str = ''
    value: str = ''
    type: str | None = None


class ExcelData(BaseModel):
    axis: list[AxisObj] = []
    data: list[dict] = []
    name: str = 'Excel'


class McpAssistant(BaseModel):
    question: str = Body(description='用户提问')
    url: str = Body(description='第三方数据接口')
    authorization: str = Body(description='第三方接口凭证')
    stream: Optional[bool] = Body(description='是否流式输出，默认为true开启, 关闭false则返回JSON对象', default=True)
