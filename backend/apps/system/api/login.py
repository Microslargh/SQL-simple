from typing import Annotated
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from apps.system.schemas.system_schema import BaseUserDTO, UserInfoDTO
from apps.system.models.user import UserModel
from apps.system.models.system_model import UserWsModel, WorkspaceModel
from apps.system.crud.user import get_user_by_account, get_user_info, get_db_user
from common.core.deps import SessionDep, Trans
from common.utils.crypto import sqlbot_decrypt
from ..crud.user import authenticate
from common.core.security import create_access_token, verify_dingtalk_signature
from datetime import timedelta
from common.core.config import settings
from common.core.schemas import Token
from sqlmodel import select, func
from common.utils.time import get_timestamp
from common.utils.utils import SQLBotLogUtil
import json

router = APIRouter(tags=["login"], prefix="/login")


def prepare_token_data(user_dict: dict) -> dict:
    """
    准备 token 数据，确保 id 作为字符串存储（避免 JavaScript 大数精度问题）
    """
    user_id = user_dict.get("id")
    if isinstance(user_id, str):
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            SQLBotLogUtil.error(f"Invalid user_id format: {user_id}")
            raise HTTPException(status_code=500, detail="Invalid user_id format")
    
    return {
        "id": str(int(user_id)) if user_id is not None else None,  # 作为字符串存储
        "account": user_dict.get("account"),
        "oid": int(user_dict.get("oid")) if user_dict.get("oid") is not None else None
    }


class AutoLoginRequest(BaseModel):
    """自动登录请求（仅需用户名）"""
    username: str = Field(..., min_length=1, max_length=100, description="用户名/账号")
    timestamp: str = Field(..., description="时间戳（秒）")
    sign: str = Field(..., description="签名")

@router.post("/access-token")
async def local_login(
    session: SessionDep,
    trans: Trans,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
) -> Token:
    """
    本地账号密码登录
    支持系统注册用户使用账号密码登录
    """
    origin_account = await sqlbot_decrypt(form_data.username)
    origin_pwd = await sqlbot_decrypt(form_data.password)
    user: BaseUserDTO = authenticate(session=session, account=origin_account, password=origin_pwd)
    if not user:
        raise HTTPException(status_code=400, detail=trans('i18n_login.account_pwd_error'))
    if not user.oid or user.oid == 0:
        raise HTTPException(status_code=400, detail=trans('i18n_login.no_associated_ws', msg = trans('i18n_concat_admin')))
    if user.status != 1:
        raise HTTPException(status_code=400, detail=trans('i18n_login.user_disable', msg = trans('i18n_concat_admin')))
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    user_dict = user.to_dict()
    token_data = prepare_token_data(user_dict)
    return Token(access_token=create_access_token(
        token_data, expires_delta=access_token_expires
    ))


@router.post("/dingtalk-login", response_model=Token)
async def dingtalk_login(
    session: SessionDep,
    request: AutoLoginRequest,
    http_request: Request
) -> Token:
    """
    钉钉登录接口（带签名校验）
    根据用户名自动创建用户（如果不存在）并完成登录，返回 token
    
    - 如果用户不存在：自动创建新用户并登录
    - 如果用户已存在：直接登录
    - 新用户会自动关联到默认工作空间（oid=1）
    - 如果是系统中的第一个用户，会自动设置为工作空间管理员
    
    安全要求：
    - 必须提供有效的签名（sign）和时间戳（timestamp）
    - 签名使用HMAC-SHA256算法，基于请求参数和DINGTALK_APP_SECRET计算
    - 时间戳必须在有效期内（默认5分钟），防止重放攻击
    """
    # 检查钉钉登录是否启用
    if not settings.DINGTALK_ENABLED:
        raise HTTPException(status_code=403, detail="DingTalk login is not enabled")
    
    # 检查应用密钥是否配置
    if not settings.DINGTALK_APP_SECRET:
        raise HTTPException(status_code=500, detail="DingTalk app secret is not configured")
    
    account = request.username.strip()
    if not account:
        raise HTTPException(status_code=400, detail="Username cannot be empty")
    
    # 验证签名
    try:
        # 构建请求参数（用于签名验证）
        params = {
            "username": account,
            "timestamp": request.timestamp,
        }
        
        # 验证签名
        is_valid = verify_dingtalk_signature(
            timestamp=request.timestamp,
            signature=request.sign,
            params=params,
            app_secret=settings.DINGTALK_APP_SECRET
        )
        
        if not is_valid:
            SQLBotLogUtil.warning(f"Invalid DingTalk signature for account: {account}")
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        SQLBotLogUtil.info(f"DingTalk signature verified for account: {account}")
    except HTTPException:
        raise
    except Exception as e:
        SQLBotLogUtil.error(f"DingTalk signature verification error: {str(e)}")
        raise HTTPException(status_code=401, detail="Signature verification failed")
    
    SQLBotLogUtil.info(f"Auto login request for account: {account}")
    
    # 1. 查询用户是否存在
    db_user = get_user_by_account(session=session, account=account)
    
    if db_user:
        # 用户已存在，直接登录
        SQLBotLogUtil.info(f"User exists, logging in: account={account}, id={db_user.id}")
        
        # 检查用户状态
        if db_user.status != 1:
            raise HTTPException(status_code=400, detail="User is disabled")
        
        if not db_user.oid or db_user.oid == 0:
            raise HTTPException(status_code=400, detail="User has no associated workspace")
        
        # 获取完整用户信息
        user_info = await get_user_info(session=session, user_id=db_user.id)
        if not user_info:
            raise HTTPException(status_code=500, detail="Failed to get user info")
        
        # 生成 token
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        # 处理缓存可能返回字典的情况
        if isinstance(user_info, dict):
            user_dict = user_info
        else:
            user_dict = user_info.model_dump()
        # 转换为 token 格式（只需要 id, account, oid）
        token_data = prepare_token_data(user_dict)
        token = create_access_token(token_data, expires_delta=access_token_expires)
        
        SQLBotLogUtil.info(f"Auto login successful for existing user: account={account}")
        return Token(access_token=token)
    
    else:
        # 用户不存在，创建新用户
        SQLBotLogUtil.info(f"User not found, creating new user: account={account}")
        
        # 检查是否是系统中的第一个用户
        user_count = session.exec(
            select(func.count(UserModel.id))
        ).first() or 0
        
        is_first_user = user_count == 0
        SQLBotLogUtil.info(f"User count: {user_count}, is_first_user: {is_first_user}")
        
        # 获取或创建默认工作空间（oid=1）
        default_workspace = session.exec(
            select(WorkspaceModel).where(WorkspaceModel.id == 1)
        ).first()
        
        if not default_workspace:
            # 创建默认工作空间
            default_workspace = WorkspaceModel(
                id=1,
                name="默认工作空间",
                description="Default workspace",
                create_time=get_timestamp()
            )
            session.add(default_workspace)
            session.commit()
            SQLBotLogUtil.info("Created default workspace (oid=1)")
        
        # 创建新用户
        new_user = UserModel(
            account=account,
            name=account,  # 使用账号作为默认名称
            email=f"{account}@example.com",  # 默认邮箱
            password="",  # 自动登录用户不需要密码
            status=1,  # 启用
            oid=1,  # 默认工作空间
            language="zh-CN",
            register_type=2  # 钉钉用户注册
        )
        session.add(new_user)
        session.commit()
        session.refresh(new_user)
        SQLBotLogUtil.info(f"Created new user: id={new_user.id}, account={account}")
        
        # 创建用户工作空间关联
        # 如果是第一个用户，设置为工作空间管理员（weight > 0）
        user_weight = 1 if is_first_user else 0
        SQLBotLogUtil.info(f"Setting user weight to {user_weight} (is_first_user: {is_first_user})")
        
        user_ws = UserWsModel(
            uid=new_user.id,
            oid=1,
            weight=user_weight
        )
        session.add(user_ws)
        session.commit()
        SQLBotLogUtil.info(f"Created user-workspace association: uid={new_user.id}, oid=1, weight={user_weight}")
        
        # 获取用户信息
        user_info = await get_user_info(session=session, user_id=new_user.id)
        if not user_info:
            raise HTTPException(status_code=500, detail="Failed to get user info after creation")
        
        # 生成 token
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        # 处理缓存可能返回字典的情况
        if isinstance(user_info, dict):
            user_dict = user_info
        else:
            user_dict = user_info.model_dump()
        # 转换为 token 格式（只需要 id, account, oid）
        token_data = prepare_token_data(user_dict)
        token = create_access_token(token_data, expires_delta=access_token_expires)
        
        SQLBotLogUtil.info(f"Auto login successful for new user: account={account}, id={new_user.id}")
        return Token(access_token=token)


def _load_template(template_name: str) -> str:
    """加载HTML模板文件"""
    template_dir = Path(__file__).parent.parent / "templates"
    template_path = template_dir / template_name
    if not template_path.exists():
        raise FileNotFoundError(f"Template file not found: {template_path}")
    with open(template_path, 'r', encoding='utf-8') as f:
        return f.read()


@router.get("/autoLogin", response_class=HTMLResponse)
async def auto_login_page(session: SessionDep):
    """
    直接登录页面接口
    如果 AUTO_LOGIN_ENABLED 开启，返回用户列表页面；否则返回错误页面
    """
    if not settings.AUTO_LOGIN_ENABLED:
        # 返回错误页面HTML
        error_html = _load_template("auto_login_error.html")
        return HTMLResponse(content=error_html, status_code=403)
    
    # 获取所有启用的用户
    stmt = select(UserModel.id, UserModel.account, UserModel.name, UserModel.email, UserModel.status).where(
        UserModel.status == 1  # 排除 admin 用户
    ).order_by(UserModel.account)
    
    users = session.exec(stmt).all()
    
    # 转换为字典列表
    user_list = []
    for user in users:
        user_list.append({
            "id": user.id,
            "account": user.account,
            "name": user.name or user.account,
            "email": user.email or "",
        })
    
    # 生成用户列表HTML
    import html
    import base64
    users_html = ""
    for user in user_list:
        avatar_text = (user["name"][0] if user["name"] else user["account"][0]).upper()
        user_name_escaped = html.escape(user['name'])
        user_account_escaped = html.escape(user['account'])
        user_email_escaped = html.escape(user['email']) if user['email'] else ""
        # 使用 base64 编码避免引号和特殊字符问题
        user_name_encoded = base64.b64encode(user['name'].encode('utf-8')).decode('utf-8')
        user_account_encoded = base64.b64encode(user['account'].encode('utf-8')).decode('utf-8')
        # 将用户ID编码为base64，避免JavaScript大数精度问题
        user_id_encoded = base64.b64encode(str(user['id']).encode('utf-8')).decode('utf-8')
        email_html = f"<div class='user-email'>{user_email_escaped}</div>" if user['email'] else ""
        users_html += f"""
        <div class="user-card" data-user-id-enc="{user_id_encoded}" data-user-name-enc="{user_name_encoded}" data-user-account-enc="{user_account_encoded}">
            <div class="user-info">
                <div class="user-avatar">{avatar_text}</div>
                <div class="user-details">
                    <div class="user-name">{user_name_escaped}</div>
                    <div class="user-account">{user_account_escaped}</div>
                    {email_html}
                </div>
                <div class="login-icon">→</div>
            </div>
        </div>
        """
    
    # 如果没有用户，显示空状态
    if not users_html:
        users_html = '<div class="empty-state">暂无可用用户</div>'
    
    # 加载模板并替换占位符
    html_content = _load_template("auto_login_user_list.html")
    html_content = html_content.replace("{{users_html}}", users_html)
    html_content = html_content.replace("{{frontend_host}}", settings.FRONTEND_HOST)
    
    return HTMLResponse(content=html_content)


class DirectLoginRequest(BaseModel):
    """直接登录请求"""
    user_id: str = Field(..., description="用户ID（字符串格式以避免JavaScript大数精度问题）")


@router.post("/autoLogin/direct")
async def direct_login(session: SessionDep, request: DirectLoginRequest) -> Token:
    """
    直接登录接口（根据用户ID）
    如果 AUTO_LOGIN_ENABLED 开启，允许直接登录；否则返回错误
    """
    try:
        # 处理用户ID：将字符串转换为整数（避免JavaScript大数精度问题）
        try:
            user_id = int(request.user_id)
        except (ValueError, TypeError) as e:
            SQLBotLogUtil.error(f"Invalid user_id format: {request.user_id}, error: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid user_id format: {request.user_id}")
        
        SQLBotLogUtil.info(f"Direct login request received for user_id: {user_id} (original string: {request.user_id})")
        
        if not settings.AUTO_LOGIN_ENABLED:
            SQLBotLogUtil.warning("Direct login attempted but AUTO_LOGIN_ENABLED is False")
            raise HTTPException(status_code=403, detail="Auto login is not enabled")
        
        # 查询用户（通过ID）
        SQLBotLogUtil.info(f"Querying user with user_id: {user_id} (type: {type(user_id).__name__})")
        db_user = get_db_user(session=session, user_id=user_id)
        
        if not db_user:
            SQLBotLogUtil.warning(f"User not found: user_id={user_id} (original string: {request.user_id})")
            raise HTTPException(status_code=404, detail="User not found")
        
        SQLBotLogUtil.info(f"User found: id={db_user.id}, account={db_user.account}, status={db_user.status}, oid={db_user.oid}")
        
        # 检查用户状态
        if db_user.status != 1:
            SQLBotLogUtil.warning(f"User is disabled: user_id={db_user.id}")
            raise HTTPException(status_code=400, detail="User is disabled")
        
        if not db_user.oid or db_user.oid == 0:
            SQLBotLogUtil.warning(f"User has no associated workspace: user_id={db_user.id}")
            raise HTTPException(status_code=400, detail="User has no associated workspace")
        
        # 获取完整用户信息
        user_info = await get_user_info(session=session, user_id=db_user.id)
        if not user_info:
            SQLBotLogUtil.error(f"Failed to get user info: user_id={db_user.id}")
            raise HTTPException(status_code=500, detail="Failed to get user info")
        
        # 生成 token
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        if isinstance(user_info, dict):
            user_dict = user_info
        else:
            user_dict = user_info.model_dump()
        
        # 转换为 token 格式（只需要 id, account, oid）
        token_data = prepare_token_data(user_dict)
        SQLBotLogUtil.info(f"Creating token with data: id={token_data['id']} (as string), account={token_data['account']}, oid={token_data['oid']}")
        token = create_access_token(token_data, expires_delta=access_token_expires)
        
        SQLBotLogUtil.info(f"Direct login successful for user: id={db_user.id}, account={db_user.account}")
        return Token(access_token=token)
    except HTTPException:
        raise
    except Exception as e:
        SQLBotLogUtil.error(f"Direct login error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Login failed: {str(e)}")