from fastapi import Request
from common.core.config import settings

def get_sqlbot_token(request: Request):
    token_key = settings.TOKEN_KEY
    token = request.headers.get(token_key)
    if token.startswith("Bearer"):
        token = token.replace("Bearer", "")
    return token
