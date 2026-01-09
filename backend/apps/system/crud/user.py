
from typing import Optional
from sqlmodel import Session, func, select, delete as sqlmodel_delete
from apps.system.models.system_model import UserWsModel, WorkspaceModel
from apps.system.schemas.auth import CacheName, CacheNamespace
from apps.system.schemas.system_schema import EMAIL_REGEX, PWD_REGEX, BaseUserDTO, UserInfoDTO, UserWs
from common.core.deps import SessionDep
from common.core.sqlbot_cache import cache, clear_cache
from common.utils.locale import I18n
from common.utils.utils import SQLBotLogUtil
from ..models.user import UserModel
from common.core.security import verify_md5pwd
import re

def get_db_user(*, session: Session, user_id: int) -> UserModel:
    db_user = session.get(UserModel, user_id)
    return db_user

def get_user_by_account(*, session: Session, account: str) -> BaseUserDTO | None:
    statement = select(UserModel).where(UserModel.account == account)
    db_user = session.exec(statement).first()
    if not db_user:
        return None
    return BaseUserDTO.model_validate(db_user.model_dump())

@cache(namespace=CacheNamespace.AUTH_INFO, cacheName=CacheName.USER_INFO, keyExpression="user_id")
async def get_user_info(*, session: Session, user_id: int) -> UserInfoDTO | None:
    db_user: UserModel = get_db_user(session = session, user_id = user_id)
    if not db_user:
        return None
    userInfo = UserInfoDTO.model_validate(db_user.model_dump())
    
    # 获取用户在所有工作空间中的最大 weight，用于判断是否是工作空间管理员
    max_weight_result = session.exec(
        select(func.max(UserWsModel.weight)).where(UserWsModel.uid == userInfo.id)
    ).first()
    # 如果用户在任何工作空间中是管理员（weight > 0），返回最大 weight
    # 否则返回 0（普通成员）
    userInfo.weight = max_weight_result if max_weight_result is not None else 0
    SQLBotLogUtil.debug(f"User {userInfo.id} ({userInfo.account}) weight: {userInfo.weight} (max_weight_result: {max_weight_result})")
    
    # 系统管理员判断逻辑：
    # 1. 传统的系统管理员：id == 1 && account == 'admin'（用于测试环境）
    # 2. 工作空间管理员：weight > 0（用于生产环境，OAuth2 认证用户）
    # 在生产环境中，工作空间管理员也被视为系统管理员，可以访问所有系统管理功能
    userInfo.isAdmin = (userInfo.id == 1 and userInfo.account == 'admin') or (userInfo.weight > 0)
    
    return userInfo

def authenticate(*, session: Session, account: str, password: str) -> BaseUserDTO | None:
    db_user = get_user_by_account(session=session, account=account)
    if not db_user:
        return None
    if not verify_md5pwd(password, db_user.password):
        return None
    return db_user

async def user_ws_options(session: Session, uid: int, trans: Optional[I18n] = None) -> list[UserWs]:
    if uid == 1:
        stmt = select(WorkspaceModel.id, WorkspaceModel.name).order_by(WorkspaceModel.name, WorkspaceModel.create_time)
    else:
        stmt = select(WorkspaceModel.id, WorkspaceModel.name).join(
            UserWsModel, UserWsModel.oid == WorkspaceModel.id
        ).where(
            UserWsModel.uid == uid,
        ).order_by(WorkspaceModel.name, WorkspaceModel.create_time)
    result = session.exec(stmt)
    if not trans:
        return result.all()
    return [
        UserWs(id = id, name = trans(name) if name.startswith('i18n') else name) 
        for id, name in result.all()
    ]
    
@clear_cache(namespace=CacheNamespace.AUTH_INFO, cacheName=CacheName.USER_INFO, keyExpression="id")
async def single_delete(session: SessionDep, id: int):
    user_model: UserModel = get_db_user(session = session, user_id = id)
    del_stmt = sqlmodel_delete(UserWsModel).where(UserWsModel.uid == id)
    session.exec(del_stmt)
    session.delete(user_model)
    session.commit()

async def clean_user_cache(id: int):
    """
    清除用户信息缓存
    注意：get_user_info 的 keyExpression 是 "user_id"，所以缓存 key 是 AUTH_INFO:USER_INFO:{user_id}
    这里需要手动构建相同的 key 来清除缓存
    """
    from common.core.config import settings
    from common.core.sqlbot_cache import is_cache_initialized
    from fastapi_cache import FastAPICache
    
    if not settings.CACHE_TYPE or settings.CACHE_TYPE.lower() == "none" or not is_cache_initialized():
        SQLBotLogUtil.info(f"Cache not enabled or not initialized, skipping cache clear for user [{id}]")
        return
    
    try:
        namespace = str(CacheNamespace.AUTH_INFO)
        cache_name = str(CacheName.USER_INFO)
        # 构建与 get_user_info 相同的缓存 key
        cache_key = f"{namespace}:{cache_name}:{id}"
        
        backend = FastAPICache.get_backend()
        if settings.CACHE_TYPE.lower() == "redis":
            redis = backend.redis
            # Redis 的 delete 方法不会抛出异常，即使 key 不存在
            result = await redis.delete(cache_key)
            if result:
                SQLBotLogUtil.info(f"User cache cleared (Redis): {cache_key}")
            else:
                SQLBotLogUtil.debug(f"User cache key not found (Redis): {cache_key}")
        else:
            # 内存缓存：先检查 key 是否存在，再删除
            if await backend.get(cache_key):
                await backend.clear(key=cache_key)
                SQLBotLogUtil.info(f"User cache cleared (Memory): {cache_key}")
            else:
                SQLBotLogUtil.debug(f"User cache key not found (Memory): {cache_key}")
    except Exception as e:
        SQLBotLogUtil.exception(f"Failed to clear user cache for [{id}]: {str(e)}")


def check_account_exists(*, session: Session, account: str) -> bool:
    return session.exec(select(func.count()).select_from(UserModel).where(UserModel.account == account)).one() > 0
def check_email_exists(*, session: Session, email: str) -> bool:
    """
    检查邮箱是否存在
    如果email为空或None，返回False（空邮箱不认为是已存在）
    """
    if not email or email.strip() == "":
        return False
    return session.exec(select(func.count()).select_from(UserModel).where(UserModel.email == email)).one() > 0


def check_email_format(email: str) -> bool:
    """
    检查邮箱格式是否正确
    如果email为空或None，返回False（空邮箱格式无效）
    """
    if not email or email.strip() == "":
        return False
    return bool(EMAIL_REGEX.fullmatch(email))

def check_pwd_format(pwd: str) -> bool:
    return bool(PWD_REGEX.fullmatch(pwd))
