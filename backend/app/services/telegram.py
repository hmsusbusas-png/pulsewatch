import asyncio
import logging

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import session_factory
from app.models import User

logger = logging.getLogger("pulsewatch.telegram")

API_BASE = "https://api.telegram.org"


def is_enabled() -> bool:
    return bool(settings.telegram_bot_token)


def _api_url(method: str) -> str:
    return f"{API_BASE}/bot{settings.telegram_bot_token}/{method}"


async def send_message(chat_id: int, text: str) -> None:
    if not is_enabled():
        logger.info("telegram_disabled", extra={"extra_fields": {"chat_id": chat_id, "text": text}})
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                _api_url("sendMessage"),
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "telegram_send_error",
            extra={"extra_fields": {"chat_id": chat_id, "error": str(exc)[:200]}},
        )
        return
    if response.status_code != 200:
        logger.warning(
            "telegram_send_failed",
            extra={
                "extra_fields": {
                    "chat_id": chat_id,
                    "status": response.status_code,
                    "body": response.text[:300],
                }
            },
        )


def format_down_message(site_name: str, reason: str, failed_checks: int) -> str:
    return f"DOWN: {site_name}\nReason: {reason}\nFailed checks in a row: {failed_checks}"


def format_recovery_message(site_name: str, downtime_seconds: int) -> str:
    minutes = downtime_seconds // 60
    duration = f"{minutes} min" if minutes else f"{downtime_seconds} s"
    return f"UP: {site_name}\nDowntime: {duration}"


def parse_start_token(text: str) -> str | None:
    parts = text.strip().split()
    if len(parts) != 2:
        return None
    if parts[0].split("@")[0] != "/start":
        return None
    return parts[1]


async def fetch_bot_username() -> str | None:
    if not is_enabled():
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(_api_url("getMe"))
    except httpx.HTTPError as exc:
        logger.warning("telegram_get_me_error", extra={"extra_fields": {"error": str(exc)[:200]}})
        return None
    if response.status_code != 200:
        logger.warning(
            "telegram_get_me_failed",
            extra={"extra_fields": {"status": response.status_code}},
        )
        return None
    payload = response.json()
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    username = result.get("username")
    return username if isinstance(username, str) else None


async def link_chat_by_token(token: str, chat_id: int) -> str | None:
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_link_token == token))
        if user is None:
            return None
        user.telegram_chat_id = chat_id
        user.telegram_link_token = None
        await session.commit()
        return user.email


async def _fetch_updates(offset: int) -> list[dict[str, object]]:
    params: dict[str, str | int] = {"timeout": settings.telegram_poll_timeout_seconds}
    if offset:
        params["offset"] = offset
    async with httpx.AsyncClient(timeout=settings.telegram_poll_timeout_seconds + 15) as client:
        response = await client.get(_api_url("getUpdates"), params=params)
    if response.status_code != 200:
        logger.warning(
            "telegram_poll_failed",
            extra={"extra_fields": {"status": response.status_code, "body": response.text[:300]}},
        )
        return []
    payload = response.json()
    result = payload.get("result")
    return result if isinstance(result, list) else []


async def _answer_start(token: str, chat_id: int) -> None:
    email = await link_chat_by_token(token, chat_id)
    if email is None:
        await send_message(chat_id, "Link token is invalid or already used.")
        return
    await send_message(chat_id, f"Chat linked to pulsewatch account {email}.")


async def poll_telegram_updates() -> None:
    offset = 0
    logger.info("telegram_polling_started")
    while True:
        try:
            updates = await _fetch_updates(offset)
        except (TimeoutError, httpx.HTTPError) as exc:
            logger.warning("telegram_poll_error", extra={"extra_fields": {"error": str(exc)[:200]}})
            await asyncio.sleep(5)
            continue
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = update_id + 1
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat")
            text = message.get("text")
            if not isinstance(chat, dict) or not isinstance(text, str):
                continue
            chat_id = chat.get("id")
            if not isinstance(chat_id, int):
                continue
            token = parse_start_token(text)
            if token is not None:
                await _answer_start(token, chat_id)
        if not updates:
            await asyncio.sleep(1)
