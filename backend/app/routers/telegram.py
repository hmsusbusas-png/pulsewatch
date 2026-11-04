from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.config import settings
from app.deps import CurrentUser, SessionDep
from app.errors import ApiError
from app.security import generate_link_token, hash_refresh_token
from app.services.billing import enforce_channel_limit
from app.services.telegram import fetch_bot_username, is_enabled, receive_update, unlink

router = APIRouter(prefix="/telegram", tags=["telegram"])


class LinkResponse(BaseModel):
    link_token: str
    bot_username: str | None
    url: str | None
    ttl: int = 600
    instructions: str = "Откройте ссылку и нажмите Start. Код действует 10 минут."


async def new_link(user: CurrentUser, session: SessionDep) -> LinkResponse:
    from app.services.rate_limit import allow_persistent

    if not await allow_persistent(f"telegram:link:user:{user.id}", 10, 600):
        raise ApiError(429, "rate_limited", "Повторите привязку позже")
    if user.telegram_chat_id is not None:
        raise ApiError(409, "telegram_already_linked", "Сначала отвяжите существующий чат")
    await enforce_channel_limit(session, user)
    token = generate_link_token()
    user.telegram_link_token = hash_refresh_token(token)
    user.telegram_link_expires_at = datetime.now(UTC) + timedelta(minutes=10)
    username = await fetch_bot_username()
    await session.commit()
    return LinkResponse(
        link_token=token,
        bot_username=username,
        url=f"https://t.me/{username}?start={token}" if username else None,
    )


@router.post("/link", response_model=LinkResponse)
async def create_link(user: CurrentUser, session: SessionDep) -> LinkResponse:
    return await new_link(user, session)


class TelegramStatus(BaseModel):
    linked: bool
    enabled: bool
    name: str | None
    muted_until: datetime | None


@router.get("/status", response_model=TelegramStatus)
async def status(user: CurrentUser) -> TelegramStatus:
    return TelegramStatus(
        linked=user.telegram_chat_id is not None,
        enabled=is_enabled(),
        name=user.telegram_name,
        muted_until=user.telegram_mute_until,
    )


@router.delete("/link", status_code=204)
async def delete_link(user: CurrentUser, session: SessionDep) -> None:
    await unlink(session, user)
    await session.commit()


@router.post("/webhook")
async def webhook(payload: dict[str, object], request: Request) -> dict[str, bool]:
    import hmac

    if (
        not settings.public_url
        or not settings.telegram_webhook_secret
        or not hmac.compare_digest(
            request.headers.get("x-telegram-bot-api-secret-token", ""),
            settings.telegram_webhook_secret,
        )
    ):
        raise ApiError(403, "invalid_signature", "Недействительный секрет Telegram")
    await receive_update(payload)
    return {"ok": True}
