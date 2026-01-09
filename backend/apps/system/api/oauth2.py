import json
import secrets
import urllib.parse
import hashlib
import time
from typing import Optional, Dict
from fastapi import APIRouter, Query, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session
from common.core.config import settings
from common.core.db import engine
from common.core.security import create_access_token
from common.core.schemas import Token
from datetime import timedelta
from apps.system.crud.user import get_user_by_account, get_user_info, clean_user_cache, clean_user_cache
from apps.system.models.user import UserModel
from apps.system.models.system_model import UserWsModel, WorkspaceModel
from apps.system.schemas.system_schema import UserInfoDTO
from apps.system.schemas.auth import CacheNamespace, CacheName
from common.utils.utils import SQLBotLogUtil
from common.core.sqlbot_cache import is_cache_initialized
from fastapi_cache import FastAPICache
import httpx
from pydantic import BaseModel
import base64


router = APIRouter(tags=["oauth2"], prefix="/oauth2")


class OAuth2ConfigResponse(BaseModel):
    """OAuth2配置响应"""
    enabled: bool
    authorization_url: str
    client_id: str
    redirect_uri: str
    scope: str
    response_type: str
    logout_url: str = ""  # OAuth2登出接口地址


class OAuth2AuthUrlResponse(BaseModel):
    """OAuth2授权URL响应"""
    auth_url: str  # 包含state的完整授权URL


@router.get("/config")
async def get_oauth2_config() -> OAuth2ConfigResponse:
    """
    获取OAuth2配置信息（用于前端直接跳转）
    """
    return OAuth2ConfigResponse(
        enabled=settings.OAUTH2_ENABLED,
        authorization_url=settings.OAUTH2_AUTHORIZATION_URL,
        client_id=settings.OAUTH2_CLIENT_ID,
        redirect_uri=settings.OAUTH2_REDIRECT_URI or f"http://localhost:8000{settings.API_V1_STR}/callback",
        scope=settings.OAUTH2_SCOPE,
        response_type=settings.OAUTH2_RESPONSE_TYPE,
        logout_url=settings.OAUTH2_LOGOUT_URL,
    )


@router.get("/auth-url")
async def get_oauth2_auth_url() -> OAuth2AuthUrlResponse:
    """
    获取OAuth2授权URL（包含state参数，state已缓存用于验证）
    前端应使用此接口获取授权URL，而不是自己构建
    """
    if not settings.OAUTH2_ENABLED:
        raise HTTPException(status_code=400, detail="OAuth2 SSO is not enabled")
    
    auth_url = await build_oauth2_auth_url()
    if not auth_url:
        raise HTTPException(status_code=400, detail="OAuth2 configuration is invalid")
    
    return OAuth2AuthUrlResponse(auth_url=auth_url)


def generate_state() -> str:
    """生成随机state参数，用于防止CSRF攻击"""
    return secrets.token_urlsafe(32)


def generate_request_id() -> str:
    """生成随机请求ID"""
    return secrets.token_urlsafe(16)


async def cache_oauth2_access_token(account: str, access_token: str, expire: int = 3600):
    """
    缓存OAuth2 access_token（使用account作为key）
    
    Args:
        account: 用户账号（作为缓存key）
        access_token: 要缓存的access_token
        expire: 过期时间（秒），默认3600秒（1小时）
    """
    if not is_cache_initialized():
        SQLBotLogUtil.debug("Cache not initialized, skipping OAuth2 access_token cache")
        return
    
    try:
        namespace = str(CacheNamespace.AUTH_INFO)
        cache_name = str(CacheName.OAUTH2_ACCESS_TOKEN)
        cache_key = f"{namespace}:{cache_name}:{account}"
        
        backend = FastAPICache.get_backend()
        await backend.set(cache_key, access_token, expire=expire)
        SQLBotLogUtil.info(f"Cached OAuth2 access_token for account: {account} (expire: {expire}s)")
    except Exception as e:
        SQLBotLogUtil.exception(f"Failed to cache OAuth2 access_token: {str(e)}")


async def get_cached_oauth2_access_token(account: str) -> Optional[str]:
    """
    从缓存中获取OAuth2 access_token（使用account作为key）
    
    Args:
        account: 用户账号（作为缓存key）
    
    Returns:
        access_token 如果存在，否则返回 None
    """
    if not is_cache_initialized():
        return None
    
    try:
        namespace = str(CacheNamespace.AUTH_INFO)
        cache_name = str(CacheName.OAUTH2_ACCESS_TOKEN)
        cache_key = f"{namespace}:{cache_name}:{account}"
        
        backend = FastAPICache.get_backend()
        cached_token = await backend.get(cache_key)
        if cached_token:
            SQLBotLogUtil.info(f"Retrieved cached OAuth2 access_token for account: {account}")
            return cached_token
        return None
    except Exception as e:
        SQLBotLogUtil.exception(f"Failed to get cached OAuth2 access_token: {str(e)}")
        return None


async def clear_cached_oauth2_access_token(account: str):
    """
    清除缓存的OAuth2 access_token
    
    Args:
        account: 用户账号（作为缓存key）
    """
    if not is_cache_initialized():
        return
    
    try:
        namespace = str(CacheNamespace.AUTH_INFO)
        cache_name = str(CacheName.OAUTH2_ACCESS_TOKEN)
        cache_key = f"{namespace}:{cache_name}:{account}"
        
        backend = FastAPICache.get_backend()
        if settings.CACHE_TYPE.lower() == "redis":
            redis = backend.redis
            await redis.delete(cache_key)
        else:
            await backend.clear(key=cache_key)
        SQLBotLogUtil.info(f"Cleared cached OAuth2 access_token for account: {account}")
    except Exception as e:
        SQLBotLogUtil.exception(f"Failed to clear cached OAuth2 access_token: {str(e)}")


async def cache_oauth2_state(state: str, expire: int = 600) -> bool:
    """
    缓存OAuth2 state参数（用于验证回调请求）
    
    Args:
        state: state参数值
        expire: 过期时间（秒），默认600秒（10分钟）
    
    Returns:
        bool: 是否成功缓存
    """
    if not is_cache_initialized():
        SQLBotLogUtil.debug("Cache not initialized, skipping OAuth2 state cache")
        return False
    
    try:
        namespace = str(CacheNamespace.AUTH_INFO)
        cache_name = str(CacheName.OAUTH2_STATE)
        cache_key = f"{namespace}:{cache_name}:{state}"
        
        backend = FastAPICache.get_backend()
        # 存储state，值为"valid"表示有效
        await backend.set(cache_key, "valid", expire=expire)
        SQLBotLogUtil.info(f"Cached OAuth2 state: {state[:16]}... (expire: {expire}s)")
        return True
    except Exception as e:
        SQLBotLogUtil.exception(f"Failed to cache OAuth2 state: {str(e)}")
        return False


async def verify_and_consume_oauth2_state(state: str) -> bool:
    """
    验证并消费OAuth2 state参数（一次性使用）
    
    Args:
        state: 要验证的state参数值
    
    Returns:
        bool: state是否有效且未被使用过
    """
    if not state:
        SQLBotLogUtil.warning("OAuth2 state is empty")
        return False
    
    if not is_cache_initialized():
        SQLBotLogUtil.warning("Cache not initialized, cannot verify OAuth2 state")
        return False
    
    try:
        namespace = str(CacheNamespace.AUTH_INFO)
        cache_name = str(CacheName.OAUTH2_STATE)
        cache_key = f"{namespace}:{cache_name}:{state}"
        
        backend = FastAPICache.get_backend()
        cached_value = await backend.get(cache_key)
        if not cached_value:
            SQLBotLogUtil.warning(f"OAuth2 state not found or expired: {state[:16]}...")
            return False
        
        # 验证通过后，立即删除state（一次性使用）
        if settings.CACHE_TYPE.lower() == "redis":
            from common.core.sqlbot_cache import redis
            await redis.delete(cache_key)
        else:
            await backend.clear(key=cache_key)
        
        SQLBotLogUtil.info(f"OAuth2 state verified and consumed: {state[:16]}...")
        return True
    except Exception as e:
        SQLBotLogUtil.exception(f"Failed to verify OAuth2 state: {str(e)}")
        return False


def build_oauth2_headers(app_method: str) -> Dict[str, str]:
    """
    构建OAuth2请求头
    根据需求构造请求头，包含signInfo签名
    
    Args:
        app_method: 应用方法名，如 "getToken" 或 "getUserInfo"
    
    Returns:
        包含所有必需请求头的字典
    """
    # 生成随机请求ID
    request_id = generate_request_id()
    
    # 获取时间戳（秒级，字符串格式）
    timestamp = str(int(time.time()))
    
    # 从配置中获取参数
    version = "1"  # 空字符串
    app_key = settings.UAP_APP_KEY
    app_id = settings.UAP_APP_ID
    app_id_param = settings.UAP_APP_ID_PARAM
    app_secret = settings.UAP_APP_SECRET
    app_code = settings.UAP_APP_ID  # 空字符串
    format_type = "json"
    
    # 计算 signInfo: md5(version + appKey + appMethod + timestamp + format + appSecret)
    sign_string = f"{version}{app_key}{app_method}{timestamp}{format_type}{app_secret}"
    sign_info = hashlib.md5(sign_string.encode('utf-8')).hexdigest()
    
    # 构建请求头
    headers = {
        "requestId": request_id,
        "version": version,
        "appId": app_id,
        "timestamp": timestamp,
        "format": format_type,
        "appKey": app_key,
        "appCode": app_code,
        "appIdParam": app_id_param,
        "appMethod": app_method,
        "signInfo": sign_info,
    }
    
    SQLBotLogUtil.info(f"Built OAuth2 headers for method {app_method}: requestId={request_id}, timestamp={timestamp}, signInfo={sign_info[:16]}...")
    
    return headers


async def build_oauth2_auth_url() -> Optional[str]:
    """
    构建OAuth2授权URL，并缓存state参数
    
    Returns:
        OAuth2授权URL，如果配置无效则返回None
    """
    if not settings.OAUTH2_ENABLED or not settings.OAUTH2_AUTHORIZATION_URL:
        return None
    
    # 生成state并缓存（有效期10分钟）
    state = generate_state()
    cache_success = await cache_oauth2_state(state, expire=600)
    if not cache_success:
        SQLBotLogUtil.warning("Failed to cache OAuth2 state, but continuing...")
    
    params = {
        "client_id": settings.OAUTH2_CLIENT_ID,
        "redirect_uri": settings.OAUTH2_REDIRECT_URI or f"http://localhost:8000{settings.API_V1_STR}/callback",
        "response_type": settings.OAUTH2_RESPONSE_TYPE,
        "scope": settings.OAUTH2_SCOPE,
        "state": state,
    }
    
    auth_url = f"{settings.OAUTH2_AUTHORIZATION_URL}?{urllib.parse.urlencode(params)}"
    return auth_url


@router.get("/login")
async def oauth2_login(request: Request):
    """
    OAuth2登录跳转接口
    未登录用户访问此接口会被重定向到OAuth2授权服务器
    """
    if not settings.OAUTH2_ENABLED:
        raise HTTPException(status_code=400, detail="OAuth2 SSO is not enabled")
    
    auth_url = await build_oauth2_auth_url()
    if not auth_url:
        raise HTTPException(status_code=400, detail="OAuth2 configuration is invalid")
    
    SQLBotLogUtil.info(f"OAuth2 login redirect: {auth_url}")
    return RedirectResponse(url=auth_url)


async def get_access_token(code: str) -> Optional[str]:
    """
    使用授权码换取访问令牌
    支持多种OAuth2服务器格式：
    1. 标准form-urlencoded格式
    2. JSON格式
    3. Basic Auth认证
    """
    redirect_uri = settings.OAUTH2_REDIRECT_URI or f"http://localhost:8000{settings.API_V1_STR}/callback"
    
    SQLBotLogUtil.info(f"Getting access token from: {settings.OAUTH2_TOKEN_URL}")
    SQLBotLogUtil.info(f"Using redirect_uri: {redirect_uri}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0,verify=False) as client:
            # 准备请求数据
            data = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": settings.OAUTH2_CLIENT_ID,
                "client_secret": settings.OAUTH2_CLIENT_SECRET,
            }
            
            # 构建OAuth2请求头（包含signInfo签名）
            oauth2_headers = build_oauth2_headers("/authcenter/getOauth2Token")
            # 合并Content-Type头
            headers = {
                **oauth2_headers,
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            # 尝试方式1：标准form-urlencoded格式
            try:
                SQLBotLogUtil.info("Trying form-urlencoded format...")
                SQLBotLogUtil.info(f"Request headers: {headers}")
                response = await client.post(
                    settings.OAUTH2_TOKEN_URL,
                    data=data,
                    headers=headers,
                    timeout=30.0
                )
                
                SQLBotLogUtil.info(f"Token response status: {response.status_code}")
                SQLBotLogUtil.info(f"First request response status: {response.status_code}, will check if need to try other formats")
                
                if response.status_code == 200:
                    try:
                        # 尝试解析JSON响应
                        token_data = response.json()
                        SQLBotLogUtil.info(f"Token response data: {token_data}")
                        
                        # 尝试多种可能的字段名和嵌套结构

                        # 1. 直接获取
                        access_token = (
                            token_data.get("access_token") or 
                            token_data.get("accessToken") or
                            token_data.get("token")
                        )
                        
                        # 2. 如果直接获取失败，尝试从data字段获取
                        if not access_token and isinstance(token_data.get("data"), dict):
                            data = token_data.get("data")
                            access_token = (
                                data.get("access_token") or 
                                data.get("accessToken") or
                                data.get("token")
                            )
                        
                        # 3. 如果还是失败，尝试从result字段获取
                        if not access_token and isinstance(token_data.get("result"), dict):
                            result = token_data.get("result")
                            access_token = (
                                result.get("access_token") or 
                                result.get("accessToken") or
                                result.get("token")
                            )
                        
                        if access_token:
                            SQLBotLogUtil.info("Successfully got access token")
                            return access_token
                        else:
                            SQLBotLogUtil.error(f"Access token not found in response. Response keys: {list(token_data.keys()) if isinstance(token_data, dict) else 'Not a dict'}")
                            SQLBotLogUtil.error(f"Full response: {token_data}")
                            return None
                    except Exception as json_error:
                        # 如果不是JSON格式，尝试解析文本
                        SQLBotLogUtil.error(f"Failed to parse JSON response: {json_error}")
                        SQLBotLogUtil.error(f"Response text: {response.text[:500]}")
                        return None
                elif response.status_code == 405:
                    # 方法不允许，尝试JSON格式
                    SQLBotLogUtil.warning(f"Method not allowed (405), trying JSON format...")
                    # 重新构建请求头（时间戳会更新）
                    oauth2_headers = build_oauth2_headers("/authcenter/getOauth2Token")
                    json_headers = {
                        **oauth2_headers,
                        "Content-Type": "application/json"
                    }
                    SQLBotLogUtil.info(f"Request headers (JSON): {json_headers}")
                    response = await client.post(
                        settings.OAUTH2_TOKEN_URL,
                        json=data,
                        headers=json_headers,
                        timeout=30.0
                    )
                    
                    if response.status_code == 200:
                        try:
                            token_data = response.json()
                            SQLBotLogUtil.info(f"Token response data (JSON format): {token_data}")
                            access_token = (
                                token_data.get("access_token") or 
                                token_data.get("accessToken") or
                                token_data.get("token") or
                                token_data.get("data", {}).get("access_token") if isinstance(token_data.get("data"), dict) else None
                            )
                            if access_token:
                                SQLBotLogUtil.info("Successfully got access token (JSON format)")
                                return access_token
                        except Exception as e:
                            SQLBotLogUtil.error(f"Failed to parse JSON response: {e}, Response: {response.text[:500]}")
                    
                    SQLBotLogUtil.error(f"Failed to get access token (JSON format): {response.status_code} - {response.text}")
                    return None
                else:
                    SQLBotLogUtil.error(f"Failed to get access token: {response.status_code} - {response.text}")
                    # 尝试使用Basic Auth
                    if response.status_code == 401:
                        SQLBotLogUtil.info("Trying Basic Auth format...")
                        auth_string = f"{settings.OAUTH2_CLIENT_ID}:{settings.OAUTH2_CLIENT_SECRET}"
                        auth_bytes = auth_string.encode('ascii')
                        auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
                        
                        basic_auth_data = {
                            "grant_type": "authorization_code",
                            "code": code,
                            "redirect_uri": redirect_uri,
                        }
                        
                        # 重新构建请求头（时间戳会更新）
                        oauth2_headers = build_oauth2_headers("/authcenter/getOauth2Token")
                        basic_auth_headers = {
                            **oauth2_headers,
                            "Content-Type": "application/x-www-form-urlencoded",
                            "Authorization": f"Basic {auth_b64}"
                        }
                        SQLBotLogUtil.info(f"Request headers (Basic Auth): {basic_auth_headers}")
                        
                        response = await client.post(
                            settings.OAUTH2_TOKEN_URL,
                            data=basic_auth_data,
                            headers=basic_auth_headers,
                            timeout=30.0
                        )
                        
                        if response.status_code == 200:
                            try:
                                token_data = response.json()
                                SQLBotLogUtil.info(f"Token response data (Basic Auth): {token_data}")
                                access_token = (
                                    token_data.get("access_token") or 
                                    token_data.get("accessToken") or
                                    token_data.get("token") or
                                    token_data.get("data", {}).get("access_token") if isinstance(token_data.get("data"), dict) else None
                                )
                                if access_token:
                                    SQLBotLogUtil.info("Successfully got access token (Basic Auth)")
                                    return access_token
                            except Exception as e:
                                SQLBotLogUtil.error(f"Failed to parse JSON response (Basic Auth): {e}, Response: {response.text[:500]}")
                    
                    return None
                    
            except httpx.TimeoutException as e:
                SQLBotLogUtil.error(f"Request timeout when getting access token: {str(e)}")
                return None
            except Exception as e:
                SQLBotLogUtil.exception(f"Error in get_access_token: {str(e)}")
                return None
                
    except httpx.TimeoutException as e:
        SQLBotLogUtil.error(f"Connection timeout when getting access token: {str(e)}")
        SQLBotLogUtil.error(f"Please check if {settings.OAUTH2_TOKEN_URL} is accessible")
        return None
    except httpx.ConnectError as e:
        SQLBotLogUtil.error(f"Connection error when getting access token: {str(e)}")
        SQLBotLogUtil.error(f"Please check if {settings.OAUTH2_TOKEN_URL} is accessible")
        return None
    except Exception as e:
        SQLBotLogUtil.exception(f"Unexpected error getting access token: {str(e)}")
        return None


async def get_userinfo(access_token: str) -> Optional[dict]:
    """
    使用访问令牌获取用户信息
    """
    try:
        async with httpx.AsyncClient(verify=False) as client:
            # 构建OAuth2请求头（包含signInfo签名）
            oauth2_headers = build_oauth2_headers("/authcenter/getOauth2UserInfo")
            
            # 合并Authorization和Accept头
            headers = {
                **oauth2_headers,
                "Accept": "application/json",
            }
            
            SQLBotLogUtil.info(f"Getting userinfo from: {settings.OAUTH2_USERINFO_URL}")
            SQLBotLogUtil.info(f"Request headers: {headers}")
            
            response = await client.post(
                settings.OAUTH2_USERINFO_URL,
                headers=headers,
                timeout=30.0,
                data={
                    "access_token":access_token,
                    "client_id":settings.OAUTH2_CLIENT_ID,
                }
            )
            
            SQLBotLogUtil.info(f"Userinfo response status: {response.status_code}")
            
            if response.status_code == 200:
                userinfo = response.json()
                SQLBotLogUtil.info(f"Userinfo received: {userinfo}")
                
                # 处理可能的嵌套结构（如 {code: 200, data: {...}}）
                if isinstance(userinfo, dict):
                    # 如果响应包含data字段，提取data内容
                    if "data" in userinfo and isinstance(userinfo.get("data"), dict):
                        userinfo = userinfo.get("data")
                        SQLBotLogUtil.info(f"Extracted userinfo from data field: {userinfo}")
                
                return userinfo
            else:
                SQLBotLogUtil.error(f"Failed to get userinfo: {response.status_code} - {response.text}")
                return None
    except httpx.TimeoutException as e:
        SQLBotLogUtil.error(f"Timeout when getting userinfo: {str(e)}")
        return None
    except Exception as e:
        SQLBotLogUtil.exception(f"Error getting userinfo: {str(e)}")
        return None


async def create_or_get_user(userinfo: dict, session: Session) -> UserInfoDTO:
    """
    根据OAuth2用户信息创建或获取系统用户
    支持多种用户信息格式：
    - 标准格式: {username, name, email}
    - 自定义格式: {username, nickname, ...}
    - 嵌套格式: {data: {username, ...}}
    
    如果用户已存在（可能是钉钉用户），会自动补充缺失的信息（如姓名、部门名称等）
    """
    SQLBotLogUtil.info(f"Processing userinfo: {userinfo}")

    # 查找用户（使用UserModel而不是BaseUserDTO，以便更新）
    from sqlmodel import select
    account = userinfo.get('usercode', '')
    db_user = session.exec(
        select(UserModel).where(UserModel.account == account)
    ).first()
    
    if db_user:
        # 用户已存在，检查并补充缺失信息
        SQLBotLogUtil.info(f"User already exists: account={account}, id={db_user.id}")
        
        # 标记是否需要更新
        need_update = False
        updates = {}
        
        # 检查并补充用户姓名（如果为空或等于account）
        oauth2_name = userinfo.get('username', '').strip()
        if oauth2_name and (not db_user.name or db_user.name.strip() == '' or db_user.name == db_user.account):
            updates['name'] = oauth2_name
            need_update = True
            SQLBotLogUtil.info(f"Will update name: '{db_user.name}' -> '{oauth2_name}'")
        
        # 检查并补充部门名称（如果为空）
        oauth2_orgname = userinfo.get('orgname', '').strip()
        if oauth2_orgname and (not db_user.orgname or db_user.orgname.strip() == ''):
            updates['orgname'] = oauth2_orgname
            need_update = True
            SQLBotLogUtil.info(f"Will update orgname: '{db_user.orgname}' -> '{oauth2_orgname}'")
        
        # 检查并补充部门编码（如果为空）
        oauth2_userorg = userinfo.get('userorg', '').strip()
        if oauth2_userorg and (not db_user.userorg or db_user.userorg.strip() == ''):
            updates['userorg'] = oauth2_userorg
            need_update = True
            SQLBotLogUtil.info(f"Will update userorg: '{db_user.userorg}' -> '{oauth2_userorg}'")


        # 如果需要进行更新
        if need_update:
            SQLBotLogUtil.info(f"Updating user info: {updates}")
            for key, value in updates.items():
                setattr(db_user, key, value)
            
            session.add(db_user)
            session.commit()
            session.refresh(db_user)
            SQLBotLogUtil.info(f"User info updated for user: {account}")
        
        # 无论是否有更新，都清除用户缓存，确保获取最新的用户信息（包括 weight 等权限信息）
        await clean_user_cache(db_user.id)
        SQLBotLogUtil.info(f"User cache cleared for user: {account}")
        
        # 获取更新后的用户信息
        user = await get_user_info(session=session, user_id=db_user.id)
        if user:
            return user
    else:
        # 创建新用户
        SQLBotLogUtil.info(f"Creating new user from OAuth2: account={userinfo['usercode']})")

        # 检查是否是系统中的第一个用户
        from sqlmodel import select, func
        user_count = session.exec(
            select(func.count(UserModel.id))
        ).first() or 0
        
        is_first_user = user_count == 0
        SQLBotLogUtil.info(f"User count: {user_count}, is_first_user: {is_first_user}")
        
        # 获取默认工作空间（oid=1）或创建
        default_workspace = session.exec(
            select(WorkspaceModel).where(WorkspaceModel.id == 1)
        ).first()
        
        if not default_workspace:
            # 创建默认工作空间
            from common.utils.time import get_timestamp
            default_workspace = WorkspaceModel(
                id=1,
                name="默认工作空间",
                description="Default workspace",
                create_time=get_timestamp()
            )
            session.add(default_workspace)
            session.commit()
        
        # 创建用户
        new_user = UserModel(
            account=userinfo['usercode'],
            name=userinfo['username'],
            email=f"{userinfo['usercode']}@example.com",
            orgname=userinfo['orgname'],
            userorg=userinfo['userorg'],
            password="",  # OAuth2用户不需要密码
            status=1,  # 启用
            oid=1,  # 默认工作空间
            language="zh-CN",
            register_type=1  # 4a平台注册
        )
        session.add(new_user)
        session.commit()
        session.refresh(new_user)
        
        # 创建用户工作空间关联
        # 如果是第一个用户，设置为工作空间管理员（weight > 0）
        # weight > 0 表示有工作空间管理员权限，可以访问 /set 菜单
        user_weight = 1 if is_first_user else 0
        SQLBotLogUtil.info(f"Setting user weight to {user_weight} (is_first_user: {is_first_user})")
        
        user_ws = UserWsModel(
            uid=new_user.id,
            oid=1,
            weight=user_weight
        )
        session.add(user_ws)
        session.commit()
        
        # 获取用户信息
        user = await get_user_info(session=session, user_id=new_user.id)
        if user:
            return user
    
    raise HTTPException(status_code=500, detail="Failed to create or get user")
