from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import (
    AuditLog,
    Check,
    DailyUptime,
    NotificationChannel,
    Payment,
    Plan,
    Site,
    Subscription,
    User,
    UserTariff,
)


async def plan_by_code(session: AsyncSession, code: str) -> Plan:
    plan = await session.scalar(select(Plan).where(Plan.code == code))
    if plan is None:
        raise ApiError(404, "plan_not_found", "Тариф не найден. Выполните python -m app.seed")
    return plan


async def grant_trial(session: AsyncSession, user: User) -> Subscription:
    await session.scalar(select(User.id).where(User.id == user.id).with_for_update())
    existing = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    if existing is not None:
        return existing
    now = datetime.now(UTC)
    plan = await plan_by_code(session, "free" if user.trial_used else "pro")
    trial = not user.trial_used
    user.trial_used = True
    user.tariff = UserTariff.pro if trial else UserTariff.free
    subscription = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        status="trialing" if trial else "active",
        current_period_start=now,
        current_period_end=now + timedelta(days=14 if trial else plan.period_days),
        trial_end=now + timedelta(days=14) if trial else None,
    )
    session.add(subscription)
    await session.flush()
    return subscription


async def apply_limits(session: AsyncSession, user_id: int, plan: Plan) -> None:
    sites = (
        await session.scalars(
            select(Site)
            .where(Site.user_id == user_id)
            .order_by(Site.created_at, Site.id)
            .with_for_update()
        )
    ).all()
    active = 0
    for site in sites:
        if site.paused and not (site.pause_reason or "").startswith("plan_"):
            continue
        active += 1
        reason = None
        if active > plan.max_sites:
            reason = "plan_limit_sites"
        elif site.monitor_type == "keyword" and not plan.features.get("keyword"):
            reason = "plan_feature_keyword"
        elif site.headers_encrypted and not plan.features.get("headers"):
            reason = "plan_feature_headers"
        elif (site.method == "POST" or site.body) and not plan.features.get("post"):
            reason = "plan_feature_post"
        elif site.maintenance and not plan.features.get("maintenance"):
            reason = "plan_feature_maintenance"
        elif site.monitor_type in ("heartbeat", "dns") and not plan.features.get(site.monitor_type):
            reason = f"plan_feature_{site.monitor_type}"
        elif site.assertions and not plan.features.get("assertions"):
            reason = "plan_feature_assertions"
        if len(site.regions) > 1 and (
            not plan.features.get("regions") or len(site.regions) > plan.max_regions
        ):
            trimmed = site.regions[: plan.max_regions] if plan.features.get("regions") else []
            site.regions = trimmed if len(trimmed) > 1 else []
        if site.slo_target is not None and not plan.features.get("slo"):
            site.slo_target = None
        if site.check_domain and not plan.features.get("domain_expiry"):
            site.check_domain = False
        site.paused = reason is not None
        site.pause_reason = reason
        site.interval_seconds = max(site.interval_seconds, plan.min_interval_sec)
    channels = (
        await session.scalars(
            select(NotificationChannel)
            .where(NotificationChannel.user_id == user_id, NotificationChannel.enabled.is_(True))
            .order_by(NotificationChannel.id)
            .with_for_update()
        )
    ).all()
    if plan.max_channels is not None:
        for channel in channels[plan.max_channels :]:
            channel.enabled = False


async def current_plan(session: AsyncSession, user: User) -> tuple[Subscription, Plan]:
    await session.scalar(select(User.id).where(User.id == user.id).with_for_update())
    sub = await session.scalar(
        select(Subscription).where(Subscription.user_id == user.id).with_for_update()
    )
    if sub is None:
        now = datetime.now(UTC)
        free = await plan_by_code(session, "free")
        sub = Subscription(
            user_id=user.id,
            plan_id=free.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        session.add(sub)
        await session.flush()
    plan = await session.get(Plan, sub.plan_id)
    if plan is None:
        raise ApiError(503, "plan_unavailable", "Тариф временно недоступен")
    now = datetime.now(UTC)
    if sub.current_period_end <= now and plan.code != "free":
        scheduled = (
            await session.get(Plan, sub.scheduled_plan_id) if sub.scheduled_plan_id else None
        )
        plan = scheduled or await plan_by_code(session, "free")
        sub.plan_id = plan.id
        sub.status = "active" if scheduled else "expired"
        sub.scheduled_plan_id = None
        sub.cancel_at_period_end = False
        if scheduled:
            sub.current_period_start = now
            sub.current_period_end = now + timedelta(days=plan.period_days)
        user.tariff = UserTariff.free if plan.code == "free" else UserTariff.pro
        await apply_limits(session, user.id, plan)
        session.add(
            AuditLog(
                user_id=user.id,
                action="subscription.downgraded" if scheduled else "subscription.expired",
            )
        )
        await session.flush()
    return sub, plan


def require_feature(plan: Plan, feature: str) -> None:
    if not plan.features.get(feature, False):
        raise ApiError(403, f"plan_feature_{feature}", "Эта возможность недоступна на вашем тарифе")


async def enforce_site_limit(session: AsyncSession, user: User) -> Plan:
    _, plan = await current_plan(session, user)
    count = await session.scalar(select(func.count(Site.id)).where(Site.user_id == user.id))
    if (count or 0) >= plan.max_sites:
        raise ApiError(
            402, "plan_limit_sites", f"Тариф {plan.name}: не более {plan.max_sites} сайтов"
        )
    return plan


async def enforce_channel_limit(session: AsyncSession, user: User) -> None:
    _, plan = await current_plan(session, user)
    count = await session.scalar(
        select(func.count(NotificationChannel.id)).where(NotificationChannel.user_id == user.id)
    )
    if plan.max_channels is not None and (count or 0) >= plan.max_channels:
        raise ApiError(
            402, "plan_limit_channels", f"Тариф {plan.name}: не более {plan.max_channels} каналов"
        )


async def activate_payment(
    session: AsyncSession,
    payment_id: str,
    provider: str,
    amount_kop: int,
    raw: dict[str, object],
    currency: str = "RUB",
) -> bool:
    payment = await session.scalar(
        select(Payment)
        .where(Payment.provider_payment_id == payment_id, Payment.provider == provider)
        .with_for_update()
    )
    if payment is None:
        raise ApiError(404, "payment_not_found", "Платёж не найден")
    if payment.amount_kop != amount_kop or payment.currency != currency:
        raise ApiError(400, "payment_amount_mismatch", "Сумма платежа не совпадает")
    if payment.paid_at is not None:
        return False
    payment_transition(payment, "succeeded", raw)
    user = await session.scalar(select(User).where(User.id == payment.user_id).with_for_update())
    sub = await session.scalar(
        select(Subscription).where(Subscription.id == payment.subscription_id).with_for_update()
    )
    plan = await session.get(Plan, payment.plan_id)
    if user is None or sub is None or plan is None:
        raise ApiError(409, "payment_account_missing", "Аккаунт платежа недоступен")
    now = datetime.now(UTC)
    start = (
        max(now, sub.current_period_end)
        if sub.plan_id == plan.id and sub.status == "active"
        else now
    )
    sub.plan_id = plan.id
    sub.status = "active"
    sub.current_period_start = now
    sub.current_period_end = start + timedelta(days=plan.period_days)
    sub.cancel_at_period_end = False
    user.tariff = UserTariff.free if plan.code == "free" else UserTariff.pro
    payment.status = "succeeded"
    payment.paid_at = now
    payment.raw_payload = safe_payment_payload(raw, payment)
    sub.scheduled_plan_id = None
    await apply_limits(session, user.id, plan)
    session.add(
        AuditLog(
            user_id=user.id,
            action="subscription.paid",
            resource_type="billing",
            resource_id=payment.id,
            changes=[{"field": "plan", "old": None, "new": plan.code}],
        )
    )
    from app.services.events import publish

    await publish(session, user.id, "subscription_changed", {"plan": plan.code, "status": "active"})
    return True


async def billing_maintenance(session: AsyncSession) -> None:
    users = (await session.scalars(select(User))).all()
    for user in users:
        _, plan = await current_plan(session, user)
        cutoff = datetime.now(UTC) - timedelta(days=plan.retention_days)
        sites = select(Site.id).where(Site.user_id == user.id)
        await session.execute(
            delete(Check).where(Check.site_id.in_(sites), Check.checked_at < cutoff)
        )
        await session.execute(
            delete(DailyUptime).where(
                DailyUptime.site_id.in_(sites), DailyUptime.day < cutoff.date()
            )
        )
    await session.commit()


def safe_payment_payload(raw: dict[str, object], payment: Payment) -> dict[str, object]:
    return {
        "id": payment.provider_payment_id,
        "status": payment.status,
        "amount_kop": payment.amount_kop,
        "currency": payment.currency,
    }


def payment_transition(payment: Payment, status: str, raw: dict[str, object]) -> None:
    allowed = {
        "pending": {"pending", "waiting_for_capture", "canceled", "succeeded"},
        "waiting_for_capture": {"waiting_for_capture", "canceled", "succeeded"},
        "canceled": {"canceled"},
        "succeeded": {"succeeded", "refunded"},
        "refunded": {"refunded"},
    }
    if status not in allowed.get(payment.status, set()):
        raise ApiError(409, "payment_state_conflict", "Недопустимый переход состояния платежа")
    payment.status = status
    payment.raw_payload = safe_payment_payload(raw, payment)


async def refund_subscription(session: AsyncSession, payment: Payment) -> None:
    later = await session.scalar(
        select(Payment.id)
        .where(
            Payment.user_id == payment.user_id,
            Payment.status == "succeeded",
            Payment.id > payment.id,
        )
        .limit(1)
    )
    if later is not None:
        return
    user = await session.scalar(select(User).where(User.id == payment.user_id).with_for_update())
    sub = await session.get(Subscription, payment.subscription_id)
    if user and sub:
        free = await plan_by_code(session, "free")
        sub.plan_id = free.id
        sub.status = "expired"
        sub.current_period_end = datetime.now(UTC)
        user.tariff = UserTariff.free
        await apply_limits(session, user.id, free)
        session.add(AuditLog(user_id=user.id, action="subscription.refund", resource_id=payment.id))
