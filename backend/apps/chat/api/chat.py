import asyncio
import io
from typing import Optional

import numpy as np
import orjson
import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_, select
from common.utils.utils import SQLBotLogUtil

from apps.chat.curd.chat import list_chats, get_chat_with_records, create_chat, rename_chat, \
    delete_chat, get_chat_chart_data, get_chat_predict_data, get_chat_with_records_with_data, get_chat_record_by_id, \
    create_error_query_record, list_execution_traces
from apps.chat.models.chat_model import CreateChat, ChatRecord, RenameChat, ChatQuestion, ExcelData
from apps.chat.task.llm import LLMService
from common.core.deps import CurrentAssistant, SessionDep, CurrentUser, Trans


router = APIRouter(tags=["Data Q&A"], prefix="/chat")


@router.get("/list")
async def chats(session: SessionDep, current_user: CurrentUser):
    return list_chats(session, current_user)


@router.get("/get/{chart_id}")
async def get_chat(session: SessionDep, current_user: CurrentUser, chart_id: int, current_assistant: CurrentAssistant):
    def inner():
        return get_chat_with_records(chart_id=chart_id, session=session, current_user=current_user,
                                     current_assistant=current_assistant)

    return await asyncio.to_thread(inner)


@router.get("/get/with_data/{chart_id}")
async def get_chat_with_data(session: SessionDep, current_user: CurrentUser, chart_id: int,
                             current_assistant: CurrentAssistant):
    def inner():
        return get_chat_with_records_with_data(chart_id=chart_id, session=session, current_user=current_user,
                                               current_assistant=current_assistant)

    return await asyncio.to_thread(inner)


@router.get("/record/get/{chart_record_id}/data")
async def chat_record_data(session: SessionDep, chart_record_id: int, current_user: CurrentUser):
    def inner():
        return get_chat_chart_data(chart_record_id=chart_record_id, session=session, current_user=current_user)

    return await asyncio.to_thread(inner)


@router.get("/record/get/{chart_record_id}/predict_data")
async def chat_predict_data(session: SessionDep, chart_record_id: int, current_user: CurrentUser):
    def inner():
        return get_chat_predict_data(chart_record_id=chart_record_id, session=session, current_user=current_user)

    return await asyncio.to_thread(inner)


@router.get("/record/{chat_record_id}/trace")
async def chat_execution_trace(session: SessionDep, chat_record_id: int, current_user: CurrentUser):
    def inner():
        return list_execution_traces(session=session, record_id=chat_record_id, current_user=current_user)

    return await asyncio.to_thread(inner)


@router.post("/rename")
async def rename(session: SessionDep, chat: RenameChat, current_user: CurrentUser):
    try:
        return rename_chat(session=session, rename_object=chat, current_user=current_user)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.get("/delete/{chart_id}")
async def delete(session: SessionDep, chart_id: int, current_user: CurrentUser):
    try:
        return delete_chat(session=session, chart_id=chart_id, current_user=current_user)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.post("/start")
async def start_chat(session: SessionDep, current_user: CurrentUser, create_chat_obj: CreateChat):
    try:
        return create_chat(session, current_user, create_chat_obj)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.post("/assistant/start")
async def start_chat(session: SessionDep, current_user: CurrentUser):
    try:
        return create_chat(session, current_user, CreateChat(origin=2), False)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


class FeedbackBody(BaseModel):
    record_id: int
    is_like: bool = True
    reason: Optional[str] = None  # no_result | inaccurate_data | wrong_analysis，点踩时必填


@router.post("/feedback")
async def submit_feedback(session: SessionDep, current_user: CurrentUser, body: FeedbackBody):
    """对话反馈：点赞或点踩。点踩时需传 reason，系统将问题、SQL、报错写入错误查询记录供运维查看。"""
    if body.is_like:
        return {"ok": True, "message": "感谢反馈"}
    reason = (body.reason or "").strip()
    if reason not in ("no_result", "inaccurate_data", "wrong_analysis"):
        raise HTTPException(status_code=400, detail="点踩时请选择原因：no_result / inaccurate_data / wrong_analysis")
    try:
        create_error_query_record(session, body.record_id, current_user, feedback_reason=reason)
        return {"ok": True, "message": "反馈已记录，我们会尽快处理"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recommend_questions/{chat_record_id}")
async def recommend_questions(session: SessionDep, current_user: CurrentUser, chat_record_id: int,
                              current_assistant: CurrentAssistant):
    def _return_empty():
        yield 'data:' + orjson.dumps({'content': '[]', 'type': 'recommended_question'}).decode() + '\n\n'

    if not settings.GUESS_RECOMMEND_ENABLED:
        return StreamingResponse(_return_empty(), media_type="text/event-stream")

    try:
        record = get_chat_record_by_id(session, chat_record_id, current_user)

        if not record:
            return StreamingResponse(_return_empty(), media_type="text/event-stream")

        request_question = ChatQuestion(chat_id=record.chat_id, question=record.question if record.question else '')

        llm_service = await LLMService.create(current_user, request_question, current_assistant, True)
        llm_service.set_record(record)
        llm_service.run_recommend_questions_task_async()
    except Exception as e:
        SQLBotLogUtil.error("Recommend questions task failed", exc_info=True)

        def _err(_e: Exception):
            safe_msg = '系统繁忙，请稍后重试'
            yield 'data:' + orjson.dumps({'content': safe_msg, 'type': 'error'}).decode() + '\n\n'

        return StreamingResponse(_err(e), media_type="text/event-stream")

    return StreamingResponse(llm_service.await_result(), media_type="text/event-stream")


@router.post("/question")
async def stream_sql(session: SessionDep, current_user: CurrentUser, request_question: ChatQuestion,
                     current_assistant: CurrentAssistant):
    """Stream SQL analysis results
    
    Args:
        session: Database session
        current_user: CurrentUser
        request_question: User question model
        
    Returns:
        Streaming response with analysis results
    """

    try:
        llm_service = await LLMService.create(current_user, request_question, current_assistant, embedding=True)
        llm_service.init_record()
        llm_service.run_task_async()
    except Exception as e:
        SQLBotLogUtil.error("LLM stream task failed", exc_info=True)

        def _err(_e: Exception):
            safe_msg = '系统繁忙，请稍后重试'
            yield 'data:' + orjson.dumps({'content': safe_msg, 'type': 'error'}).decode() + '\n\n'

        return StreamingResponse(_err(e), media_type="text/event-stream")

    return StreamingResponse(llm_service.await_result(), media_type="text/event-stream")


@router.post("/record/{chat_record_id}/{action_type}")
async def analysis_or_predict(session: SessionDep, current_user: CurrentUser, chat_record_id: int, action_type: str,
                              current_assistant: CurrentAssistant):
    try:
        if action_type != 'analysis' and action_type != 'predict':
            raise Exception(f"Type {action_type} Not Found")
        record: ChatRecord | None = None

        stmt = select(ChatRecord.id, ChatRecord.question, ChatRecord.chat_id, ChatRecord.datasource,
                      ChatRecord.engine_type,
                      ChatRecord.ai_modal_id, ChatRecord.create_by, ChatRecord.chart, ChatRecord.data).where(
            and_(ChatRecord.id == chat_record_id, ChatRecord.create_by == current_user.id))
        result = session.execute(stmt)
        for r in result:
            record = ChatRecord(id=r.id, question=r.question, chat_id=r.chat_id, datasource=r.datasource,
                                engine_type=r.engine_type, ai_modal_id=r.ai_modal_id, create_by=r.create_by,
                                chart=r.chart,
                                data=r.data)

        if not record:
            raise Exception(f"Chat record with id {chat_record_id} not found or permission denied")

        if not record.chart:
            raise Exception(
                f"Chat record with id {chat_record_id} has not generated chart, do not support to analyze it")

        request_question = ChatQuestion(chat_id=record.chat_id, question=record.question)

        llm_service = await LLMService.create(current_user, request_question, current_assistant)
        llm_service.run_analysis_or_predict_task_async(action_type, record)
    except Exception as e:
        SQLBotLogUtil.error("LLM analysis/predict task failed", exc_info=True)

        def _err(_e: Exception):
            safe_msg = '系统繁忙，请稍后重试'
            yield 'data:' + orjson.dumps({'content': safe_msg, 'type': 'error'}).decode() + '\n\n'

        return StreamingResponse(_err(e), media_type="text/event-stream")

    return StreamingResponse(llm_service.await_result(), media_type="text/event-stream")


@router.post("/excel/export")
async def export_excel(excel_data: ExcelData, trans: Trans):
    def inner():
        _fields_list = []
        data = []
        if not excel_data.data:
            raise HTTPException(
                status_code=500,
                detail=trans("i18n_excel_export.data_is_empty")
            )

        for _data in excel_data.data:
            _row = []
            for field in excel_data.axis:
                _row.append(_data.get(field.value))
            data.append(_row)
        for field in excel_data.axis:
            _fields_list.append(field.name)
        df = pd.DataFrame(np.array(data), columns=_fields_list)

        buffer = io.BytesIO()

        with pd.ExcelWriter(buffer, engine='xlsxwriter',
                            engine_kwargs={'options': {'strings_to_numbers': True}}) as writer:
            df.to_excel(writer, sheet_name='Sheet1', index=False)

        buffer.seek(0)
        return io.BytesIO(buffer.getvalue())

    result = await asyncio.to_thread(inner)
    return StreamingResponse(result, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
