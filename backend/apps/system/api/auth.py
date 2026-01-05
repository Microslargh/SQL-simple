"""
认证相关接口（登出等）
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.security.utils import get_authorization_scheme_param
from common.core.config import settings
from common.core.db import engine
from common.core.schemas import TokenPayload
from common.core import security
from common.utils.utils import SQLBotLogUtil
from apps.system.crud.user import get_db_user
from apps.system.api.oauth2 import get_cached_oauth2_access_token, clear_cached_oauth2_access_token
from sqlmodel import Session
import httpx
import urllib.parse
import jwt

router = APIRouter(tags=["auth"], prefix="/auth")


@router.post("/logout")
async def logout(request: Request):
    """
    用户登出接口
    根据用户的register_type判断：
    - register_type=1（4a平台注册）：根据用户名从缓存获取OAuth2 access_token，调用OAuth2平台的登出接口
    - 其他：走普通登出流程
    """
    SQLBotLogUtil.info("User logout request received")
    
    # 获取token并解析用户信息
    token_key = settings.TOKEN_KEY
    token = request.headers.get(token_key)
    user_register_type = None
    user_account = None
    
    if not token:
        SQLBotLogUtil.warning("No token provided in logout request")
        return JSONResponse({
            "code": 0,
            "msg": "success",
            "data": {
                "oauth2_enabled": False,
                "logout_success": True
            }
        })
    
    try:
        # 解析token获取用户ID
        schema, param = get_authorization_scheme_param(token)
        if schema.lower() != "bearer":
            SQLBotLogUtil.warning(f"Invalid token schema: {schema}")
            return JSONResponse({
                "code": 0,
                "msg": "success",
                "data": {
                    "oauth2_enabled": False,
                    "logout_success": True
                }
            })
        
        payload = jwt.decode(
            param, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
        
        # 从数据库获取用户信息，检查register_type和account
        with Session(engine) as session:
            db_user = get_db_user(session=session, user_id=token_data.id)
            if not db_user:
                SQLBotLogUtil.warning(f"User not found: {token_data.id}")
                return JSONResponse({
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "oauth2_enabled": False,
                        "logout_success": True
                    }
                })
            
            user_register_type = db_user.register_type
            user_account = db_user.account
            SQLBotLogUtil.info(f"User logout: id={token_data.id}, account={user_account}, register_type={user_register_type}")
            
    except jwt.ExpiredSignatureError:
        SQLBotLogUtil.warning("Token expired during logout")
        return JSONResponse({
            "code": 0,
            "msg": "success",
            "data": {
                "oauth2_enabled": False,
                "logout_success": True
            }
        })
    except Exception as e:
        SQLBotLogUtil.exception(f"Error parsing token during logout: {str(e)}")
        return JSONResponse({
            "code": 0,
            "msg": "success",
            "data": {
                "oauth2_enabled": False,
                "logout_success": True
            }
        })
    
    # 只有register_type=1或者2的用户才调用OAuth2登出接口
    if (user_register_type == 1 or user_register_type == 2) and settings.OAUTH2_ENABLED and settings.OAUTH2_LOGOUT_URL:
        return await _oauth2_logout(user_account)
    else:
        # 普通登出：只返回成功消息，前端清除本地token即可
        SQLBotLogUtil.info(f"Local logout for user account={user_account}, register_type={user_register_type}")
        return JSONResponse({
            "code": 0,
            "msg": "success",
            "data": {
                "oauth2_enabled": False,
                "logout_success": True
            }
        })


async def _oauth2_logout(user_account: str) -> JSONResponse:
    """
    OAuth2登出处理函数
    根据用户名从缓存获取access_token，并调用OAuth2平台的登出接口
    
    Args:
        user_account: 用户账号
    
    Returns:
        JSONResponse: 登出结果
    """
    if not user_account:
        SQLBotLogUtil.error("User account is required for OAuth2 logout")
        return JSONResponse({
            "code": 0,
            "msg": "success",
            "data": {
                "oauth2_enabled": True,
                "logout_success": False,
                "error": "User account not found"
            }
        })
    
    SQLBotLogUtil.info(f"OAuth2 logout for account: {user_account}")
    
    # 从缓存中根据用户名获取OAuth2 access_token
    oauth2_access_token = await get_cached_oauth2_access_token(user_account)
    
    if not oauth2_access_token:
        SQLBotLogUtil.warning(f"OAuth2 access_token not found in cache for account: {user_account}")
        # 即使没有access_token，也清除缓存并返回成功
        await clear_cached_oauth2_access_token(user_account)
        return JSONResponse({
            "code": 0,
            "msg": "success",
            "data": {
                "oauth2_enabled": True,
                "logout_success": False,
                "error": "OAuth2 access_token not found in cache"
            }
        })
    
    SQLBotLogUtil.info(f"Retrieved OAuth2 access_token from cache for account: {user_account}")
    
    # 调用OAuth2平台的登出接口
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            # 构建OAuth2请求头（如果需要）
            from apps.system.api.oauth2 import build_oauth2_headers
            oauth2_headers = build_oauth2_headers("/authcenter/logout")
            
            # 合并请求头
            headers = {
                **oauth2_headers,
                "Accept": "application/json",
            }
            
            # 使用access_token作为参数调用登出接口
            url = f"{settings.OAUTH2_LOGOUT_URL}?access_token={oauth2_access_token}&client_id={settings.OAUTH2_CLIENT_ID}"
            
            SQLBotLogUtil.info(f"Calling OAuth2 logout API: {url}")
            SQLBotLogUtil.debug(f"Request headers: {headers}")
            
            response = await client.get(
                url,
                headers=headers,
                timeout=30.0,
            )
            
            SQLBotLogUtil.info(f"OAuth2 logout API response status: {response.status_code}")
            SQLBotLogUtil.debug(f"OAuth2 logout API response: {response.text[:500]}")
            
            # 无论响应状态如何，都清除缓存
            await clear_cached_oauth2_access_token(user_account)
            
            if response.status_code == 200:
                return JSONResponse({
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "oauth2_enabled": True,
                        "logout_success": True
                    }
                })
            else:
                return JSONResponse({
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "oauth2_enabled": True,
                        "logout_success": False,
                        "error": f"OAuth2 logout API returned status {response.status_code}"
                    }
                })
                
    except httpx.TimeoutException:
        SQLBotLogUtil.error("OAuth2 logout API timeout")
        await clear_cached_oauth2_access_token(user_account)
        return JSONResponse({
            "code": 0,
            "msg": "success",
            "data": {
                "oauth2_enabled": True,
                "logout_success": False,
                "error": "OAuth2 logout API timeout"
            }
        })
    except Exception as e:
        SQLBotLogUtil.exception(f"Error calling OAuth2 logout API: {str(e)}")
        await clear_cached_oauth2_access_token(user_account)
        return JSONResponse({
            "code": 0,
            "msg": "success",
            "data": {
                "oauth2_enabled": True,
                "logout_success": False,
                "error": str(e)
            }
        })

