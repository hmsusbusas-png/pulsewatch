import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crypto import encrypt_config
from app.database import session_factory
from app.models import NotificationChannel, Site, User
from app.security import hash_refresh_token
from app.services.billing import enforce_channel_limit

logger = logging.getLogger("pulsewatch.telegram")
API_BASE = "https://api.telegram.org"


def is_enabled() -> bool:
    return bool(settings.bot_token or settings.telegram_bot_token)


def _api_url(method: str) -> str:
    return f"{API_BASE}/bot{settings.bot_token or settings.telegram_bot_token}/{method}"


_send_lock = asyncio.Lock()
_last_send = 0.0


async def telegram_request(method: str, payload: dict[str, object]) -> httpx.Response:
    from app.services.rate_limit import allow_persistent

    for _ in range(60):
        if await allow_persistent("telegram:outbound", 28, 1):
            break
        await asyncio.sleep(1)
    else:
        raise ConnectionError("Telegram: превышен лимит отправки")
    global _last_send
    async with _send_lock:
        await asyncio.sleep(max(0, 1 / 30 - (time.monotonic() - _last_send)))
        async with httpx.AsyncClient(timeout=35, trust_env=False) as client:
            response = await client.post(_api_url(method), json=payload)
        _last_send = time.monotonic()
    return response


async def send_message(chat_id: int, text: str) -> None:
    if not is_enabled():
        return
    for _attempt in range(5):
        response = await telegram_request(
            "sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        )
        if response.status_code != 429:
            response.raise_for_status()
            return
        await asyncio.sleep(
            min(int(response.json().get("parameters", {}).get("retry_after", 1)), 60)
        )
    raise ConnectionError("Telegram: исчерпаны повторы")


def format_down_message(site_name: str, reason: str, failed_checks: int) -> str:
    return f"\U0001f534 {site_name}\n{reason}\nНеудачных проверок: {failed_checks}"


def format_recovery_message(site_name: str, downtime_seconds: int) -> str:
    minutes = downtime_seconds // 60
    duration = f"{minutes} min" if minutes else f"{downtime_seconds} s"
    return f"\U0001f7e2 {site_name}\nВосстановлен. Простой: {duration}"


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


async def link_chat_by_token(token: str, chat_id: int, name: str | None = None) -> str | None:
    from app.services.rate_limit import allow_persistent

    if not await allow_persistent(f"telegram:link:{chat_id}", 10, 600):
        return None
    async with session_factory() as session:
        user = await session.scalar(
            select(User)
            .where(User.telegram_link_token == hash_refresh_token(token))
            .with_for_update()
        )
        if (
            user is None
            or user.telegram_link_expires_at is None
            or user.telegram_link_expires_at <= datetime.now(UTC)
        ):
            return None
        occupied = await session.scalar(
            select(User.id).where(User.telegram_chat_id == chat_id, User.id != user.id)
        )
        if occupied is not None:
            return None
        await enforce_channel_limit(session, user)
        user.telegram_chat_id = chat_id
        user.telegram_name = name
        user.telegram_link_token = None
        user.telegram_link_expires_at = None
        channel = NotificationChannel(
            user_id=user.id,
            type="telegram",
            name="Telegram",
            config=encrypt_config({"chat_id": str(chat_id)}),
            verified_at=datetime.now(UTC),
        )
        session.add(channel)
        await session.flush()
        from app.models import SiteChannel

        for site_id in await session.scalars(select(Site.id).where(Site.user_id == user.id)):
            session.add(SiteChannel(site_id=site_id, channel_id=channel.id))
        await session.commit()
        return user.email


async def bot_command(chat_id: int, text: str, name: str | None = None) -> str:
    token = parse_start_token(text)
    if token is not None:
        email = await link_chat_by_token(token, chat_id, name)
        return (
            "Чат привязан. Команды доступны по /help."
            if email
            else "Код недействителен, истёк или уже использован."
        )
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return "Команды: /help"
    command = parts[0].split("@")[0]
    argument = parts[1] if len(parts) == 2 else ""
    if command in {"/help", "/start"}:
        return (
            "/status: сводка\n/sites: сайты\n/pause <id|имя>\n"
            "/resume <id|имя>\n/mute 1h|8h|24h\n/unlink: отвязать"
        )
    async with session_factory() as session:
        user = await session.scalar(
            select(User).where(User.telegram_chat_id == chat_id).with_for_update()
        )
        if user is None:
            return "Сначала создайте ссылку привязки в аккаунте Pulsewatch."
        sites = list(
            await session.scalars(select(Site).where(Site.user_id == user.id).order_by(Site.id))
        )
        if command == "/status":
            up = sum(s.status.value == "up" for s in sites)
            down = sum(s.status.value == "down" for s in sites)
            return f"Сайтов: {len(sites)}. Работают: {up}. Недоступны: {down}."
        if command == "/sites":
            return (
                "\n".join(
                    f"{site.id}. {site.name}: {'пауза' if site.paused else site.status.value}"
                    for site in sites
                )[:4000]
                or "Сайтов пока нет."
            )
        if command in {"/pause", "/resume"}:
            site = next((s for s in sites if str(s.id) == argument or s.name == argument), None)
            if site is None:
                return "Сайт не найден."
            if command == "/resume":
                from app.services.billing import current_plan
                from app.services.monitors import validate_monitor

                _, plan = await current_plan(session, user)
                validate_monitor(plan, site)
                if site.pause_reason and site.pause_reason.startswith("plan_"):
                    return "Сайт остановлен ограничением тарифа."
            site.paused = command == "/pause"
            site.pause_reason = "user" if site.paused else None
        elif command == "/mute":
            hours = {"1h": 1, "8h": 8, "24h": 24}.get(argument)
            if hours is None:
                return "Укажите /mute 1h, 8h или 24h."
            user.telegram_mute_until = datetime.now(UTC) + timedelta(hours=hours)
        elif command == "/unlink":
            await unlink(session, user)
        else:
            return "Неизвестная команда. Список команд доступен по /help."
        await session.commit()
        return "Готово."


async def unlink(session: "AsyncSession", user: User) -> None:
    from sqlalchemy import delete

    await session.execute(
        delete(NotificationChannel).where(
            NotificationChannel.user_id == user.id, NotificationChannel.type == "telegram"
        )
    )
    user.telegram_chat_id = None
    user.telegram_name = None
    user.telegram_link_token = None
    user.telegram_link_expires_at = None


async def receive_update(update: dict[str, object]) -> None:
    message = update.get("message")
    if not isinstance(message, dict):
        return
    chat, text = message.get("chat"), message.get("text")
    if not isinstance(chat, dict) or chat.get("type") != "private" or not isinstance(text, str):
        return
    chat_id = chat.get("id")
    if isinstance(chat_id, int):
        from app.errors import ApiError

        try:
            answer = await bot_command(
                chat_id, text, str(chat.get("username", chat.get("first_name", "")))
            )
        except ApiError as exc:
            answer = exc.message
        await send_message(chat_id, answer)


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


async def poll_telegram_updates() -> None:
    from app.scheduler import heartbeat

    offset = 0
    logger.info("telegram_polling_started")
    while True:
        await heartbeat("bot")
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
            try:
                await receive_update(update)
            except (httpx.HTTPError, ConnectionError):
                logger.exception("telegram_update_failed")
        if not updates:
            await asyncio.sleep(1)
