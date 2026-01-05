
from pydantic import BaseModel
from enum import Enum

class LocalLoginSchema(BaseModel):
    account: str
    password: str
    
class CacheNamespace(Enum):
    AUTH_INFO = "sqlbot:auth"
    EMBEDDED_INFO = "sqlbot:embedded"
    def __str__(self):
        return self.value
class CacheName(Enum):
    USER_INFO = "user:info"
    ASSISTANT_INFO = "assistant:info"
    ASSISTANT_DS = "assistant:ds"
    OAUTH2_ACCESS_TOKEN = "oauth2:access_token"
    OAUTH2_STATE = "oauth2:state"
    def __str__(self):
        return self.value