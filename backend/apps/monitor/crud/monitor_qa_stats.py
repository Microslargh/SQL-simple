"""
问数（Data Q&A）监控统计：基于 chat_record + chat(chat_type=chat) + sys_user。

成功定义：finish 为真且 error 为空（NULL 或仅空白）。
失败定义：区间内其余记录。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from sqlalchemy import and_, case, func, or_, select
from sqlmodel import Session

from apps.chat.models.chat_model import Chat, ChatRecord
from apps.system.models.user import UserModel


def _parse_range(start_time: datetime | None, end_time: datetime | None) -> tuple[datetime, datetime]:
    end = end_time or datetime.now()
    # 数据库 DateTime(timezone=False) 通常按“本地时间/无时区”存储；因此这里丢弃 tzinfo
    if end.tzinfo is not None:
        end = end.replace(tzinfo=None)

    start = start_time
    if start is None:
        start = end - timedelta(days=30)
    elif start.tzinfo is not None:
        start = start.replace(tzinfo=None)

    if start >= end:
        raise ValueError("start_time must be before end_time")
    return start, end


def _success_condition():
    """业务成功：已完成且无 error 文本（NULL 或仅空白）。"""
    return and_(
        func.coalesce(ChatRecord.finish, False).is_(True),
        or_(ChatRecord.error.is_(None), func.trim(ChatRecord.error) == ""),
    )


def _base_where(start: datetime, end: datetime, oid: int | None):
    cond = [
        Chat.chat_type == "chat",
        ChatRecord.create_time.is_not(None),
        ChatRecord.create_time >= start,
        ChatRecord.create_time < end,
    ]
    if oid is not None:
        cond.append(Chat.oid == oid)
    return and_(*cond)


def get_summary(
    session: Session,
    start_time: datetime | None,
    end_time: datetime | None,
    oid: int | None,
) -> dict[str, Any]:
    start, end = _parse_range(start_time, end_time)
    succ = _success_condition()

    stmt = (
        select(
            func.count(ChatRecord.id).label("total"),
            func.sum(case((succ, 1), else_=0)).label("success_count"),
        )
        .select_from(ChatRecord)
        .join(Chat, ChatRecord.chat_id == Chat.id)
        .where(_base_where(start, end, oid))
    )
    row = session.exec(stmt).one()

    total = int(row.total or 0)
    success_count = int(row.success_count or 0)
    failed_count = total - success_count
    success_rate = (success_count / total) if total else None

    return {
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "total": total,
        "success_count": success_count,
        "failed_count": failed_count,
        "success_rate": round(success_rate, 6) if success_rate is not None else None,
        "definition": "success = finish is true and error is empty; failed = all other records in range",
    }


def get_timeseries(
    session: Session,
    start_time: datetime | None,
    end_time: datetime | None,
    oid: int | None,
    granularity: Literal["day", "hour"],
) -> dict[str, Any]:
    start, end = _parse_range(start_time, end_time)
    trunc = "day" if granularity == "day" else "hour"

    succ = _success_condition()
    bucket = func.date_trunc(trunc, ChatRecord.create_time)

    stmt = (
        select(
            bucket.label("bucket"),
            func.count(ChatRecord.id).label("total"),
            func.sum(case((succ, 1), else_=0)).label("success_count"),
        )
        .select_from(ChatRecord)
        .join(Chat, ChatRecord.chat_id == Chat.id)
        .where(_base_where(start, end, oid))
        .group_by(bucket)
        .order_by(bucket)
    )

    rows = session.exec(stmt).all()
    points = []
    for r in rows:
        tot = int(r.total or 0)
        sc = int(r.success_count or 0)
        bt = r.bucket
        points.append(
            {
                "time": bt.isoformat() if hasattr(bt, "isoformat") else str(bt),
                "total": tot,
                "success_count": sc,
                "failed_count": tot - sc,
            }
        )

    return {
        "granularity": granularity,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "points": points,
    }


def get_by_user(
    session: Session,
    start_time: datetime | None,
    end_time: datetime | None,
    oid: int | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    start, end = _parse_range(start_time, end_time)

    stmt = (
        select(
            ChatRecord.create_by.label("user_id"),
            UserModel.account,
            UserModel.name,
            UserModel.userorg,
            UserModel.orgname,
            func.count(ChatRecord.id).label("query_count"),
        )
        .select_from(ChatRecord)
        .join(Chat, ChatRecord.chat_id == Chat.id)
        # 仅统计可识别的真实用户：要求能关联到用户档案，并过滤系统管理员账号
        .join(UserModel, ChatRecord.create_by == UserModel.id)
        .where(_base_where(start, end, oid))
        .where(UserModel.account.is_not(None))
        .where(func.trim(UserModel.account) != "")
        .where(UserModel.account != "admin")
        .where(UserModel.id != 1)
        .group_by(
            ChatRecord.create_by,
            UserModel.account,
            UserModel.name,
            UserModel.userorg,
            UserModel.orgname,
        )
        .order_by(func.count(ChatRecord.id).desc())
        .limit(limit)
        .offset(offset)
    )

    rows = session.exec(stmt).all()
    items = []
    for r in rows:
        items.append(
            {
                "user_id": r.user_id,
                "account": r.account,
                "name": r.name,
                "userorg": r.userorg,
                "orgname": r.orgname,
                "query_count": int(r.query_count or 0),
            }
        )

    return {
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "limit": limit,
        "offset": offset,
        "items": items,
    }


def get_by_hour_of_day(
    session: Session,
    start_time: datetime | None,
    end_time: datetime | None,
    oid: int | None,
    *,
    target_date: date | None = None,
) -> dict[str, Any]:
    if target_date is not None and start_time is None and end_time is None:
        start = datetime.combine(target_date, time.min)
        end = start + timedelta(days=1)
    else:
        start, end = _parse_range(start_time, end_time)

    hour_expr = func.extract("hour", ChatRecord.create_time)
    succ = _success_condition()

    stmt = (
        select(
            hour_expr.label("hour"),
            func.count(ChatRecord.id).label("cnt"),
            func.sum(case((succ, 1), else_=0)).label("succ_cnt"),
        )
        .select_from(ChatRecord)
        .join(Chat, ChatRecord.chat_id == Chat.id)
        .where(_base_where(start, end, oid))
        .group_by(hour_expr)
        .order_by(hour_expr)
    )

    rows = session.exec(stmt).all()
    by_hour: dict[int, dict[str, int]] = {}
    for r in rows:
        h = int(r.hour)
        tot = int(r.cnt or 0)
        sc = int(r.succ_cnt or 0)
        by_hour[h] = {"count": tot, "success_count": sc, "failed_count": tot - sc}

    buckets = [
        {
            "hour": h,
            "count": by_hour.get(h, {}).get("count", 0),
            "success_count": by_hour.get(h, {}).get("success_count", 0),
            "failed_count": by_hour.get(h, {}).get("failed_count", 0),
        }
        for h in range(24)
    ]

    return {
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "buckets": buckets,
        "note": "hour is 0-23 from extract(hour, create_time); align with database server local time",
    }

