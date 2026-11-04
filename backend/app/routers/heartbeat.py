from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.database import session_factory
from app.errors import ApiError, ErrorResponse
from app.models import Site
from app.security import hash_refresh_token
from app.services.monitoring import record_probe
from app.services.probe import ProbeResult

router = APIRouter(tags=["heartbeat"])


class PingAccepted(BaseModel):
    ok: bool
    next_expected_at: datetime


async def register_ping(token: str, status: str, duration: int | None) -> PingAccepted:
    if not token.startswith("hb_") or len(token) > 80:
        raise ApiError(404, "heartbeat_not_found", "Адрес пинга не найден")
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        site = await session.scalar(
            select(Site)
            .where(
                Site.heartbeat_token_hash == hash_refresh_token(token),
                Site.monitor_type == "heartbeat",
            )
            .with_for_update()
        )
        if site is None:
            raise ApiError(404, "heartbeat_not_found", "Адрес пинга не найден")
        site_id = site.id
        site.last_ping_at = now
        expected = now + timedelta(
            seconds=(site.heartbeat_period_seconds or 3600) + (site.heartbeat_grace_seconds or 0)
        )
        site.next_check_at = expected
    failed = status == "fail"
    await record_probe(
        site_id,
        ProbeResult(
            not failed,
            None,
            duration,
            "Задача сообщила об ошибке (status=fail)" if failed else None,
        ),
    )
    return PingAccepted(ok=not failed, next_expected_at=expected)


DOC = (
    "Сигнал от cron-задачи. `status=fail` фиксирует сбой сразу, `duration` содержит длительность "
    "задачи в миллисекундах. Ответ 404 одинаков для неизвестного и отозванного токена."
)


@router.get(
    "/heartbeat/{token}",
    response_model=PingAccepted,
    summary="Пинг heartbeat (GET)",
    description=DOC,
    responses={404: {"model": ErrorResponse}},
)
async def ping_get(
    token: str,
    status: Literal["ok", "fail"] = "ok",
    duration: int | None = Query(None, ge=0, le=86400000),
) -> PingAccepted:
    return await register_ping(token, status, duration)


@router.post(
    "/heartbeat/{token}",
    response_model=PingAccepted,
    summary="Пинг heartbeat (POST)",
    description=DOC,
    responses={404: {"model": ErrorResponse}},
)
async def ping_post(
    token: str,
    status: Literal["ok", "fail"] = "ok",
    duration: int | None = Query(None, ge=0, le=86400000),
) -> PingAccepted:
    return await register_ping(token, status, duration)
