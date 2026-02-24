"""系统管理 - 反馈空间：展示用户点踩反馈（问题、SQL、报错、原因），运维可标记已解决/待解决。"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from apps.chat.curd.chat import (
    list_error_query_records_pager,
    update_error_query_record_status,
    delete_error_query_record,
)
from common.core.deps import CurrentUser, SessionDep
from common.core.schemas import PaginatedResponse

router = APIRouter(tags=["system/error_query_record"], prefix="/system/error-query-records")


class ErrorQueryRecordItem(BaseModel):
    id: Optional[int] = None
    record_id: int = 0
    chat_id: int = 0
    question: str = ""
    sql: str = ""
    analysis_text: str = ""
    error_message: str = ""
    feedback_reason: str = ""
    status: str = "pending"
    create_by: Optional[int] = None
    create_time: Optional[str] = None

    class Config:
        from_attributes = True


class UpdateStatusBody(BaseModel):
    status: str  # pending | resolved


@router.get("/page/{pageNum}/{pageSize}", response_model=PaginatedResponse[ErrorQueryRecordItem])
async def pager(
    session: SessionDep,
    current_user: CurrentUser,
    pageNum: int,
    pageSize: int,
    status: Optional[str] = Query(None, description="按处理状态筛选：pending 待解决，resolved 已解决，不传则全部"),
    feedback_reason: Optional[str] = Query(None, description="按反馈原因筛选：no_result / inaccurate_data / wrong_analysis，不传则全部"),
):
    """分页获取反馈记录（仅管理员）。支持按处理状态、反馈原因筛选。"""
    if not getattr(current_user, "isAdmin", False):
        raise HTTPException(status_code=403, detail="仅管理员可查看反馈记录")
    if pageNum < 1 or pageSize < 1:
        raise HTTPException(status_code=400, detail="pageNum 与 pageSize 须为正整数")
    filter_status = status if status in ("pending", "resolved") else None
    filter_reason = feedback_reason if feedback_reason in ("no_result", "inaccurate_data", "wrong_analysis") else None
    items, total = list_error_query_records_pager(
        session, page_num=pageNum, page_size=pageSize, status=filter_status, feedback_reason=filter_reason
    )
    result = [
        ErrorQueryRecordItem(
            id=r.id,
            record_id=r.record_id,
            chat_id=r.chat_id,
            question=r.question or "",
            sql=r.sql or "",
            analysis_text=r.analysis_text or "",
            error_message=r.error_message or "",
            feedback_reason=r.feedback_reason or "",
            status=r.status or "pending",
            create_by=r.create_by,
            create_time=r.create_time.isoformat() if r.create_time else None,
        )
        for r in items
    ]
    return PaginatedResponse(
        items=result,
        total=total,
        page=pageNum,
        size=pageSize,
        total_pages=(total + pageSize - 1) // pageSize if pageSize > 0 else 0,
    )


@router.patch("/{record_id}")
async def update_status(
    session: SessionDep,
    current_user: CurrentUser,
    record_id: int,
    body: UpdateStatusBody,
):
    """更新反馈记录处理状态（仅管理员）。"""
    if not getattr(current_user, "isAdmin", False):
        raise HTTPException(status_code=403, detail="仅管理员可更新状态")
    if body.status not in ("pending", "resolved"):
        raise HTTPException(status_code=400, detail="status 须为 pending 或 resolved")
    ok = update_error_query_record_status(session, record_id, body.status)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {}


@router.delete("/{record_id}")
async def delete_record(
    session: SessionDep,
    current_user: CurrentUser,
    record_id: int,
):
    """删除一条反馈记录（仅管理员）。"""
    if not getattr(current_user, "isAdmin", False):
        raise HTTPException(status_code=403, detail="仅管理员可删除反馈记录")
    ok = delete_error_query_record(session, record_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {}
