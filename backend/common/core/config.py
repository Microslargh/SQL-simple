import os
import secrets
from email.policy import default
from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    BeforeValidator,
    PostgresDsn,
    computed_field,
    Field
)
from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",")]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file=r"E:/cursor/data/sqlbot/file/config/.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    PROJECT_NAME: str = "SQLBot"
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = secrets.token_urlsafe(32)
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS] + [
            self.FRONTEND_HOST
        ]

    POSTGRES_SERVER: str = 'localhost'
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = 'root'
    POSTGRES_PASSWORD: str = Field(default="Password123@pg", description="PostgreSQL密码")
    POSTGRES_DB: str = "sqlbot"
    SQLBOT_DB_URL: str = ''

    TOKEN_KEY: str = "X-SQLBOT-TOKEN"
    DEFAULT_PWD: str = Field(default="123456", description="默认密码")
    ASSISTANT_TOKEN_KEY: str = "X-SQLBOT-ASSISTANT-TOKEN"

    CACHE_TYPE: Literal["redis", "memory", "None"] = "memory"
    CACHE_REDIS_URL: str | None = None  # Redis URL, e.g., "redis://[[username]:[password]]@localhost:6379/0"

    LOG_LEVEL: str = "INFO"  # DEBUG, INFO, WARNING, ERROR
    LOG_DIR: str = "logs"
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s:%(lineno)d - %(message)s"
    SQL_DEBUG: bool = False

    UPLOAD_DIR: str = "/opt/sqlbot/data/file"
    SQLBOT_KEY_EXPIRED: int = 100  # License key expiration timestamp, 0 means no expiration

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> PostgresDsn | str:
        if self.SQLBOT_DB_URL:
            return self.SQLBOT_DB_URL
        return MultiHostUrl.build(
            scheme="postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_SERVER,
            port=self.POSTGRES_PORT,
            path=self.POSTGRES_DB,
        )

    MCP_IMAGE_PATH: str = '/opt/sqlbot/images'
    EXCEL_PATH: str = '/opt/sqlbot/data/excel'
    MCP_IMAGE_HOST: str = 'http://localhost:3000'
    SERVER_IMAGE_HOST: str = 'http://YOUR_SERVE_IP:MCP_PORT/images/'

    LOCAL_MODEL_PATH: str = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../my_model'))
    # LOCAL_MODEL_PATH: str = '/opt/sqlbot/models'
    DEFAULT_EMBEDDING_MODEL: str = 'shibing624/text2vec-base-chinese'
    EMBEDDING_ENABLED: bool = True
    EMBEDDING_DEFAULT_SIMILARITY: float = 0.4
    EMBEDDING_TERMINOLOGY_SIMILARITY: float = EMBEDDING_DEFAULT_SIMILARITY
    EMBEDDING_DATA_TRAINING_SIMILARITY: float = EMBEDDING_DEFAULT_SIMILARITY
    EMBEDDING_DEFAULT_TOP_COUNT: int = 5
    EMBEDDING_TERMINOLOGY_TOP_COUNT: int = EMBEDDING_DEFAULT_TOP_COUNT
    EMBEDDING_DATA_TRAINING_TOP_COUNT: int = EMBEDDING_DEFAULT_TOP_COUNT

    PARSE_REASONING_BLOCK_ENABLED: bool = True
    DEFAULT_REASONING_CONTENT_START: str = '<think>'
    DEFAULT_REASONING_CONTENT_END: str = '</think>'

    PG_POOL_SIZE: int = 20
    PG_MAX_OVERFLOW: int = 30
    PG_POOL_RECYCLE: int = 3600
    PG_POOL_PRE_PING: bool = True
    USER_DEFAULT_PWD: str = Field(default="!exi87223@QQX", description="用户默认密码")

    # 钉钉登录配置
    DINGTALK_ENABLED: bool = True  # 是否启用钉钉登录（默认禁用）
    DINGTALK_APP_SECRET: str = "cgn1234567890"  # 钉钉应用密钥，用于签名校验
    DINGTALK_SIGNATURE_TIMEOUT: int = 300  # 签名有效期（秒），默认5分钟，防止重放攻击

    # 直接登录配置
    AUTO_LOGIN_ENABLED: bool = False  # 是否启用直接登录模式（默认关闭）

    TABLE_EMBEDDING_ENABLED: bool = Field(default=False, description="是否启用表结构的embedding检索筛选")
    TABLE_EMBEDDING_COUNT: int = Field(default=10, description="表embedding检索返回的最大表数量")

    # OAuth2 SSO 配置
    OAUTH2_ENABLED: bool = True  # 是否启用OAuth2单点登录（默认禁用，需要手动启用）
    OAUTH2_AUTHORIZATION_URL: str = "https://uap-t.cgnpc.com.cn/authcenter/getOauth2Authorize"  # OAuth2授权服务器地址，例如: https://oauth.example.com/oauth/authorize
    OAUTH2_TOKEN_URL: str = "https://aepgw-t.gnpjvc.cgnpc.com.cn/authcenter/getOauth2Token"  # OAuth2 Token获取地址，例如: https://oauth.example.com/oauth/token
    OAUTH2_USERINFO_URL: str = "https://aepgw-t.gnpjvc.cgnpc.com.cn/authcenter/getOauth2UserInfo"  # OAuth2用户信息获取地址，例如: https://oauth.example.com/api/userinfo
    OAUTH2_CLIENT_ID: str = "SQLBot"  # OAuth2客户端ID
    OAUTH2_CLIENT_SECRET: str = "Xy7$kL9@mN2!pQ"  # OAuth2客户端密钥
    OAUTH2_REDIRECT_URI: str = "https://10.125.33.145:9018/api/v1/callback"  # OAuth2回调地址，例如: http://localhost:8000/api/v1/callback
    OAUTH2_SCOPE: str = "openid profile email"  # OAuth2请求的权限范围
    OAUTH2_RESPONSE_TYPE: str = "code"  # OAuth2响应类型，通常为code
    OAUTH2_LOGOUT_URL: str = ""  # OAuth2登出接口地址，例如: https://oauth.example.com/oauth/logout
    # OAuth2用户信息字段映射（从OAuth2平台返回的用户信息字段映射到系统字段）
    OAUTH2_USER_FIELD_MAPPING: str = '{"account": "usercode", "name": "username",orgname:"orgname",userorg:"userorg"}'
    UAP_APP_KEY: str = "836ec1c7b508478ebf0cf5f5c9188f49"
    UAP_APP_ID: str = "HRAISD_CGN"
    UAP_APP_SECRET: str = "0c84b9cf0a184d2d8a7fb9b8c7424ba1"
    UAP_APP_ID_PARAM: str = "1988067706615631874"
    AS_S_K: str = ""
    AES_IV: str = ""

settings = Settings()  # type: ignore
