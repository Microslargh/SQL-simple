from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext
import hashlib
from common.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


ALGORITHM = "HS256"


def create_access_token(data: dict | Any, expires_delta: timedelta) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode.update({"exp": expire})
    # to_encode = {"exp": expire, "account": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def md5pwd(password: str) -> str:
    m = hashlib.md5()
    m.update(password.encode("utf-8"))
    return m.hexdigest()

def verify_md5pwd(plain_password: str, md5_password: str) -> bool:
    return md5pwd(plain_password) == md5_password

def default_pwd() -> str:
    return settings.DEFAULT_PWD

def default_md5_pwd() -> str:
    pwd = default_pwd()
    return md5pwd(pwd)

def verify_dingtalk_signature(timestamp: str, signature: str, params: dict, app_secret: str) -> bool:
    """
    验证钉钉签名
    
    Args:
        timestamp: 时间戳（秒）
        signature: 请求中的签名
        params: 请求参数（不包括sign和timestamp）
        app_secret: 钉钉应用密钥
    
    Returns:
        bool: 签名是否有效
    """
    import hmac
    import hashlib
    import time
    from common.utils.utils import SQLBotLogUtil
    
    try:
        # 验证时间戳（防止重放攻击）
        current_time = int(time.time())
        request_time = int(timestamp)
        
        # 检查时间戳是否在有效期内（默认5分钟）
        timeout = settings.DINGTALK_SIGNATURE_TIMEOUT
        if abs(current_time - request_time) > timeout:
            SQLBotLogUtil.warning(f"DingTalk signature timestamp expired: {request_time}, current: {current_time}, timeout: {timeout}")
            return False
        
        # 按字典序排序参数
        sorted_params = sorted(params.items())
        
        # 构建待签名字符串：key1=value1&key2=value2...
        sign_string = '&'.join([f"{k}={v}" for k, v in sorted_params if k not in ['sign', 'timestamp']])
        
        # 添加时间戳
        sign_string += f"&timestamp={timestamp}"
        
        # 使用HMAC-SHA256计算签名
        sign_bytes = hmac.new(
            app_secret.encode('utf-8'),
            sign_string.encode('utf-8'),
            hashlib.sha256
        ).digest()
        
        # 转换为十六进制字符串
        calculated_signature = sign_bytes.hex()
        
        # 使用安全比较防止时序攻击
        is_valid = hmac.compare_digest(calculated_signature, signature)
        
        if not is_valid:
            SQLBotLogUtil.warning(f"DingTalk signature mismatch. Calculated: {calculated_signature[:16]}..., Received: {signature[:16]}...")
        
        return is_valid
    except ValueError as e:
        SQLBotLogUtil.error(f"DingTalk signature verification error (invalid timestamp): {str(e)}")
        return False
    except Exception as e:
        SQLBotLogUtil.error(f"DingTalk signature verification error: {str(e)}")
        return False