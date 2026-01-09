from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import RedirectResponse
from sqlmodel import Session
from common.core.config import settings
from common.core.db import engine
from common.core.security import create_access_token
from common.core.schemas import Token
from datetime import timedelta
from apps.system.api.oauth2 import (
    get_access_token, 
    get_userinfo, 
    create_or_get_user,
    cache_oauth2_access_token,
    verify_and_consume_oauth2_state
)
from common.utils.utils import SQLBotLogUtil

router = APIRouter(tags=["callback"], prefix="/callback")


# 支持 /api/v1/callback 与 /api/v1/callback/ 两种形式
@router.get("")
#@router.get("/")
async def callback(
    code: str = Query(..., description="授权码"),
    state: str = Query(None, description="状态参数"),
    error: str = Query(None, description="错误信息"),
):
    """
    OAuth2回调接口，接收 code 和 state 参数
    处理OAuth2登录回调：获取token -> 获取用户信息 -> 创建/获取用户 -> 生成系统token -> 跳转到前端
    """
    if error:
        SQLBotLogUtil.error(f"OAuth2 callback error: {error}")
        # 跳转到登录页面并显示错误
        redirect_url = f"{settings.FRONTEND_HOST}/#/login?error={error}"
        return RedirectResponse(url=redirect_url)
    
    if not settings.OAUTH2_ENABLED:
        raise HTTPException(status_code=400, detail="OAuth2 SSO is not enabled")
    
    SQLBotLogUtil.info(f"OAuth2 callback received: code={code[:20]}..., state={state}")
    # 验证state参数（防止CSRF攻击）
    if not state:
        SQLBotLogUtil.error("OAuth2 callback: state parameter is missing")
        redirect_url = f"{settings.FRONTEND_HOST}/#/login?error=invalid_state"
        return RedirectResponse(url=redirect_url)
    
    # 验证并消费state（一次性使用）
    is_valid_state = await verify_and_consume_oauth2_state(state)
    if not is_valid_state:
        SQLBotLogUtil.error(f"OAuth2 callback: invalid or already used state: {state[:16]}...")
        redirect_url = f"{settings.FRONTEND_HOST}/#/login?error=invalid_state"
        return RedirectResponse(url=redirect_url)
    
    SQLBotLogUtil.info(f"OAuth2 state verified successfully: {state[:16]}...")
    
    try:
        # 1. 使用code换取access_token
        access_token = await get_access_token(code)
        if not access_token:
            raise HTTPException(status_code=400, detail="Failed to get access token from OAuth2 provider")
        
        # 2. 使用access_token获取用户信息
        userinfo = await get_userinfo(access_token)
        if not userinfo:
            raise HTTPException(status_code=400, detail="Failed to get user info from OAuth2 provider")
        
        SQLBotLogUtil.info(f"OAuth2 userinfo received: {userinfo}")
        
        # 3. 创建或获取系统用户
        with Session(engine) as session:
            user = await create_or_get_user(userinfo, session)
            
            # 确保user是UserInfoDTO对象，如果不是则转换
            from apps.system.schemas.system_schema import UserInfoDTO
            if isinstance(user, dict):
                user = UserInfoDTO.model_validate(user)
            elif not isinstance(user, UserInfoDTO):
                # 如果user不是UserInfoDTO类型，尝试转换
                user = UserInfoDTO.model_validate(user)
            
            # 记录用户信息，用于调试
            SQLBotLogUtil.info(f"User info before token generation: id={user.id}, account={user.account}, weight={user.weight}, isAdmin={user.isAdmin}")
            
            # 4. 使用account作为key缓存access_token（用于后续登出）
            user_account = user.account
            await cache_oauth2_access_token(user_account, access_token, expire=3600)
            SQLBotLogUtil.info(f"Cached OAuth2 access_token for account: {user_account}")
            
            # 5. 生成系统token
            access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
            # 转换为字典用于生成token
            user_dict = user.model_dump()
            SQLBotLogUtil.info(f"Token payload: weight={user_dict.get('weight')}, isAdmin={user_dict.get('isAdmin')}")
            system_token = create_access_token(
                user_dict, expires_delta=access_token_expires
            )
        
        # 5. 跳转到前端，携带token
        redirect_url = f"{settings.FRONTEND_HOST}/#/oauth2/callback?token={system_token}"
        SQLBotLogUtil.info(f"OAuth2 login successful, redirecting to: {redirect_url}")
        return RedirectResponse(url=redirect_url)
        
    except HTTPException:
        raise
    except Exception as e:
        SQLBotLogUtil.exception(f"OAuth2 callback error: {str(e)}")
        redirect_url = f"{settings.FRONTEND_HOST}/#/login?error=oauth2_error"
        return RedirectResponse(url=redirect_url)

