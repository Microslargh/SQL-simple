"""
问数监控开放 API（供数字员工监控平台拉取统计数据）。

鉴权：请求头 `X-SQLBOT-MONITOR-KEY` 与配置项 `MONITOR_API_KEY` 一致；路由已加入白名单，无需用户 JWT。
"""

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.monitor.crud import monitor_qa_stats
from apps.monitor.deps import require_monitor_api_key
from common.core.deps import SessionDep

router = APIRouter(tags=["monitor/qa"], prefix="/monitor/qa")


@router.get("/summary")
async def qa_summary(
    session: SessionDep,
    _auth: bool = Depends(require_monitor_api_key),
    start_time: datetime | None = Query(
        None, description="统计区间开始（含），默认 end_time 前 30 天"
    ),
    end_time: datetime | None = Query(
        None, description="统计区间结束（不含），默认当前时间"
    ),
    oid: int | None = Query(None, description="按工作空间 ID 过滤，不传则全量"),
):
    """问数成功率、问数总量、失败量。"""
    try:
        data = monitor_qa_stats.get_summary(session, start_time, end_time, oid)
        return {"code": 0, "data": data, "msg": None}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/timeseries")
async def qa_timeseries(
    session: SessionDep,
    _auth: bool = Depends(require_monitor_api_key),
    start_time: datetime | None = Query(None),
    end_time: datetime | None = Query(None),
    oid: int | None = Query(None),
    granularity: Literal["day", "hour"] = Query("day", description="时间桶：day 或 hour"),
):
    """问数量随时间变化（按日或按小时聚合）。"""
    try:
        data = monitor_qa_stats.get_timeseries(
            session, start_time, end_time, oid, granularity
        )
        return {"code": 0, "data": data, "msg": None}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/by-user")
async def qa_by_user(
    session: SessionDep,
    _auth: bool = Depends(require_monitor_api_key),
    start_time: datetime | None = Query(None),
    end_time: datetime | None = Query(None),
    oid: int | None = Query(None),
    limit: int = Query(500, ge=1, le=5000, description="返回用户数上限"),
    offset: int = Query(0, ge=0),
):
    """用户分布：用户 ID、用户账号、姓名、部门（部门编码/名称）与问数次数。"""
    try:
        data = monitor_qa_stats.get_by_user(
            session, start_time, end_time, oid, limit, offset
        )
        return {"code": 0, "data": data, "msg": None}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/by-hour-of-day")
async def qa_by_hour_of_day(
    session: SessionDep,
    _auth: bool = Depends(require_monitor_api_key),
    target_date: date | None = Query(
        None, description="用于分析某一天的访问时段；与 start_time/end_time 不能同时提供"
    ),
    start_time: datetime | None = Query(None),
    end_time: datetime | None = Query(None),
    oid: int | None = Query(None),
):
    """访问时间分布：一天内 0-23 点（在所选时间范围内汇总为 24 个桶）。"""
    try:
        data = monitor_qa_stats.get_by_hour_of_day(
            session, start_time, end_time, oid, target_date=target_date
        )
        return {"code": 0, "data": data, "msg": None}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/meta")
async def qa_meta(_auth: bool = Depends(require_monitor_api_key)):
    """指标口径说明，便于对接方文档化。"""
    data = {
        "scope": "仅统计 chat 表中 chat_type='chat' 的问数会话对应记录（chat_record）",
        "success": "finish 为 true 且 error 为空（NULL 或空白）",
        "failed": "区间内其余记录（含未完成、带 error 文本等）",
        "user_org": "部门来自 sys_user.orgname / userorg，与 OAuth2 登录同步后的用户档案；非提问时刻快照",
        "time_fields": "默认以 chat_record.create_time 为提问时间；若入参带时区则以其“本地时间”解释（去 tzinfo）",
    }
    return {"code": 0, "data": data, "msg": None}

