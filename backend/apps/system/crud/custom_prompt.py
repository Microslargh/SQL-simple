from typing import List, Optional
from sqlmodel import select, func, or_
from sqlmodel import Session
from apps.system.models.custom_prompt_model import CustomPrompt, CustomPromptCreator, CustomPromptEditor


def get_custom_prompt(session: Session, prompt_id: int) -> Optional[CustomPrompt]:
    """获取单个自定义提示词"""
    return session.exec(select(CustomPrompt).where(CustomPrompt.id == prompt_id)).first()


def list_custom_prompts(
    session: Session,
    oid: int,
    prompt_type: str,
    page_num: int = 1,
    page_size: int = 10,
    keyword: Optional[str] = None
) -> tuple[List[CustomPrompt], int]:
    """分页获取自定义提示词列表"""
    stmt = select(CustomPrompt).where(
        CustomPrompt.oid == oid,
        CustomPrompt.type == prompt_type
    )
    
    if keyword:
        kw = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                CustomPrompt.name.ilike(kw),
                CustomPrompt.prompt.ilike(kw),
            )
        )
    
    # 获取总数
    count_stmt = select(func.count()).select_from(CustomPrompt).where(
        CustomPrompt.oid == oid,
        CustomPrompt.type == prompt_type
    )
    if keyword:
        kw = f"%{keyword.strip()}%"
        count_stmt = count_stmt.where(
            or_(
                CustomPrompt.name.ilike(kw),
                CustomPrompt.prompt.ilike(kw),
            )
        )
    
    total = session.exec(count_stmt).one()
    
    # 分页查询
    stmt = stmt.offset((page_num - 1) * page_size).limit(page_size)
    prompts = list(session.exec(stmt).all())
    
    return prompts, total


def create_custom_prompt(session: Session, oid: int, creator: CustomPromptCreator) -> CustomPrompt:
    """创建自定义提示词"""
    prompt = CustomPrompt(
        oid=oid,
        type=creator.type,
        name=creator.name,
        prompt=creator.prompt,
        specific_ds=creator.specific_ds,
        datasource_ids=creator.datasource_ids or []
    )
    session.add(prompt)
    session.commit()
    session.refresh(prompt)
    return prompt


def update_custom_prompt(session: Session, editor: CustomPromptEditor) -> Optional[CustomPrompt]:
    """更新自定义提示词"""
    prompt = get_custom_prompt(session, editor.id)
    if not prompt:
        return None
    
    if editor.type is not None:
        prompt.type = editor.type
    if editor.name is not None:
        prompt.name = editor.name
    if editor.prompt is not None:
        prompt.prompt = editor.prompt
    if editor.specific_ds is not None:
        prompt.specific_ds = editor.specific_ds
    if editor.datasource_ids is not None:
        prompt.datasource_ids = editor.datasource_ids
    
    session.add(prompt)
    session.commit()
    session.refresh(prompt)
    return prompt


def delete_custom_prompts(session: Session, prompt_ids: List[int]) -> int:
    """批量删除自定义提示词"""
    prompts = session.exec(select(CustomPrompt).where(CustomPrompt.id.in_(prompt_ids))).all()
    for prompt in prompts:
        session.delete(prompt)
    session.commit()
    return len(prompts)