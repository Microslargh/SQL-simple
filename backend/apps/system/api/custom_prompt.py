from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from sqlmodel import select
from apps.system.crud.custom_prompt import (
    list_custom_prompts,
    create_custom_prompt,
    update_custom_prompt,
    delete_custom_prompts,
    get_custom_prompt
)
from apps.system.models.custom_prompt_model import CustomPrompt, CustomPromptCreator, CustomPromptEditor
from common.core.deps import CurrentUser, SessionDep, Trans
from common.core.pagination import Paginator
from common.core.schemas import PaginatedResponse
from common.utils.time import get_timestamp
from datetime import datetime

router = APIRouter(tags=["system/custom_prompt"], prefix="/system/custom_prompt")


@router.get("/{type}/page/{pageNum}/{pageSize}", response_model=PaginatedResponse[CustomPrompt])
async def get_list(
    session: SessionDep,
    current_user: CurrentUser,
    type: str,
    pageNum: int,
    pageSize: int,
    name: Optional[str] = Query(None, description="搜索关键字(可选)")
):
    """分页获取自定义提示词列表"""
    prompts, total = list_custom_prompts(
        session=session,
        oid=current_user.oid,
        prompt_type=type,
        page_num=pageNum,
        page_size=pageSize,
        keyword=name
    )
    
    return PaginatedResponse(
        items=prompts,
        total=total,
        page=pageNum,
        size=pageSize,
        total_pages=(total + pageSize - 1) // pageSize if pageSize > 0 else 0
    )


@router.get("/id/{id}")
async def get_one(
    session: SessionDep,
    current_user: CurrentUser,
    id: int
) -> CustomPrompt:
    """获取单个自定义提示词"""
    prompt = get_custom_prompt(session, id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Custom prompt not found")
    if prompt.oid != current_user.oid:
        raise HTTPException(status_code=403, detail="Permission denied")
    return prompt


@router.post("", response_model=CustomPrompt)
async def create(
    session: SessionDep,
    current_user: CurrentUser,
    creator: CustomPromptCreator
):
    """创建自定义提示词"""
    prompt = create_custom_prompt(
        session=session,
        oid=current_user.oid,
        creator=creator
    )
    # 设置创建时间
    if not prompt.create_time:
        prompt.create_time = datetime.now()
        session.add(prompt)
        session.commit()
        session.refresh(prompt)
    return prompt


@router.put("", response_model=CustomPrompt)
async def update(
    session: SessionDep,
    current_user: CurrentUser,
    editor: CustomPromptEditor
):
    """更新自定义提示词"""
    # 检查权限
    prompt = get_custom_prompt(session, editor.id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Custom prompt not found")
    if prompt.oid != current_user.oid:
        raise HTTPException(status_code=403, detail="Permission denied")
    
    updated = update_custom_prompt(session=session, editor=editor)
    if not updated:
        raise HTTPException(status_code=404, detail="Custom prompt not found")
    return updated


@router.delete("")
async def delete(
    session: SessionDep,
    current_user: CurrentUser,
    data: List[int]
):
    """批量删除自定义提示词"""
    # 检查权限
    prompts = session.exec(select(CustomPrompt).where(CustomPrompt.id.in_(data))).all()
    for prompt in prompts:
        if prompt.oid != current_user.oid:
            raise HTTPException(status_code=403, detail="Permission denied")
    
    count = delete_custom_prompts(session=session, prompt_ids=data)
    return {"deleted_count": count}

