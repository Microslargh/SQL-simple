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
    # 当设置 EMBEDDING_API_BASE_URL 时使用 API 嵌入（如 Qwen3-embedding-8B），否则使用本地 HuggingFace 模型
    EMBEDDING_API_BASE_URL: str = Field(default='', description="Embedding API 基地址，如 http://host:port/v1，留空则使用本地模型")
    EMBEDDING_API_MODEL: str = Field(default='qwen3-embedding-8b', description="API 嵌入模型名称")
    EMBEDDING_API_KEY: str = Field(default='', description="Embedding API Key，若服务不需要可留空")
    # 库表 embedding 列维度，须与 migration 中 VECTOR 维度一致。本地 768 维模型会自动填充到该维度后写入
    EMBEDDING_VECTOR_DIMENSION: int = Field(default=4096, description="embedding 存储维度：768=本地 text2vec，4096=API 如 Qwen3-embedding-8B")
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
    # 上下文仲裁者（多轮对话意图冲突检测，解决「子集过滤」与「全量分布」冲突）
    CONTEXT_ARBITRATOR_API_URL: str = Field(
        default="http://10.100.110.113:8000/v1",
        description="语义仲裁者 API 地址（OpenAI 兼容格式），留空则禁用仲裁",
    )
    CONTEXT_ARBITRATOR_MODEL: str = Field(default="Qwen3.5-9B", description="仲裁者模型名称")
    CONTEXT_ARBITRATOR_TIMEOUT: float = Field(default=5.0, description="仲裁者调用超时秒数")

    # 问题增强 LLM（多轮追问补全：规则未命中时用 LLM 做语义重写，与仲裁者同方式 API 接入）
    QUESTION_ENHANCE_API_URL: str = Field(
        default="",
        description="问题增强 LLM API 地址（OpenAI 兼容 /chat/completions），留空则仅用规则增强",
    )
    QUESTION_ENHANCE_MODEL: str = Field(default="Qwen3.5-9B", description="问题增强模型名称")
    QUESTION_ENHANCE_TIMEOUT: float = Field(default=8.0, description="问题增强 LLM 调用超时秒数")
    QUESTION_ENHANCE_MAX_TURNS: int = Field(default=5, description="参与 LLM 补全的历史对话轮数（3-5 轮）")

    # 隐式参数提取（模板中硬编码实体→用户实体对齐，与问题增强同方式 API 接入；留空则跳过）
    IMPLICIT_PARAM_EXTRACT_API_URL: str = Field(
        default="",
        description="隐式参数提取 LLM API 地址（OpenAI 兼容），留空则不做隐式替换",
    )
    IMPLICIT_PARAM_EXTRACT_MODEL: str = Field(default="Qwen3.5-9B", description="隐式参数提取使用的模型名称")
    IMPLICIT_PARAM_EXTRACT_TIMEOUT: float = Field(default=8.0, description="隐式参数提取调用超时秒数")

    # 分析反思与修正（基于真实数据对初版报告做事实核查与清洗）
    ANALYSIS_REFLECTION_ENABLED: bool = Field(default=True, description="是否启用分析反思修正节点（建议开启）")
    ANALYSIS_REFLECTION_MODEL: str = Field(default="Qwen3.5-9B", description="分析反思修正使用的模型名称")
    ANALYSIS_REFLECTION_TIMEOUT: float = Field(default=10.0, description="分析反思修正调用超时秒数")

    GUESS_SCHEMA_TABLE_COUNT: int = Field(default=5, description="猜你想问场景 schema pruning 返回表数量（建议 3-5）")
    GUESS_SCHEMA_INCLUDE_VALUE_HINTS: bool = Field(default=True, description="猜你想问 schema 是否注入深表字段值域示例")
    GUESS_SCHEMA_VALUE_HINT_TOPK: int = Field(default=5, description="深表字段值域示例最多注入值数量")
    GUESS_SQL_VALIDATE_ENABLED: bool = Field(default=False, description="是否启用猜你想问 SQL 预验证（会增加延迟）")
    TABLE_SELECTOR_LLM_ENABLED: bool = Field(default=True, description="是否启用 SQL 前 LLM 选表节点")
    TABLE_SELECTOR_LLM_TOPK: int = Field(default=1, description="LLM 选表返回数量（建议 1-3）")
    TABLE_SELECTOR_LLM_STRICT: bool = Field(default=True, description="LLM 选表失败时是否禁止回退到默认表检索")
    SQL_AUTOFIX_ENABLED: bool = Field(default=True, description="是否启用 SQL 语法校验与自动修复")
    SQL_AUTOFIX_MAX_RETRIES: int = Field(default=1, description="SQL 自动修复最大重试次数")

    # 历史行业规则开关（如 cqs/jq_zbval、并表/存续口径、特定字段槽位映射等）
    # 面向全新数据库时建议关闭，避免旧业务规则干扰 SQL 生成。
    LEGACY_DOMAIN_RULES_ENABLED: bool = Field(default=False, description="是否启用历史行业硬编码规则")

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
