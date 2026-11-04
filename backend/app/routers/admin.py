from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.deps import AdminUser, SessionDep
from app.errors import ApiError
from app.models import (
    AuditLog,
    Check,
    NotificationDelivery,
    Payment,
    Plan,
    Site,
    Subscription,
    User,
    UserTariff,
    WorkerHeartbeat,
)
from app.pagination import PAGINATION_HEADERS, paginate
from app.services.billing import apply_limits, current_plan, plan_by_code
from app.services.events import publish
from app.validation import RequestModel

router = APIRouter(prefix="/admin", tags=["admin"])


class PlanChange(RequestModel):
    plan_code: str = Field(pattern="^(free|pro|business)$")
    days: int = Field(default=30, ge=1, le=3650)


class SignupPoint(BaseModel):
    day: date
    count: int


class AdminStats(BaseModel):
    users: int
    subscriptions: dict[str, int]
    revenue_kop: int
    revenue_month_start: datetime
    checks: int
    checks_24h: int
    sites: int
    signups: list[SignupPoint]
    worker_seen_seconds: int | None
    queue_lag_seconds: int
    due_checks: int
    pending_notifications: int


class AdminUserRow(BaseModel):
    id: int
    email: str
    plan: str | None
    status: str | None
    period_end: datetime | None
    sites: int
    is_admin: bool
    created_at: datetime


@router.get("/stats", response_model=AdminStats, summary="Показатели сервиса")
async def stats(user: AdminUser, session: SessionDep) -> AdminStats:
    now = datetime.now(UTC)
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    subscriptions = await session.execute(
        select(Plan.code, func.count(Subscription.id))
        .join(Subscription, Subscription.plan_id == Plan.id)
        .group_by(Plan.code)
    )
    since = (now - timedelta(days=89)).date()
    signup_rows: dict[date, int] = dict(
        (
            await session.execute(
                select(func.date(User.created_at), func.count(User.id))
                .where(User.created_at >= datetime.combine(since, datetime.min.time(), UTC))
                .group_by(func.date(User.created_at))
            )
        )
        .tuples()
        .all()
    )
    seen = await session.scalar(
        select(WorkerHeartbeat.seen_at).where(WorkerHeartbeat.name == "worker")
    )
    oldest_due = await session.scalar(
        select(func.min(Site.next_check_at)).where(
            Site.paused.is_(False), Site.next_check_at <= now, Site.monitor_type != "heartbeat"
        )
    )
    return AdminStats(
        users=await session.scalar(select(func.count(User.id))) or 0,
        subscriptions=dict(subscriptions.tuples().all()),
        revenue_kop=await session.scalar(
            select(func.coalesce(func.sum(Payment.amount_kop), 0)).where(
                Payment.status == "succeeded", Payment.paid_at >= month
            )
        )
        or 0,
        revenue_month_start=month,
        checks=await session.scalar(select(func.count(Check.id))) or 0,
        checks_24h=await session.scalar(
            select(func.count(Check.id)).where(Check.checked_at >= now - timedelta(hours=24))
        )
        or 0,
        sites=await session.scalar(select(func.count(Site.id))) or 0,
        signups=[
            SignupPoint(day=day, count=signup_rows.get(day, 0))
            for day in (since + timedelta(days=offset) for offset in range(90))
        ],
        worker_seen_seconds=int((now - seen).total_seconds()) if seen else None,
        queue_lag_seconds=int((now - oldest_due).total_seconds()) if oldest_due else 0,
        due_checks=await session.scalar(
            select(func.count(Site.id)).where(
                Site.paused.is_(False), Site.next_check_at <= now, Site.monitor_type != "heartbeat"
            )
        )
        or 0,
        pending_notifications=await session.scalar(
            select(func.count(NotificationDelivery.id)).where(
                NotificationDelivery.status == "pending"
            )
        )
        or 0,
    )


@router.get(
    "/users",
    response_model=list[AdminUserRow],
    summary="Пользователи",
    responses={200: {"headers": PAGINATION_HEADERS}},
)
async def users(
    request: Request,
    response: Response,
    user: AdminUser,
    session: SessionDep,
    search: str = Query("", max_length=100),
    cursor: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> list[AdminUserRow]:
    site_count = (
        select(Site.user_id, func.count(Site.id).label("sites")).group_by(Site.user_id).subquery()
    )
    statement = (
        select(
            User,
            Plan.code,
            Subscription.status,
            Subscription.current_period_end,
            site_count.c.sites,
        )
        .outerjoin(Subscription, Subscription.user_id == User.id)
        .outerjoin(Plan, Plan.id == Subscription.plan_id)
        .outerjoin(site_count, site_count.c.user_id == User.id)
        .where(User.id > cursor)
        .order_by(User.id)
        .limit(limit)
    )
    if search:
        statement = statement.where(User.email.icontains(search, autoescape=True))
    rows = (await session.execute(statement)).all()
    paginate(request, response, rows, limit, lambda row: row[0].id)
    return [
        AdminUserRow(
            id=account.id,
            email=account.email,
            plan=code,
            status=status,
            period_end=period_end,
            sites=sites or 0,
            is_admin=account.is_admin,
            created_at=account.created_at,
        )
        for account, code, status, period_end, sites in rows
    ]


@router.post("/users/{user_id}/plan", status_code=204)
async def change_plan(
    user_id: int, payload: PlanChange, user: AdminUser, session: SessionDep
) -> None:
    target = await session.get(User, user_id)
    if target is None:
        raise ApiError(404, "user_not_found", "Пользователь не найден")
    sub, previous = await current_plan(session, target)
    plan = await plan_by_code(session, payload.plan_code)
    sub.plan_id = plan.id
    sub.status = "active"
    sub.scheduled_plan_id = None
    sub.current_period_start = datetime.now(UTC)
    sub.current_period_end = datetime.now(UTC) + timedelta(days=payload.days)
    sub.cancel_at_period_end = False
    target.tariff = UserTariff.free if plan.code == "free" else UserTariff.pro
    await apply_limits(session, target.id, plan)
    session.add(
        AuditLog(
            user_id=target.id,
            action="subscription.admin",
            resource_type="billing",
            resource_id=user.id,
            changes=[{"field": "plan", "old": previous.code, "new": plan.code}],
        )
    )
    await publish(session, target.id, "subscription_changed", {"plan": plan.code})
    await session.commit()
