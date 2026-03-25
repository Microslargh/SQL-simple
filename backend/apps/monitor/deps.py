from fastapi import HTTPException, Request

from common.core.config import settings


async def require_monitor_api_key(request: Request) -> bool:
    """
    监控平台开放接口鉴权：
    仅依赖 `X-SQLBOT-MONITOR-KEY`，不走用户 JWT。
    """
    if not settings.MONITOR_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="MONITOR_API_KEY is not configured; set it in environment / .env",
        )

    key = request.headers.get("X-SQLBOT-MONITOR-KEY", "").strip()
    if not key or key != settings.MONITOR_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-SQLBOT-MONITOR-KEY")
    return True

