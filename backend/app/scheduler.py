import asyncio
import logging
import secrets
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import session_factory
from app.models import (
    CheckDiagnostic,
    IdempotencyKey,
    IncidentEscalation,
    NotificationDelivery,
    Region,
    Site,
    StreamToken,
    WorkerHeartbeat,
)
from app.services.certificates import days_left, inspect_certificate
from app.services.domains import domain_expiry, hostname_of
from app.services.monitoring import record_probe
from app.services.monitors import in_maintenance
from app.services.notifications import enqueue
from app.services.probe import ProbeResult, probe_monitor
from app.services.templates import human_duration

logger = logging.getLogger("pulsewatch.worker")
scheduler = AsyncIOScheduler(timezone=UTC)
DOMAIN_REFRESH = timedelta(hours=24)
TLS_TYPES = {"http", "keyword", "ssl-only"}


async def expiry_alerts(session: AsyncSession, site: Site, now: datetime) -> None:
    notified = dict(site.expiry_notified or {})
    for kind, expires, label in (
        ("ssl", site.ssl_expires_at, "сертификат"),
        ("domain", site.domain_expires_at, "домен"),
    ):
        if expires is None:
            continue
        left = days_left(expires, now)
        crossed = [threshold for threshold in site.expiry_thresholds if left <= threshold]
        if not crossed:
            notified.pop(kind, None)
            continue
        level = min(crossed)
        if kind in notified and notified[kind] <= level:
            continue
        message = (
            f"{label} истёк {expires:%d.%m.%Y}"
            if left < 0
            else f"{label} истекает через {left} дн., {expires:%d.%m.%Y}"
        )
        context = {
            "name": site.name,
            "url": site.url,
            "status": "expiring",
            "code": "н/д",
            "duration": f"{max(left, 0)} дн.",
            "error": message,
        }
        await enqueue(session, site, f"⏳ {site.name}: {message}", "expiring", context)
        notified[kind] = level
    site.expiry_notified = notified


async def _refresh_expiry(site: Site, token: str) -> None:
    now = datetime.now(UTC)
    ssl_due = site.monitor_type in TLS_TYPES and (
        site.ssl_checked_at is None
        or site.ssl_checked_at < now - timedelta(hours=settings.ssl_refresh_hours)
    )
    domain_due = site.check_domain and (
        site.domain_checked_at is None or site.domain_checked_at < now - DOMAIN_REFRESH
    )
    if not ssl_due and not domain_due:
        return
    report = await inspect_certificate(site.url) if ssl_due else None
    host = hostname_of(site.url)
    domain = await domain_expiry(host) if domain_due and host else None
    async with session_factory() as session, session.begin():
        owned = await session.scalar(
            select(Site).where(Site.id == site.id, Site.lease_token == token).with_for_update()
        )
        if owned is None:
            return
        if ssl_due:
            owned.ssl_checked_at = now
            owned.ssl_expires_at = report.expires_at if report else owned.ssl_expires_at
            owned.ssl_details = report.details() if report else owned.ssl_details
        if domain_due:
            owned.domain_checked_at = now
            owned.domain_expires_at = domain or owned.domain_expires_at
        await expiry_alerts(session, owned, now)


def _next_run(site: Site, now: datetime) -> datetime:
    if site.monitor_type == "heartbeat":
        expected = (site.last_ping_at or site.created_at or now) + timedelta(
            seconds=(site.heartbeat_period_seconds or 3600) + (site.heartbeat_grace_seconds or 0)
        )
        return (
            expected
            if expected > now
            else now + timedelta(seconds=site.heartbeat_period_seconds or 3600)
        )
    jitter = secrets.randbelow(max(2, site.interval_seconds // 10))
    return now + timedelta(seconds=site.interval_seconds + jitter)


async def _run_single_check(site: Site, token: str) -> None:
    try:
        now = datetime.now(UTC)
        if in_maintenance(site, now):
            return
        if site.monitor_type == "heartbeat":
            anchor = site.last_ping_at or site.created_at or now
            deadline = anchor + timedelta(
                seconds=(site.heartbeat_period_seconds or 3600)
                + (site.heartbeat_grace_seconds or 0)
            )
            if now >= deadline:
                silence = human_duration(int((now - anchor).total_seconds()))
                await record_probe(
                    site.id, ProbeResult(False, None, None, f"Нет сигнала {silence}"), token
                )
            return
        try:
            result = await asyncio.wait_for(probe_monitor(site), timeout=site.timeout_seconds + 2)
        except TimeoutError:
            result = ProbeResult(False, None, None, "Истёк таймаут проверки")
        await record_probe(site.id, result, token)
        await _refresh_expiry(site, token)
    except Exception:
        logger.exception("probe_failed", extra={"extra_fields": {"site_id": site.id}})
    finally:
        async with session_factory() as session, session.begin():
            owned = await session.scalar(
                select(Site).where(Site.id == site.id, Site.lease_token == token).with_for_update()
            )
            if owned:
                owned.lease_token = None
                owned.lease_until = None
                owned.next_check_at = _next_run(owned, datetime.now(UTC))


async def heartbeat(name: str = "worker") -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        await session.execute(
            insert(WorkerHeartbeat)
            .values(name=name, seen_at=now)
            .on_conflict_do_update(index_elements=["name"], set_={"seen_at": now})
        )
        if name == "worker":
            await session.execute(
                insert(Region)
                .values(
                    code=settings.worker_region,
                    name=settings.worker_region_name,
                    is_primary=True,
                    last_seen_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["code"],
                    set_={
                        "last_seen_at": now,
                        "is_primary": True,
                        "name": settings.worker_region_name,
                    },
                )
            )


async def run_due_checks() -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        rows = list(
            await session.scalars(
                select(Site)
                .where(
                    Site.paused.is_(False),
                    Site.next_check_at <= now,
                    or_(Site.lease_until.is_(None), Site.lease_until < now),
                )
                .order_by(Site.next_check_at)
                .limit(settings.check_concurrency)
                .with_for_update(skip_locked=True)
            )
        )
        claimed: list[tuple[Site, str]] = []
        for site in rows:
            token = secrets.token_hex(16)
            site.lease_token = token
            site.lease_until = now + timedelta(minutes=10)
            claimed.append((site, token))
    await asyncio.gather(*(_run_single_check(site, token) for site, token in claimed))


ESCALATION_QUERY = text(
    """
    SELECT i.id AS incident_id, r.id AS rule_id, r.channel_id, s.id AS site_id,
           extract(epoch FROM now() - i.started_at)::int AS seconds
    FROM incidents i
    JOIN sites s ON s.id = i.site_id
    JOIN escalation_rules r ON r.user_id = s.user_id AND r.enabled
         AND (r.site_id IS NULL OR r.site_id = s.id)
    JOIN notification_channels c ON c.id = r.channel_id AND c.enabled
    WHERE i.ended_at IS NULL AND i.kind = 'down' AND i.acknowledged_at IS NULL
      AND i.started_at <= now() - make_interval(mins => r.after_minutes)
      AND NOT EXISTS (
          SELECT 1 FROM incident_escalations e WHERE e.incident_id = i.id AND e.rule_id = r.id
      )
    LIMIT 100
    """
)


async def escalate_incidents() -> None:
    async with session_factory() as session, session.begin():
        for row in (await session.execute(ESCALATION_QUERY)).all():
            inserted = await session.scalar(
                insert(IncidentEscalation)
                .values(incident_id=row.incident_id, rule_id=row.rule_id)
                .on_conflict_do_nothing()
                .returning(IncidentEscalation.rule_id)
            )
            site = await session.get(Site, row.site_id)
            if inserted is None or site is None:
                continue
            session.add(
                NotificationDelivery(
                    channel_id=row.channel_id,
                    payload={
                        "site_id": site.id,
                        "name": site.name,
                        "event": "escalation",
                        "text": f"⏰ {site.name} не отвечает уже {human_duration(row.seconds)}. "
                        "Инцидент не взят в работу.",
                    },
                )
            )


BASELINE_QUERY = text(
    """
    WITH recent AS (
        SELECT site_id, response_ms FROM checks
        WHERE ok AND response_ms IS NOT NULL AND checked_at >= now() - interval '7 days'
          AND (region IS NULL OR region = :region)
    ), medians AS (
        SELECT site_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY response_ms) AS median
        FROM recent GROUP BY site_id HAVING count(*) >= 50
    )
    UPDATE sites SET baseline_ms = round(stats.median), baseline_mad_ms = round(stats.mad),
                     baseline_at = now()
    FROM (
        SELECT m.site_id, m.median,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(r.response_ms - m.median)) AS mad
        FROM recent r JOIN medians m USING (site_id) GROUP BY m.site_id, m.median
    ) stats
    WHERE sites.id = stats.site_id
    """
)


async def refresh_baselines() -> None:
    async with session_factory() as session, session.begin():
        await session.execute(BASELINE_QUERY, {"region": settings.worker_region})


async def cleanup() -> None:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        await session.execute(
            delete(IdempotencyKey).where(IdempotencyKey.created_at < now - timedelta(hours=24))
        )
        await session.execute(delete(StreamToken).where(StreamToken.expires_at < now))
        await session.execute(
            delete(CheckDiagnostic).where(CheckDiagnostic.created_at < now - timedelta(days=90))
        )


def start_scheduler() -> None:
    scheduler.add_job(
        run_due_checks,
        "interval",
        seconds=settings.scheduler_tick_seconds,
        id="checks",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(heartbeat, "interval", seconds=15, id="heartbeat", max_instances=1)
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
