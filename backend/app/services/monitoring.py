import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import session_factory
from app.models import Check, Incident, Site, SiteStatus, User
from app.services.probe import ProbeResult
from app.services.telegram import format_down_message, format_recovery_message, send_message

logger = logging.getLogger("pulsewatch.monitoring")

DOWN_THRESHOLD = 2


async def close_open_incident(
    session: AsyncSession, site_id: int, closed_at: datetime
) -> datetime | None:
    result = await session.execute(
        select(Incident.id, Incident.started_at)
        .where(Incident.site_id == site_id, Incident.ended_at.is_(None))
        .with_for_update()
    )
    row = result.first()
    if row is None:
        return None
    incident_id, started_at = row
    incident = await session.get(Incident, incident_id)
    if incident is None:
        return None
    incident.ended_at = closed_at
    return started_at


async def open_incident(
    session: AsyncSession, site_id: int, reason: str, started_at: datetime
) -> bool:
    statement = (
        pg_insert(Incident)
        .values(site_id=site_id, started_at=started_at, reason=reason)
        .on_conflict_do_nothing(index_elements=["site_id"], index_where=Incident.ended_at.is_(None))
        .returning(Incident.id)
    )
    inserted = await session.scalar(statement)
    return inserted is not None


async def _notify(user_id: int, text: str) -> None:
    async with session_factory() as session:
        chat_id = await session.scalar(select(User.telegram_chat_id).where(User.id == user_id))
    if chat_id is None:
        return
    await send_message(chat_id, text)


async def process_probe_result(site_id: int, result: ProbeResult) -> None:
    checked_at = datetime.now(UTC)
    down_notification: tuple[str, str, int] | None = None
    recovery_notification: tuple[str, int] | None = None
    notify_user_id: int | None = None
    site_name = ""

    async with session_factory() as session, session.begin():
        site = await session.scalar(select(Site).where(Site.id == site_id).with_for_update())
        if site is None:
            logger.warning(
                "probe_result_for_missing_site", extra={"extra_fields": {"site_id": site_id}}
            )
            return
        session.add(
            Check(
                site_id=site.id,
                checked_at=checked_at,
                status_code=result.status_code,
                response_ms=result.response_ms,
                ok=result.ok,
                error=result.error,
            )
        )
        site.last_checked_at = checked_at
        site_name = site.name

        if result.ok:
            site.consecutive_failures = 0
            if site.status is SiteStatus.down:
                started_at = await close_open_incident(session, site.id, checked_at)
                if started_at is not None:
                    downtime = max(int((checked_at - started_at).total_seconds()), 0)
                    recovery_notification = (site.name, downtime)
            site.status = SiteStatus.up
        else:
            site.consecutive_failures += 1
            crossed_threshold = site.consecutive_failures >= DOWN_THRESHOLD
            if site.status is not SiteStatus.down and crossed_threshold:
                reason = result.error or "check failed"
                opened = await open_incident(session, site.id, reason, checked_at)
                site.status = SiteStatus.down
                if opened:
                    down_notification = (site.name, reason, site.consecutive_failures)
        notify_user_id = site.user_id

    if notify_user_id is None:
        return
    if down_notification is not None:
        await _notify(
            notify_user_id,
            format_down_message(*down_notification),
        )
    if recovery_notification is not None:
        await _notify(
            notify_user_id,
            format_recovery_message(*recovery_notification),
        )
    logger.info(
        "probe_processed",
        extra={
            "extra_fields": {
                "site": site_name,
                "ok": result.ok,
                "status_code": result.status_code,
                "response_ms": result.response_ms,
            }
        },
    )


async def calculate_uptime(session: AsyncSession, site_id: int, since: datetime) -> float | None:
    total, successful = (
        await session.execute(
            select(
                func.count(Check.id),
                func.count(Check.id).filter(Check.ok.is_(True)),
            ).where(Check.site_id == site_id, Check.checked_at >= since)
        )
    ).one()
    if not total:
        return None
    return round(successful * 100.0 / total, 2)


async def purge_old_checks(session: AsyncSession, retention_days: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    deleted_rows = await session.execute(
        delete(Check).where(Check.checked_at < cutoff).returning(Check.id)
    )
    await session.commit()
    return len(deleted_rows.all())
