import datetime
from typing import List

import orjson
import sqlparse
from sqlalchemy import and_, select, update
from sqlalchemy.orm import aliased

from apps.chat.models.chat_model import Chat, ChatRecord, CreateChat, ChatInfo, RenameChat, ChatQuestion, ChatLog, \
    TypeEnum, OperationEnum, ChatRecordResult, ErrorQueryRecord
from apps.datasource.models.datasource import CoreDatasource
from apps.system.crud.assistant import AssistantOutDsFactory
from common.core.deps import CurrentAssistant, SessionDep, CurrentUser
from common.utils.utils import extract_nested_json



def get_chat_record_by_id(session: SessionDep, record_id: int, current_user: CurrentUser):
    record: ChatRecord | None = None

    stmt = select(
        ChatRecord.id,
        ChatRecord.question,
        ChatRecord.chat_id,
        ChatRecord.datasource,
        ChatRecord.engine_type,
        ChatRecord.ai_modal_id,
        ChatRecord.create_by,
        ChatRecord.sql,
    ).where(
        and_(ChatRecord.id == record_id, ChatRecord.create_by == current_user.id)
    )
    result = session.execute(stmt)
    for r in result:
        record = ChatRecord(
            id=r.id,
            question=r.question,
            chat_id=r.chat_id,
            datasource=r.datasource,
            engine_type=r.engine_type,
            ai_modal_id=r.ai_modal_id,
            create_by=r.create_by,
            sql=r.sql,
        )
    return record


def list_chats(session: SessionDep, current_user: CurrentUser) -> List[Chat]:
    oid = current_user.oid if current_user.oid is not None else 1
    chart_list = session.query(Chat).filter(and_(Chat.create_by == current_user.id, Chat.oid == oid)).order_by(
        Chat.create_time.desc()).all()
    return chart_list


def rename_chat(session: SessionDep, rename_object: RenameChat, current_user: CurrentUser) -> str:
    chat = session.get(Chat, rename_object.id)
    if not chat:
        raise Exception(f"Chat with id {rename_object.id} not found")
    
    # 检查权限：只有创建者才能重命名
    if chat.create_by != current_user.id:
        raise Exception(f"Permission denied: You don't have permission to rename this chat")

    chat.brief = rename_object.brief.strip()[:20]
    session.add(chat)
    session.flush()
    session.refresh(chat)

    brief = chat.brief
    session.commit()
    return brief


def delete_chat(session, chart_id, current_user: CurrentUser) -> str:
    chat = session.query(Chat).filter(Chat.id == chart_id).first()
    if not chat:
        return f'Chat with id {chart_id} has been deleted'
    
    # 检查权限：只有创建者才能删除
    if chat.create_by != current_user.id:
        raise Exception(f"Permission denied: You don't have permission to delete this chat")

    session.delete(chat)
    session.commit()

    return f'Chat with id {chart_id} has been deleted'


def get_chart_config(session: SessionDep, chart_record_id: int, current_user: CurrentUser):
    stmt = select(ChatRecord.chart).where(
        and_(ChatRecord.id == chart_record_id, ChatRecord.create_by == current_user.id))
    res = session.execute(stmt)
    for row in res:
        try:
            return orjson.loads(row.chart)
        except Exception:
            pass
    return {}


def get_last_execute_sql_error(session: SessionDep, chart_id: int, current_user: CurrentUser):
    # 首先检查 chat 是否属于当前用户
    chat = session.get(Chat, chart_id)
    if not chat or chat.create_by != current_user.id:
        return None
    
    stmt = select(ChatRecord.error).where(
        and_(ChatRecord.chat_id == chart_id, ChatRecord.create_by == current_user.id)).order_by(
        ChatRecord.create_time.desc()).limit(1)
    res = session.execute(stmt).scalar()
    if res:
        try:
            obj = orjson.loads(res)
            if obj.get('type') and obj.get('type') == 'exec-sql-err':
                return obj.get('traceback')
        except Exception:
            pass

    return None


def get_chat_chart_data(session: SessionDep, chart_record_id: int, current_user: CurrentUser):
    stmt = select(ChatRecord.data).where(
        and_(ChatRecord.id == chart_record_id, ChatRecord.create_by == current_user.id))
    res = session.execute(stmt)
    for row in res:
        try:
            return orjson.loads(row.data)
        except Exception:
            pass
    return []


def get_chat_predict_data(session: SessionDep, chart_record_id: int, current_user: CurrentUser):
    stmt = select(ChatRecord.predict_data).where(
        and_(ChatRecord.id == chart_record_id, ChatRecord.create_by == current_user.id))
    res = session.execute(stmt)
    for row in res:
        try:
            return orjson.loads(row.predict_data)
        except Exception:
            pass
    return []


def get_chat_with_records_with_data(session: SessionDep, chart_id: int, current_user: CurrentUser,
                                    current_assistant: CurrentAssistant) -> ChatInfo:
    return get_chat_with_records(session, chart_id, current_user, current_assistant, True)


dynamic_ds_types = [1, 3]


def get_chat_with_records(session: SessionDep, chart_id: int, current_user: CurrentUser,
                          current_assistant: CurrentAssistant, with_data: bool = False) -> ChatInfo:
    chat = session.get(Chat, chart_id)
    if not chat:
        raise Exception(f"Chat with id {chart_id} not found")
    
    # 检查权限：只有创建者才能查看
    if chat.create_by != current_user.id:
        raise Exception(f"Permission denied: You don't have permission to access this chat")

    chat_info = ChatInfo(**chat.model_dump())

    if current_assistant and current_assistant.type in dynamic_ds_types:
        out_ds_instance = AssistantOutDsFactory.get_instance(current_assistant)
        ds = out_ds_instance.get_ds(chat.datasource)
    else:
        ds = session.get(CoreDatasource, chat.datasource) if chat.datasource else None

    if not ds:
        chat_info.datasource_exists = False
        chat_info.datasource_name = 'Datasource not exist'
    else:
        chat_info.datasource_exists = True
        chat_info.datasource_name = ds.name
        chat_info.ds_type = ds.type

    sql_alias_log = aliased(ChatLog)
    chart_alias_log = aliased(ChatLog)
    analysis_alias_log = aliased(ChatLog)
    predict_alias_log = aliased(ChatLog)

    stmt = (select(ChatRecord.id, ChatRecord.chat_id, ChatRecord.create_time, ChatRecord.finish_time,
                   ChatRecord.question, ChatRecord.sql_answer, ChatRecord.sql,
                   ChatRecord.chart_answer, ChatRecord.chart, ChatRecord.analysis, ChatRecord.predict,
                   ChatRecord.datasource_select_answer, ChatRecord.analysis_record_id, ChatRecord.predict_record_id,
                   ChatRecord.recommended_question, ChatRecord.first_chat,
                   ChatRecord.finish, ChatRecord.error,
                   sql_alias_log.reasoning_content.label('sql_reasoning_content'),
                   chart_alias_log.reasoning_content.label('chart_reasoning_content'),
                   analysis_alias_log.reasoning_content.label('analysis_reasoning_content'),
                   predict_alias_log.reasoning_content.label('predict_reasoning_content')
                   )
    .outerjoin(sql_alias_log, and_(sql_alias_log.pid == ChatRecord.id,
                                   sql_alias_log.type == TypeEnum.CHAT,
                                   sql_alias_log.operate == OperationEnum.GENERATE_SQL))
    .outerjoin(chart_alias_log, and_(chart_alias_log.pid == ChatRecord.id,
                                     chart_alias_log.type == TypeEnum.CHAT,
                                     chart_alias_log.operate == OperationEnum.GENERATE_CHART))
    .outerjoin(analysis_alias_log, and_(analysis_alias_log.pid == ChatRecord.id,
                                        analysis_alias_log.type == TypeEnum.CHAT,
                                        analysis_alias_log.operate == OperationEnum.ANALYSIS))
    .outerjoin(predict_alias_log, and_(predict_alias_log.pid == ChatRecord.id,
                                       predict_alias_log.type == TypeEnum.CHAT,
                                       predict_alias_log.operate == OperationEnum.PREDICT_DATA))
    .where(and_(ChatRecord.create_by == current_user.id, ChatRecord.chat_id == chart_id)).order_by(
        ChatRecord.create_time))
    if with_data:
        stmt = select(ChatRecord.id, ChatRecord.chat_id, ChatRecord.create_time, ChatRecord.finish_time,
                      ChatRecord.question, ChatRecord.sql_answer, ChatRecord.sql,
                      ChatRecord.chart_answer, ChatRecord.chart, ChatRecord.analysis, ChatRecord.predict,
                      ChatRecord.datasource_select_answer, ChatRecord.analysis_record_id, ChatRecord.predict_record_id,
                      ChatRecord.recommended_question, ChatRecord.first_chat,
                      ChatRecord.finish, ChatRecord.error, ChatRecord.data, ChatRecord.predict_data).where(
            and_(ChatRecord.create_by == current_user.id, ChatRecord.chat_id == chart_id)).order_by(
            ChatRecord.create_time)

    result = session.execute(stmt).all()
    record_list: list[ChatRecordResult] = []
    for row in result:
        if not with_data:
            record_list.append(
                ChatRecordResult(id=row.id, chat_id=row.chat_id, create_time=row.create_time,
                                 finish_time=row.finish_time,
                                 question=row.question, sql_answer=row.sql_answer, sql=row.sql,
                                 chart_answer=row.chart_answer, chart=row.chart,
                                 analysis=row.analysis, predict=row.predict,
                                 datasource_select_answer=row.datasource_select_answer,
                                 analysis_record_id=row.analysis_record_id, predict_record_id=row.predict_record_id,
                                 recommended_question=row.recommended_question, first_chat=row.first_chat,
                                 finish=row.finish, error=row.error,
                                 sql_reasoning_content=row.sql_reasoning_content,
                                 chart_reasoning_content=row.chart_reasoning_content,
                                 analysis_reasoning_content=row.analysis_reasoning_content,
                                 predict_reasoning_content=row.predict_reasoning_content,
                                 ))
        else:
            record_list.append(
                ChatRecordResult(id=row.id, chat_id=row.chat_id, create_time=row.create_time,
                                 finish_time=row.finish_time,
                                 question=row.question, sql_answer=row.sql_answer, sql=row.sql,
                                 chart_answer=row.chart_answer, chart=row.chart,
                                 analysis=row.analysis, predict=row.predict,
                                 datasource_select_answer=row.datasource_select_answer,
                                 analysis_record_id=row.analysis_record_id, predict_record_id=row.predict_record_id,
                                 recommended_question=row.recommended_question, first_chat=row.first_chat,
                                 finish=row.finish, error=row.error, data=row.data, predict_data=row.predict_data))

    result = list(map(format_record, record_list))

    chat_info.records = result

    return chat_info


def format_record(record: ChatRecordResult):
    _dict = record.model_dump()

    if record.sql_answer and record.sql_answer.strip() != '' and record.sql_answer.strip()[0] == '{' and \
            record.sql_answer.strip()[-1] == '}':
        _obj = orjson.loads(record.sql_answer)
        _dict['sql_answer'] = _obj.get('reasoning_content')
    if record.sql_reasoning_content and record.sql_reasoning_content.strip() != '':
        _dict['sql_answer'] = record.sql_reasoning_content
    if record.chart_answer and record.chart_answer.strip() != '' and record.chart_answer.strip()[0] == '{' and \
            record.chart_answer.strip()[-1] == '}':
        _obj = orjson.loads(record.chart_answer)
        _dict['chart_answer'] = _obj.get('reasoning_content')
    if record.chart_reasoning_content and record.chart_reasoning_content.strip() != '':
        _dict['chart_answer'] = record.chart_reasoning_content
    if record.analysis and record.analysis.strip() != '' and record.analysis.strip()[0] == '{' and \
            record.analysis.strip()[-1] == '}':
        _obj = orjson.loads(record.analysis)
        _dict['analysis_thinking'] = _obj.get('reasoning_content')
        _dict['analysis'] = _obj.get('content')
    if record.analysis_reasoning_content and record.analysis_reasoning_content.strip() != '':
        _dict['analysis_thinking'] = record.analysis_reasoning_content
    if record.predict and record.predict.strip() != '' and record.predict.strip()[0] == '{' and record.predict.strip()[
        -1] == '}':
        _obj = orjson.loads(record.predict)
        _dict['predict'] = _obj.get('reasoning_content')
        _dict['predict_content'] = _obj.get('content')
    if record.predict_reasoning_content and record.predict_reasoning_content.strip() != '':
        _dict['predict'] = record.predict_reasoning_content
    if record.data and record.data.strip() != '':
        try:
            _obj = orjson.loads(record.data)
            _dict['data'] = _obj
        except Exception:
            pass
    if record.predict_data and record.predict_data.strip() != '':
        try:
            _obj = orjson.loads(record.predict_data)
            _dict['predict_data'] = _obj
        except Exception:
            pass
    if record.sql and record.sql.strip() != '':
        try:
            _dict['sql'] = sqlparse.format(record.sql, reindent=True)
        except Exception:
            pass

    return _dict


def list_generate_sql_logs(session: SessionDep, chart_id: int, current_user: CurrentUser) -> List[ChatLog]:
    # 首先检查 chat 是否属于当前用户
    chat = session.get(Chat, chart_id)
    if not chat or chat.create_by != current_user.id:
        return []
    
    stmt = select(ChatLog).where(
        and_(ChatLog.pid.in_(select(ChatRecord.id).where(
            and_(ChatRecord.chat_id == chart_id, ChatRecord.create_by == current_user.id))),
             ChatLog.type == TypeEnum.CHAT, ChatLog.operate == OperationEnum.GENERATE_SQL)).order_by(
        ChatLog.start_time)
    result = session.execute(stmt).all()
    _list = []
    for row in result:
        for r in row:
            _list.append(ChatLog(**r.model_dump()))
    return _list


def list_generate_chart_logs(session: SessionDep, chart_id: int, current_user: CurrentUser) -> List[ChatLog]:
    # 首先检查 chat 是否属于当前用户
    chat = session.get(Chat, chart_id)
    if not chat or chat.create_by != current_user.id:
        return []
    
    stmt = select(ChatLog).where(
        and_(ChatLog.pid.in_(select(ChatRecord.id).where(
            and_(ChatRecord.chat_id == chart_id, ChatRecord.create_by == current_user.id))),
             ChatLog.type == TypeEnum.CHAT, ChatLog.operate == OperationEnum.GENERATE_CHART)).order_by(
        ChatLog.start_time)
    result = session.execute(stmt).all()
    _list = []
    for row in result:
        for r in row:
            _list.append(ChatLog(**r.model_dump()))
    return _list


def create_chat(session: SessionDep, current_user: CurrentUser, create_chat_obj: CreateChat,
                require_datasource: bool = True) -> ChatInfo:
    if not create_chat_obj.datasource and require_datasource:
        raise Exception("Datasource cannot be None")

    if not create_chat_obj.question or create_chat_obj.question.strip() == '':
        create_chat_obj.question = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    chat = Chat(create_time=datetime.datetime.now(),
                create_by=current_user.id,
                oid=current_user.oid if current_user.oid is not None else 1,
                brief=create_chat_obj.question.strip()[:20],
                origin=create_chat_obj.origin if create_chat_obj.origin is not None else 0)
    ds: CoreDatasource | None = None
    if create_chat_obj.datasource:
        chat.datasource = create_chat_obj.datasource
        ds = session.get(CoreDatasource, create_chat_obj.datasource)

        if not ds:
            raise Exception(f"Datasource with id {create_chat_obj.datasource} not found")

        chat.engine_type = ds.type_name
    else:
        chat.engine_type = ''

    chat_info = ChatInfo(**chat.model_dump())

    session.add(chat)
    session.flush()
    session.refresh(chat)
    chat_info.id = chat.id
    session.commit()

    if ds:
        chat_info.datasource_exists = True
        chat_info.datasource_name = ds.name
        chat_info.ds_type = ds.type

    if require_datasource and ds:
        # generate first empty record
        record = ChatRecord()
        record.chat_id = chat.id
        record.datasource = ds.id
        record.engine_type = ds.type_name
        record.first_chat = True
        record.finish = True
        record.create_time = datetime.datetime.now()
        record.create_by = current_user.id

        _record = ChatRecord(**record.model_dump())

        session.add(record)
        session.flush()
        session.refresh(record)
        _record.id = record.id
        session.commit()

        chat_info.records.append(_record)

    return chat_info


def save_question(session: SessionDep, current_user: CurrentUser, question: ChatQuestion) -> ChatRecord:
    if not question.chat_id:
        raise Exception("ChatId cannot be None")
    if not question.question or question.question.strip() == '':
        raise Exception("Question cannot be Empty")

    # chat = session.query(Chat).filter(Chat.id == question.chat_id).first()
    chat: Chat = session.get(Chat, question.chat_id)
    if not chat:
        raise Exception(f"Chat with id {question.chat_id} not found")
    
    # 检查权限：只有创建者才能添加问题
    if chat.create_by != current_user.id:
        raise Exception(f"Permission denied: You don't have permission to add question to this chat")

    record = ChatRecord()
    record.question = question.question
    record.chat_id = chat.id
    record.create_time = datetime.datetime.now()
    record.create_by = current_user.id
    record.datasource = chat.datasource
    record.engine_type = chat.engine_type
    record.ai_modal_id = question.ai_modal_id

    result = ChatRecord(**record.model_dump())

    session.add(record)
    session.flush()
    session.refresh(record)
    result.id = record.id
    session.commit()

    return result


def save_analysis_predict_record(session: SessionDep, base_record: ChatRecord, action_type: str) -> ChatRecord:
    record = ChatRecord()
    record.question = base_record.question
    record.chat_id = base_record.chat_id
    record.datasource = base_record.datasource
    record.engine_type = base_record.engine_type
    record.ai_modal_id = base_record.ai_modal_id
    record.create_time = datetime.datetime.now()
    record.create_by = base_record.create_by
    record.chart = base_record.chart
    record.data = base_record.data

    if action_type == 'analysis':
        record.analysis_record_id = base_record.id
    elif action_type == 'predict':
        record.predict_record_id = base_record.id

    result = ChatRecord(**record.model_dump())

    session.add(record)
    session.flush()
    session.refresh(record)
    result.id = record.id
    session.commit()

    return result


def start_log(session: SessionDep, ai_modal_id: int, ai_modal_name: str, operate: OperationEnum, record_id: int,
              full_message: list[dict]) -> ChatLog:
    log = ChatLog(type=TypeEnum.CHAT, operate=operate, pid=record_id, ai_modal_id=ai_modal_id, base_modal=ai_modal_name,
                  messages=full_message, start_time=datetime.datetime.now())

    result = ChatLog(**log.model_dump())

    session.add(log)
    session.flush()
    session.refresh(log)
    result.id = log.id
    session.commit()

    return result


def end_log(session: SessionDep, log: ChatLog, full_message: list[dict], reasoning_content: str = None,
            token_usage=None) -> ChatLog:
    if token_usage is None:
        token_usage = {}
    log.messages = full_message
    log.token_usage = token_usage
    log.finish_time = datetime.datetime.now()
    log.reasoning_content = reasoning_content if reasoning_content and len(reasoning_content.strip()) > 0 else None

    stmt = update(ChatLog).where(and_(ChatLog.id == log.id)).values(
        messages=log.messages,
        token_usage=log.token_usage,
        finish_time=log.finish_time,
        reasoning_content=log.reasoning_content
    )
    session.execute(stmt)
    session.commit()

    return log


def save_sql_answer(session: SessionDep, record_id: int, answer: str, current_user: CurrentUser) -> ChatRecord:
    if not record_id:
        raise Exception("Record id cannot be None")
    
    # 检查权限
    record_check = get_chat_record_by_id(session, record_id, current_user)
    if not record_check:
        raise Exception(f"Permission denied: You don't have permission to update this record")
    
    where_clause = and_(ChatRecord.id == record_id, ChatRecord.create_by == current_user.id)

    stmt = update(ChatRecord).where(where_clause).values(
        sql_answer=answer,
    )

    session.execute(stmt)

    session.commit()

    record = get_chat_record_by_id(session, record_id, current_user)

    return record


def save_analysis_answer(session: SessionDep, record_id: int, current_user: CurrentUser, answer: str = '') -> ChatRecord:
    if not record_id:
        raise Exception("Record id cannot be None")
    
    # 检查权限
    record_check = get_chat_record_by_id(session, record_id, current_user)
    if not record_check:
        raise Exception(f"Permission denied: You don't have permission to update this record")
    
    where_clause = and_(ChatRecord.id == record_id, ChatRecord.create_by == current_user.id)

    stmt = update(ChatRecord).where(where_clause).values(
        analysis=answer,
    )

    session.execute(stmt)

    session.commit()

    record = get_chat_record_by_id(session, record_id, current_user)

    return record


def save_predict_answer(session: SessionDep, record_id: int, answer: str, current_user: CurrentUser) -> ChatRecord:
    if not record_id:
        raise Exception("Record id cannot be None")
    
    # 检查权限
    record_check = get_chat_record_by_id(session, record_id, current_user)
    if not record_check:
        raise Exception(f"Permission denied: You don't have permission to update this record")
    
    where_clause = and_(ChatRecord.id == record_id, ChatRecord.create_by == current_user.id)

    stmt = update(ChatRecord).where(where_clause).values(
        predict=answer,
    )

    session.execute(stmt)

    session.commit()

    record = get_chat_record_by_id(session, record_id, current_user)

    return record


def save_select_datasource_answer(session: SessionDep, record_id: int, current_user: CurrentUser, answer: str,
                                  datasource: int = None, engine_type: str = None) -> ChatRecord:
    if not record_id:
        raise Exception("Record id cannot be None")
    
    # 检查权限
    record = get_chat_record_by_id(session, record_id, current_user)
    if not record:
        raise Exception(f"Permission denied: You don't have permission to update this record")

    record.datasource_select_answer = answer

    if datasource:
        record.datasource = datasource
        record.engine_type = engine_type

    result = ChatRecord(**record.model_dump())

    where_clause = and_(ChatRecord.id == record.id, ChatRecord.create_by == current_user.id)

    if datasource:
        stmt = update(ChatRecord).where(where_clause).values(
            datasource_select_answer=record.datasource_select_answer,
            datasource=record.datasource,
            engine_type=record.engine_type,
        )
    else:
        stmt = update(ChatRecord).where(where_clause).values(
            datasource_select_answer=record.datasource_select_answer,
        )

    session.execute(stmt)

    session.commit()

    return result


def save_recommend_question_answer(session: SessionDep, record_id: int, current_user: CurrentUser,
                                   answer: dict = None) -> ChatRecord:
    if not record_id:
        raise Exception("Record id cannot be None")
    
    # 检查权限
    record_check = get_chat_record_by_id(session, record_id, current_user)
    if not record_check:
        raise Exception(f"Permission denied: You don't have permission to update this record")
    
    where_clause = and_(ChatRecord.id == record_id, ChatRecord.create_by == current_user.id)

    recommended_question_answer = orjson.dumps(answer).decode()

    json_str = '[]'
    if answer and answer.get('content') and answer.get('content') != '':
        try:
            json_str = extract_nested_json(answer.get('content'))

            if not json_str:
                json_str = '[]'
        except Exception as e:
            pass
    recommended_question = json_str

    stmt = update(ChatRecord).where(where_clause).values(
        recommended_question_answer=recommended_question_answer,
        recommended_question=recommended_question,
    )

    session.execute(stmt)

    session.commit()

    record = get_chat_record_by_id(session, record_id, current_user)
    record.recommended_question_answer = recommended_question_answer
    record.recommended_question = recommended_question

    return record


def save_sql(session: SessionDep, record_id: int, sql: str, current_user: CurrentUser) -> ChatRecord:
    if not record_id:
        raise Exception("Record id cannot be None")

    record = get_chat_record_by_id(session, record_id, current_user)
    if not record:
        raise Exception(f"Permission denied: You don't have permission to update this record")

    record.sql = sql

    result = ChatRecord(**record.model_dump())

    where_clause = and_(ChatRecord.id == record.id, ChatRecord.create_by == current_user.id)

    stmt = update(ChatRecord).where(where_clause).values(
        sql=record.sql
    )

    session.execute(stmt)

    session.commit()

    return result


def save_chart_answer(session: SessionDep, record_id: int, answer: str, current_user: CurrentUser) -> ChatRecord:
    if not record_id:
        raise Exception("Record id cannot be None")
    
    # 检查权限
    record_check = get_chat_record_by_id(session, record_id, current_user)
    if not record_check:
        raise Exception(f"Permission denied: You don't have permission to update this record")
    
    where_clause = and_(ChatRecord.id == record_id, ChatRecord.create_by == current_user.id)

    stmt = update(ChatRecord).where(where_clause).values(
        chart_answer=answer,
    )

    session.execute(stmt)

    session.commit()

    record = get_chat_record_by_id(session, record_id, current_user)

    return record


def save_chart(session: SessionDep, record_id: int, chart: str, current_user: CurrentUser) -> ChatRecord:
    if not record_id:
        raise Exception("Record id cannot be None")
    record = get_chat_record_by_id(session, record_id, current_user)
    if not record:
        raise Exception(f"Permission denied: You don't have permission to update this record")

    record.chart = chart

    result = ChatRecord(**record.model_dump())

    where_clause = and_(ChatRecord.id == record.id, ChatRecord.create_by == current_user.id)

    stmt = update(ChatRecord).where(where_clause).values(
        chart=record.chart
    )

    session.execute(stmt)

    session.commit()

    return result


def save_predict_data(session: SessionDep, record_id: int, current_user: CurrentUser, data: str = '') -> ChatRecord:
    if not record_id:
        raise Exception("Record id cannot be None")
    record = get_chat_record_by_id(session, record_id, current_user)
    if not record:
        raise Exception(f"Permission denied: You don't have permission to update this record")

    record.predict_data = data

    result = ChatRecord(**record.model_dump())

    where_clause = and_(ChatRecord.id == record.id, ChatRecord.create_by == current_user.id)

    stmt = update(ChatRecord).where(where_clause).values(
        predict_data=record.predict_data
    )

    session.execute(stmt)

    session.commit()

    return result


def save_error_message(session: SessionDep, record_id: int, message: str, current_user: CurrentUser) -> ChatRecord:
    if not record_id:
        raise Exception("Record id cannot be None")
    record = get_chat_record_by_id(session, record_id, current_user)
    if not record:
        raise Exception(f"Permission denied: You don't have permission to update this record")

    record.error = message
    record.finish = True
    record.finish_time = datetime.datetime.now()

    result = ChatRecord(**record.model_dump())

    where_clause = and_(ChatRecord.id == record.id, ChatRecord.create_by == current_user.id)

    stmt = update(ChatRecord).where(where_clause).values(
        error=record.error,
        finish=record.finish,
        finish_time=record.finish_time
    )

    session.execute(stmt)

    session.commit()

    return result


def save_sql_exec_data(session: SessionDep, record_id: int, data: str, current_user: CurrentUser) -> ChatRecord:
    if not record_id:
        raise Exception("Record id cannot be None")
    record = get_chat_record_by_id(session, record_id, current_user)
    if not record:
        raise Exception(f"Permission denied: You don't have permission to update this record")

    record.data = data

    result = ChatRecord(**record.model_dump())

    where_clause = and_(ChatRecord.id == record.id, ChatRecord.create_by == current_user.id)

    stmt = update(ChatRecord).where(where_clause).values(
        data=record.data,
    )

    session.execute(stmt)

    session.commit()

    return result


def finish_record(session: SessionDep, record_id: int, current_user: CurrentUser) -> ChatRecord:
    if not record_id:
        raise Exception("Record id cannot be None")
    record = get_chat_record_by_id(session, record_id, current_user)
    if not record:
        raise Exception(f"Permission denied: You don't have permission to update this record")

    record.finish = True
    record.finish_time = datetime.datetime.now()

    result = ChatRecord(**record.model_dump())

    where_clause = and_(ChatRecord.id == record.id, ChatRecord.create_by == current_user.id)

    stmt = update(ChatRecord).where(where_clause).values(
        finish=record.finish,
        finish_time=record.finish_time
    )

    session.execute(stmt)

    session.commit()

    return result


def get_old_questions(session: SessionDep, datasource: int, current_user: CurrentUser):
    records = []
    if not datasource:
        return records
    stmt = select(ChatRecord.question).where(
        and_(ChatRecord.datasource == datasource, ChatRecord.question.isnot(None),
             ChatRecord.error.is_(None), ChatRecord.create_by == current_user.id)).order_by(
        ChatRecord.create_time.desc()).limit(20)
    result = session.execute(stmt)
    for r in result:
        records.append(r.question)
    return records


def _parse_error_message(error_raw: str | None) -> str:
    """从 ChatRecord.error 解析出可读的报错信息。可能为 JSON { type, traceback }。"""
    if not error_raw or not error_raw.strip():
        return ""
    try:
        obj = orjson.loads(error_raw)
        if isinstance(obj, dict) and obj.get("type") == "exec-sql-err":
            return (obj.get("traceback") or "").strip()
        return error_raw.strip()
    except Exception:
        return error_raw.strip()


def _extract_analysis_content(analysis_raw: str | None) -> str:
    """从 ChatRecord.analysis 中提取数据分析正文（供反馈「分析过程有误」时存储）。"""
    if not analysis_raw or not analysis_raw.strip():
        return ""
    raw = analysis_raw.strip()
    if raw.startswith("{") and raw.endswith("}"):
        try:
            obj = orjson.loads(raw)
            content = obj.get("content") if isinstance(obj, dict) else None
            return (content or "").strip()
        except Exception:
            pass
    return raw


def create_error_query_record(
    session: SessionDep,
    record_id: int,
    current_user: CurrentUser,
    feedback_reason: str,
) -> ErrorQueryRecord:
    """用户点踩后写入反馈记录。分析过程有误→记录数据分析文本；查询无结果/数据不准确→记录生成 SQL。"""
    stmt = select(
        ChatRecord.id,
        ChatRecord.chat_id,
        ChatRecord.question,
        ChatRecord.sql,
        ChatRecord.analysis,
        ChatRecord.error,
        ChatRecord.create_by,
    ).where(
        and_(ChatRecord.id == record_id, ChatRecord.create_by == current_user.id)
    )
    row = session.execute(stmt).first()
    if not row:
        raise Exception("Permission denied or record not found")

    if feedback_reason == "wrong_analysis":
        analysis_text = _extract_analysis_content(row.analysis)
        sql_value = ""
    else:
        analysis_text = ""
        sql_value = row.sql or ""

    rec = ErrorQueryRecord(
        record_id=record_id,
        chat_id=row.chat_id,
        question=row.question or "",
        sql=sql_value,
        analysis_text=analysis_text,
        error_message=_parse_error_message(row.error),
        feedback_reason=feedback_reason,
        create_by=current_user.id,
        create_time=datetime.datetime.now(),
    )
    session.add(rec)
    session.flush()
    session.refresh(rec)
    session.commit()
    return rec


def list_error_query_records_pager(
    session: SessionDep,
    page_num: int,
    page_size: int,
    status: str | None = None,
    feedback_reason: str | None = None,
):
    """分页查询反馈记录（管理员）。status/feedback_reason 为空则不过滤。返回 (list[ErrorQueryRecord], total)。"""
    from sqlalchemy import func
    base = select(ErrorQueryRecord)
    if status and status in ("pending", "resolved"):
        base = base.where(ErrorQueryRecord.status == status)
    if feedback_reason and feedback_reason in ("no_result", "inaccurate_data", "wrong_analysis"):
        base = base.where(ErrorQueryRecord.feedback_reason == feedback_reason)
    count_stmt = select(func.count()).select_from(ErrorQueryRecord)
    if status and status in ("pending", "resolved"):
        count_stmt = count_stmt.where(ErrorQueryRecord.status == status)
    if feedback_reason and feedback_reason in ("no_result", "inaccurate_data", "wrong_analysis"):
        count_stmt = count_stmt.where(ErrorQueryRecord.feedback_reason == feedback_reason)
    total = session.execute(count_stmt).scalar() or 0
    stmt = (
        base.order_by(ErrorQueryRecord.create_time.desc())
        .offset((page_num - 1) * page_size)
        .limit(page_size)
    )
    rows = session.execute(stmt).scalars().all()
    return list(rows), total


def delete_error_query_record(session: SessionDep, record_id: int) -> bool:
    """删除一条反馈记录（仅管理员）。"""
    rec = session.get(ErrorQueryRecord, record_id)
    if not rec:
        return False
    session.delete(rec)
    session.commit()
    return True


def update_error_query_record_status(
    session: SessionDep,
    record_id: int,
    status: str,
) -> bool:
    """更新反馈记录的处理状态（仅管理员）。status: pending | resolved。"""
    if status not in ("pending", "resolved"):
        return False
    stmt = (
        update(ErrorQueryRecord)
        .where(ErrorQueryRecord.id == record_id)
        .values(status=status)
    )
    result = session.execute(stmt)
    session.commit()
    return result.rowcount > 0
