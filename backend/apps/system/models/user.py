
from typing import Optional
from sqlmodel import BigInteger, SQLModel, Field

from common.core.models import SnowflakeBase
from common.core.security import default_md5_pwd
from common.utils.time import get_timestamp



class BaseUserPO(SQLModel):
    account: str = Field(max_length=255, unique=True)
    oid: int = Field(nullable=False, sa_type=BigInteger(), default=0)
    name: str = Field(max_length=255, unique=True)
    password: str = Field(default_factory=default_md5_pwd, max_length=255)
    email: str = Field(max_length=255)
    status: int = Field(default=0, nullable=False)
    create_time: int = Field(default_factory=get_timestamp, sa_type=BigInteger(), nullable=False)
    language: str = Field(max_length=255, default="zh-CN")
    register_type: int = Field(default=0, nullable=False, description="注册类型：0-系统注册，1-4a平台注册，2-钉钉用户注册")
    orgname: str = Field(max_length=255,description="部门名称")
    userorg: str = Field(max_length=255, description="部门编码")


class UserModel(SnowflakeBase, BaseUserPO, table=True):
    __tablename__ = "sys_user"
    
