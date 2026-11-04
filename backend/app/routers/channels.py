from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field, TypeAdapter, ValidationError
from sqlalchemy import func, select

from app.config import settings
from app.crypto import decrypt_config, encrypt_config
from app.deps import CurrentUser, SessionDep
from app.errors import ApiError, ErrorResponse, Responses
from app.models import NotificationChannel, NotificationDelivery, User
from app.pagination import PAGINATION_HEADERS, paginate
from app.security import link_claims
from app.services import audit
from app.services.billing import current_plan, enforce_channel_limit
from app.services.channel_formats import WEBHOOK_TYPES, validate_config
from app.services.notifications import token_channel
from app.ssrf import ensure_target_allowed
from app.validation import RequestModel

router = APIRouter(prefix="/channels", tags=["channels"])
ChannelType = Literal["email", "webhook", "slack", "discord", "teams", "ntfy"]
NOT_FOUND: Responses = {404: {"model": ErrorResponse, "description": "Канал не найден"}}


class ChannelCreate(RequestModel):
    type: ChannelType
    name: str = Field(min_length=1, max_length=100)
    config: dict[str, str] = Field(
        description="email: {email}; webhook: {url, secret}; slack/discord/teams: {url}; "
        "ntfy: {url, token?}"
    )


class ChannelUpdate(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    config: dict[str, str] | None = Field(
        default=None,
        description="Новый адрес не используется, пока не пройдёт подтверждение; до этого "
        "уведомления идут на прежний.",
    )


class ChannelResponse(BaseModel):
    id: int
    type: str
    name: str
    enabled: bool
    verified_at: datetime | None
    pending_verification: bool
    config: dict[str, str]
    target: str | None = Field(description="Замаскированный адрес назначения")


class TokenRequest(RequestModel):
    token: str = Field(min_length=20, max_length=2048)


class DeliveryResponse(BaseModel):
    id: int
    status: str
    event: str | None
    attempts: int
    response_code: int | None
    error: str | None
    created_at: datetime
    delivered_at: datetime | None


def masked_target(kind: str, config: dict[str, str]) -> str | None:
    if kind == "email" and "email" in config:
        local, _, domain = config["email"].partition("@")
        return f"{local[:2]}•••@{domain}"
    if "url" in config:
        from urllib.parse import urlsplit

        parts = urlsplit(config["url"])
        return f"{parts.scheme}://{parts.hostname}/•••"
    if kind == "telegram":
        return "Telegram-чат"
    return None


def channel_response(channel: NotificationChannel) -> ChannelResponse:
    config = decrypt_config(channel.config)
    return ChannelResponse(
        id=channel.id,
        type=channel.type,
        name=channel.name,
        enabled=channel.enabled,
        verified_at=channel.verified_at,
        pending_verification=channel.pending_config is not None or channel.verified_at is None,
        config=dict.fromkeys(config, "********"),
        target=masked_target(channel.type, config),
    )


async def owned_channel(session: SessionDep, user_id: int, channel_id: int) -> NotificationChannel:
    channel = await session.scalar(
        select(NotificationChannel).where(
            NotificationChannel.id == channel_id, NotificationChannel.user_id == user_id
        )
    )
    if channel is None:
        raise ApiError(404, "channel_not_found", "Канал не найден")
    return channel


async def checked_config(kind: str, config: dict[str, str]) -> dict[str, str]:
    if kind in WEBHOOK_TYPES:
        clean = validate_config(kind, config)
        await ensure_target_allowed(clean["url"], settings.allow_private_targets)
        return clean
    try:
        address = str(TypeAdapter(EmailStr).validate_python(config.get("email", "")))
    except ValidationError as exc:
        raise ApiError(422, "invalid_channel_config", "Укажите корректный email") from exc
    return {"email": address.lower()}


def verification(channel: NotificationChannel) -> NotificationDelivery:
    return NotificationDelivery(
        channel_id=channel.id,
        payload={"event": "verification", "text": "Канал подключён к Pulsewatch"},
    )


@router.get(
    "",
    response_model=list[ChannelResponse],
    responses={200: {"headers": PAGINATION_HEADERS}},
)
async def channels(
    request: Request,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    cursor: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
) -> list[ChannelResponse]:
    rows = list(
        await session.scalars(
            select(NotificationChannel)
            .where(NotificationChannel.id > cursor, NotificationChannel.user_id == user.id)
            .order_by(NotificationChannel.id)
            .limit(limit)
        )
    )
    paginate(request, response, rows, limit, lambda row: row.id)
    return [channel_response(row) for row in rows]


@router.post(
    "",
    response_model=ChannelResponse,
    status_code=201,
    summary="Добавить канал",
    description="Адреса вебхуков проверяются той же SSRF-политикой, что и мониторы. Email "
    "своего аккаунта подтверждён сразу, чужой подтверждается по ссылке из письма. Поддерживает "
    "Idempotency-Key.",
    responses={402: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def create_channel(
    payload: ChannelCreate, user: CurrentUser, session: SessionDep
) -> ChannelResponse:
    await enforce_channel_limit(session, user)
    config = await checked_config(payload.type, payload.config)
    channel = NotificationChannel(
        user_id=user.id, type=payload.type, name=payload.name, config=encrypt_config(config)
    )
    if payload.type == "email" and config["email"] == user.email.lower():
        channel.verified_at = datetime.now(UTC)
    session.add(channel)
    await session.flush()
    audit.record(
        session,
        user.id,
        "channel.create",
        "channel",
        channel.id,
        [{"field": "type", "old": None, "new": payload.type}],
    )
    await session.commit()
    return channel_response(channel)


@router.post(
    "/unsubscribe",
    summary="Отписать email-канал по ссылке",
    description="Вызывается страницей фронтенда /channels/unsubscribe.",
    responses={400: {"model": ErrorResponse}},
)
async def unsubscribe(payload: TokenRequest, session: SessionDep) -> dict[str, bool]:
    channel_id = token_channel(payload.token)
    channel = await session.get(NotificationChannel, channel_id) if channel_id else None
    if channel is None:
        raise ApiError(400, "invalid_token", "Ссылка недействительна")
    channel.enabled = False
    await session.commit()
    return {"unsubscribed": True}


@router.post(
    "/verify",
    summary="Подтвердить email-канал по ссылке",
    description="Вызывается страницей фронтенда /channels/verify.",
    responses={400: {"model": ErrorResponse}},
)
async def confirm_verification(payload: TokenRequest, session: SessionDep) -> dict[str, bool]:
    claims = link_claims(payload.token, "channel_verify")
    channel = (
        await session.get(NotificationChannel, int(str(claims["channel"]))) if claims else None
    )
    if channel is None:
        raise ApiError(400, "invalid_token", "Ссылка недействительна или истекла")
    if channel.pending_config:
        channel.config, channel.pending_config = channel.pending_config, None
    channel.verified_at = datetime.now(UTC)
    audit.record(session, channel.user_id, "channel.verified", "channel", channel.id)
    await session.commit()
    return {"verified": True}


@router.patch(
    "/{channel_id}",
    response_model=ChannelResponse,
    summary="Изменить канал",
    responses={402: {"model": ErrorResponse}, **NOT_FOUND},
)
async def update_channel(
    channel_id: int, payload: ChannelUpdate, user: CurrentUser, session: SessionDep
) -> ChannelResponse:
    channel = await owned_channel(session, user.id, channel_id)
    _, plan = await current_plan(session, user)
    changes: list[dict[str, object]] = []
    if payload.enabled and not channel.enabled and plan.max_channels is not None:
        active = await session.scalar(
            select(func.count(NotificationChannel.id)).where(
                NotificationChannel.user_id == user.id, NotificationChannel.enabled.is_(True)
            )
        )
        if (active or 0) >= plan.max_channels:
            raise ApiError(402, "plan_limit_channels", "Превышен лимит активных каналов")
    if payload.name is not None and payload.name != channel.name:
        changes.append({"field": "name", "old": channel.name, "new": payload.name})
        channel.name = payload.name
    if payload.enabled is not None and payload.enabled != channel.enabled:
        changes.append({"field": "enabled", "old": channel.enabled, "new": payload.enabled})
        channel.enabled = payload.enabled
    if payload.config is not None:
        if channel.type == "telegram":
            raise ApiError(422, "invalid_channel_config", "Telegram подключается через бота")
        current = decrypt_config(channel.config)
        supplied = {
            key: current.get(key, "") if value == "********" else value
            for key, value in payload.config.items()
        }
        config = await checked_config(channel.type, supplied)
        if config != current:
            changes.append(
                {
                    "field": "target",
                    "old": masked_target(channel.type, current),
                    "new": masked_target(channel.type, config),
                }
            )
            owner = await session.get(User, user.id)
            if channel.type == "email" and owner and config["email"] == owner.email.lower():
                channel.config, channel.pending_config = encrypt_config(config), None
                channel.verified_at = datetime.now(UTC)
            else:
                channel.pending_config = encrypt_config(config)
                await session.flush()
                session.add(verification(channel))
    if changes:
        audit.record(session, user.id, "channel.update", "channel", channel.id, changes)
    await session.commit()
    return channel_response(channel)


@router.post(
    "/{channel_id}/verify",
    status_code=202,
    summary="Отправить подтверждение",
    description="Вебхук-каналы подтверждаются успешным ответом 2xx, email подтверждается "
    "переходом по ссылке из письма. До подтверждения канал не получает уведомления о сбоях.",
    responses={409: {"model": ErrorResponse}, **NOT_FOUND},
)
async def verify_channel(channel_id: int, user: CurrentUser, session: SessionDep) -> dict[str, int]:
    channel = await owned_channel(session, user.id, channel_id)
    if not channel.enabled:
        raise ApiError(409, "channel_disabled", "Канал отключён")
    if channel.type == "telegram":
        raise ApiError(409, "channel_verified", "Telegram подтверждается при привязке бота")
    delivery = verification(channel)
    session.add(delivery)
    await session.commit()
    return {"delivery_id": delivery.id}


@router.post("/{channel_id}/test", status_code=202, responses=NOT_FOUND)
async def test_channel(channel_id: int, user: CurrentUser, session: SessionDep) -> dict[str, int]:
    channel = await owned_channel(session, user.id, channel_id)
    if not channel.enabled:
        raise ApiError(409, "channel_disabled", "Канал отключён")
    delivery = NotificationDelivery(
        channel_id=channel.id, payload={"event": "test", "text": "Проверка уведомлений Pulsewatch"}
    )
    session.add(delivery)
    await session.commit()
    return {"delivery_id": delivery.id}


@router.get(
    "/{channel_id}/deliveries",
    response_model=list[DeliveryResponse],
    responses=NOT_FOUND,
)
async def deliveries(
    channel_id: int, user: CurrentUser, session: SessionDep
) -> list[DeliveryResponse]:
    await owned_channel(session, user.id, channel_id)
    rows = await session.scalars(
        select(NotificationDelivery)
        .where(NotificationDelivery.channel_id == channel_id)
        .order_by(NotificationDelivery.id.desc())
        .limit(100)
    )
    return [
        DeliveryResponse(
            id=row.id,
            status=row.status,
            event=str(row.payload.get("event")) if row.payload.get("event") else None,
            attempts=row.attempts,
            response_code=row.response_code,
            error=row.error,
            created_at=row.created_at,
            delivered_at=row.delivered_at,
        )
        for row in rows
    ]


@router.delete("/{channel_id}", status_code=204, responses=NOT_FOUND)
async def delete_channel(channel_id: int, user: CurrentUser, session: SessionDep) -> None:
    channel = await owned_channel(session, user.id, channel_id)
    audit.record(
        session,
        user.id,
        "channel.delete",
        "channel",
        channel.id,
        [{"field": "name", "old": channel.name, "new": None}],
    )
    await session.delete(channel)
    await session.commit()
