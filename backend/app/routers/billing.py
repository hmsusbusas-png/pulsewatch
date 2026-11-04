import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.config import settings
from app.deps import CurrentUser, SessionDep
from app.errors import ApiError, ErrorResponse
from app.models import AuditLog, NotificationChannel, Payment, Plan, Site
from app.services.billing import activate_payment, current_plan, plan_by_code
from app.services.events import publish
from app.services.payments import payment_provider
from app.validation import RequestModel

router = APIRouter(tags=["billing"])


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    name: str
    price_kop: int
    period_days: int
    max_sites: int
    min_interval_sec: int
    retention_days: int
    max_channels: int | None
    max_regions: int = 1
    features: dict[str, bool]


class CheckoutRequest(RequestModel):
    plan_code: str = Field(pattern="^(pro|business)$")


class CheckoutResponse(BaseModel):
    payment_id: str
    url: str


class DowngradeRequest(RequestModel):
    plan_code: str = Field(default="free", pattern="^(free|pro)$")


class SubscriptionResponse(BaseModel):
    plan: PlanResponse
    scheduled_plan: str | None = None
    status: str
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    trial_end: datetime | None
    usage: dict[str, int]


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    provider: str
    provider_payment_id: str
    amount_kop: int
    status: str
    created_at: datetime
    paid_at: datetime | None


@router.get("/plans", response_model=list[PlanResponse])
async def plans(session: SessionDep) -> list[Plan]:
    return list(await session.scalars(select(Plan).order_by(Plan.price_kop)))


@router.get("/billing/subscription", response_model=SubscriptionResponse)
async def subscription(user: CurrentUser, session: SessionDep) -> SubscriptionResponse:
    sub, plan = await current_plan(session, user)
    sites = await session.scalar(select(func.count(Site.id)).where(Site.user_id == user.id))
    channels = await session.scalar(
        select(func.count(NotificationChannel.id)).where(NotificationChannel.user_id == user.id)
    )
    scheduled = await session.get(Plan, sub.scheduled_plan_id) if sub.scheduled_plan_id else None
    result = SubscriptionResponse(
        plan=PlanResponse.model_validate(plan),
        scheduled_plan=scheduled.code if scheduled else None,
        status=sub.status,
        current_period_start=sub.current_period_start,
        current_period_end=sub.current_period_end,
        cancel_at_period_end=sub.cancel_at_period_end,
        trial_end=sub.trial_end,
        usage={"sites": sites or 0, "channels": channels or 0},
    )
    await session.commit()
    return result


@router.post("/billing/checkout", response_model=CheckoutResponse)
async def checkout(
    payload: CheckoutRequest, user: CurrentUser, session: SessionDep
) -> CheckoutResponse:
    sub, _ = await current_plan(session, user)
    plan = await plan_by_code(session, payload.plan_code)
    provider = payment_provider(settings.payment_provider)
    created = await provider.create_payment(plan.price_kop, uuid.uuid4().hex)
    session.add(
        Payment(
            user_id=user.id,
            subscription_id=sub.id,
            plan_id=plan.id,
            provider=settings.payment_provider,
            provider_payment_id=created.id,
            amount_kop=plan.price_kop,
        )
    )
    await session.commit()
    return CheckoutResponse(payment_id=created.id, url=created.url)


@router.post("/billing/cancel", status_code=204)
async def cancel(user: CurrentUser, session: SessionDep) -> None:
    sub, _ = await current_plan(session, user)
    sub.cancel_at_period_end = True
    session.add(AuditLog(user_id=user.id, action="subscription.cancel", resource_type="billing"))
    await publish(session, user.id, "subscription_changed", {"cancel_at_period_end": True})
    await session.commit()


@router.post(
    "/billing/downgrade",
    response_model=SubscriptionResponse,
    summary="Понизить тариф в конце периода",
    description="Текущий тариф действует до current_period_end, затем подписка переходит "
    "на выбранный тариф без оплаты. Отменяется через /billing/resume.",
    responses={409: {"model": ErrorResponse}},
)
async def downgrade(
    payload: DowngradeRequest, user: CurrentUser, session: SessionDep
) -> SubscriptionResponse:
    sub, plan = await current_plan(session, user)
    target = await plan_by_code(session, payload.plan_code)
    if target.price_kop >= plan.price_kop:
        raise ApiError(409, "not_a_downgrade", "Выбранный тариф не дешевле текущего")
    sub.scheduled_plan_id = target.id
    sub.cancel_at_period_end = True
    session.add(
        AuditLog(
            user_id=user.id,
            action="subscription.downgrade_scheduled",
            resource_type="billing",
            changes=[{"field": "plan", "old": plan.code, "new": target.code}],
        )
    )
    await publish(session, user.id, "subscription_changed", {"scheduled_plan": target.code})
    await session.commit()
    return await subscription(user, session)


@router.post("/billing/resume", status_code=204)
async def resume(user: CurrentUser, session: SessionDep) -> None:
    sub, _ = await current_plan(session, user)
    if sub.status == "expired":
        raise ApiError(409, "subscription_expired", "Оплатите новый период подписки")
    sub.cancel_at_period_end = False
    sub.scheduled_plan_id = None
    await publish(session, user.id, "subscription_changed", {"cancel_at_period_end": False})
    await session.commit()


@router.get("/billing/payments", response_model=list[PaymentResponse])
async def payments(user: CurrentUser, session: SessionDep) -> list[Payment]:
    return list(
        await session.scalars(
            select(Payment).where(Payment.user_id == user.id).order_by(Payment.id.desc()).limit(100)
        )
    )


@router.post("/billing/webhook/{provider}")
async def webhook(
    provider: str, payload: dict[str, object], request: Request, session: SessionDep
) -> dict[str, bool]:
    adapter = payment_provider(provider)
    if not isinstance(payload, dict) or not await adapter.verify_signature(
        payload, request.headers.get("x-signature", "")
    ):
        raise ApiError(403, "invalid_signature", "Подпись платежа недействительна")
    paid = await adapter.parse_webhook(payload)
    changed = False
    if paid.status == "succeeded":
        changed = await activate_payment(
            session, paid.id, provider, paid.amount_kop, paid.raw, paid.currency
        )
    else:
        payment = await session.scalar(
            select(Payment)
            .where(Payment.provider_payment_id == paid.id, Payment.provider == provider)
            .with_for_update()
        )
        if payment and (payment.paid_at is None or paid.status == "refunded"):
            from app.services.billing import payment_transition

            if paid.amount_kop != payment.amount_kop or paid.currency != payment.currency:
                raise ApiError(400, "payment_amount_mismatch", "Сумма или валюта не совпадает")
            payment_transition(payment, paid.status, paid.raw)
            if paid.status == "refunded":
                from app.services.billing import refund_subscription

                await refund_subscription(session, payment)
    await session.commit()
    return {"ok": True, "activated": changed}
