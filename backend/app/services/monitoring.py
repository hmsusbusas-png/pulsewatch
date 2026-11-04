import json
import logging
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crypto import encrypt_text
from app.database import session_factory
from app.errors import ApiError
from app.models import Check, CheckDiagnostic, Incident, Site, SiteRegionState, SiteStatus
from app.services.events import publish
from app.services.monitors import in_maintenance
from app.services.notifications import enqueue
from app.services.probe import ProbeResult
from app.services.redaction import redact_body, redact_headers
from app.services.telegram import format_down_message, format_recovery_message
from app.services.templates import human_duration
from app.ssrf import resolve_host

logger = logging.getLogger("pulsewatch.monitoring")
DEGRADATION_STREAK = 5
RECOVERY_STREAK = 5
HOST_TYPES = {"http", "keyword", "tcp", "ssl-only"}


async def close_open_incident(
    session: AsyncSession, site_id: int, closed_at: datetime, kind: str = "down"
) -> datetime | None:
    result = await session.execute(
        select(Incident.id, Incident.started_at)
        .where(Incident.site_id == site_id, Incident.kind == kind, Incident.ended_at.is_(None))
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


async def open_incident_id(
    session: AsyncSession,
    site_id: int,
    reason: str,
    started_at: datetime,
    kind: str = "down",
    confirmed_at: datetime | None = None,
) -> int | None:
    statement = (
        pg_insert(Incident)
        .values(
            site_id=site_id,
            started_at=started_at,
            reason=reason[:500],
            kind=kind,
            confirmed_at=confirmed_at or started_at,
        )
        .on_conflict_do_nothing(
            index_elements=["site_id", "kind"], index_where=Incident.ended_at.is_(None)
        )
        .returning(Incident.id)
    )
    return await session.scalar(statement)


async def open_incident(
    session: AsyncSession, site_id: int, reason: str, started_at: datetime, kind: str = "down"
) -> bool:
    return await open_incident_id(session, site_id, reason, started_at, kind) is not None


async def _first_failure_at(session: AsyncSession, site_id: int, fallback: datetime) -> datetime:
    last_ok = (
        select(func.max(Check.checked_at))
        .where(Check.site_id == site_id, Check.ok.is_(True))
        .scalar_subquery()
    )
    first = await session.scalar(
        select(func.min(Check.checked_at)).where(
            Check.site_id == site_id,
            Check.ok.is_(False),
            or_(last_ok.is_(None), Check.checked_at > last_ok),
        )
    )
    return min(first, fallback) if first else fallback


async def _resolution(site_id: int) -> list[str]:
    async with session_factory() as session:
        row = (
            await session.execute(select(Site.url, Site.monitor_type).where(Site.id == site_id))
        ).first()
    if row is None or row.monitor_type not in HOST_TYPES:
        return []
    parsed = urlparse(row.url)
    if not parsed.hostname:
        return []
    try:
        return (await resolve_host(parsed.hostname, parsed.port or 443))[:10]
    except (ApiError, OSError, TimeoutError):
        return []


async def _region_failures(
    session: AsyncSession, site: Site, region: str, result: ProbeResult, checked_at: datetime
) -> list[SiteRegionState]:
    await session.execute(
        pg_insert(SiteRegionState)
        .values(site_id=site.id, region=region, next_check_at=checked_at)
        .on_conflict_do_nothing(index_elements=["site_id", "region"])
    )
    state = await session.get(SiteRegionState, (site.id, region), with_for_update=True)
    if state is not None:
        state.consecutive_failures = 0 if result.ok else state.consecutive_failures + 1
        state.status = (
            "up"
            if result.ok
            else ("down" if state.consecutive_failures >= site.failure_threshold else state.status)
        )
        state.last_checked_at = checked_at
        state.last_response_ms = result.response_ms
        state.last_error = result.error
    return list(
        await session.scalars(
            select(SiteRegionState).where(
                SiteRegionState.site_id == site.id, SiteRegionState.region.in_(site.regions)
            )
        )
    )


def _alert_context(site: Site, result: ProbeResult, duration: int | None = None) -> dict[str, str]:
    return {
        "name": site.name,
        "url": site.url,
        "status": site.status.value,
        "code": str(result.status_code) if result.status_code else "н/д",
        "duration": human_duration(duration) if duration is not None else "н/д",
        "error": result.error or "н/д",
    }


async def _track_latency(
    session: AsyncSession, site: Site, result: ProbeResult, checked_at: datetime
) -> None:
    if not (result.ok and result.response_ms is not None and site.baseline_ms):
        return
    sigma = max(1.4826 * (site.baseline_mad_ms or 0), site.baseline_ms * 0.05)
    slow = result.response_ms > max(site.baseline_ms * 2, site.baseline_ms + 5 * sigma)
    site.slow_streak = site.slow_streak + 1 if slow else 0
    site.fast_streak = 0 if slow else site.fast_streak + 1
    if site.slow_streak == DEGRADATION_STREAK:
        reason = f"Отклик {result.response_ms} мс при обычных {site.baseline_ms} мс"
        incident_id = await open_incident_id(
            session, site.id, reason, checked_at, kind="degraded", confirmed_at=checked_at
        )
        if incident_id is not None:
            context = {**_alert_context(site, result), "error": reason}
            await enqueue(session, site, f"🟠 {site.name}\n{reason}", "degraded", context)
            await publish(
                session,
                site.user_id,
                "incident_opened",
                {"incident_id": incident_id, "site_id": site.id, "kind": "degraded"},
            )
    if site.fast_streak == RECOVERY_STREAK:
        started_at = await close_open_incident(session, site.id, checked_at, kind="degraded")
        if started_at is not None:
            text = f"🟢 {site.name}\nОтклик вернулся к норме"
            await enqueue(session, site, text, None, None)
            await publish(
                session,
                site.user_id,
                "incident_resolved",
                {"site_id": site.id, "kind": "degraded", "ended_at": checked_at},
            )


async def _remember_response(site: Site, result: ProbeResult) -> None:
    if result.evidence is None or site.monitor_type not in {"http", "keyword"}:
        return
    snapshot = {
        "status_code": result.status_code,
        "headers": redact_headers(result.evidence.headers),
        "body": result.evidence.body.decode("utf-8", "replace"),
    }
    site.last_response_encrypted = encrypt_text(json.dumps(snapshot, ensure_ascii=False))


def _diagnostic(
    site: Site, result: ProbeResult, region: str, resolved: list[str], checked_at: datetime
) -> CheckDiagnostic:
    evidence = result.evidence
    return CheckDiagnostic(
        site_id=site.id,
        created_at=checked_at,
        region=region,
        status_code=result.status_code,
        error=result.error,
        ip=evidence.ip if evidence else (resolved[0] if resolved else None),
        resolved=resolved,
        headers=redact_headers(evidence.headers) if evidence else {},
        body_encrypted=encrypt_text(redact_body(evidence.body))
        if evidence and evidence.body
        else None,
        timings=result.timings.as_dict() if result.timings else {},
    )


async def record_probe(
    site_id: int, result: ProbeResult, lease_token: str | None = None, region: str | None = None
) -> None:
    checked_at = datetime.now(UTC)
    region_code = region or settings.worker_region
    resolved = await _resolution(site_id) if not result.ok else []
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
        if lease_token is not None and region is None and site.lease_token != lease_token:
            return
        if site.paused or in_maintenance(site, checked_at):
            return
        timings = result.timings
        check = Check(
            site_id=site.id,
            checked_at=checked_at,
            status_code=result.status_code,
            response_ms=result.response_ms,
            ok=result.ok,
            error=result.error,
            dns_ms=timings.dns_ms if timings else None,
            connect_ms=timings.connect_ms if timings else None,
            tls_ms=timings.tls_ms if timings else None,
            ttfb_ms=timings.ttfb_ms if timings else None,
            transfer_ms=timings.transfer_ms if timings else None,
            region=region_code,
        )
        session.add(check)
        await session.flush()
        previous_status = site.status
        site.last_checked_at = checked_at
        site_name = site.name
        multi = len(site.regions) > 1
        if multi:
            states = await _region_failures(session, site, region_code, result, checked_at)
            failing = [s for s in states if s.consecutive_failures >= site.failure_threshold]
            site.consecutive_failures = max((s.consecutive_failures for s in states), default=0)
            confirmed_down = len(failing) * 2 > len(site.regions)
        else:
            site.consecutive_failures = 0 if result.ok else site.consecutive_failures + 1
            confirmed_down = site.consecutive_failures >= site.failure_threshold
        if not confirmed_down and (result.ok or multi):
            if site.status is SiteStatus.down:
                started_at = await close_open_incident(session, site.id, checked_at)
                if started_at is not None:
                    downtime = max(int((checked_at - started_at).total_seconds()), 0)
                    recovery_notification = (site.name, downtime)
                    await publish(
                        session,
                        site.user_id,
                        "incident_resolved",
                        {"site_id": site.id, "kind": "down", "ended_at": checked_at},
                    )
            if result.ok or site.status is SiteStatus.down:
                site.status = SiteStatus.up
        elif confirmed_down and site.status is not SiteStatus.down:
            reason = result.error or "check failed"
            if multi:
                reason = f"{len(failing)} из {len(site.regions)} регионов: {reason}"
            started = await _first_failure_at(session, site.id, checked_at)
            incident_id = await open_incident_id(
                session, site.id, reason, started, confirmed_at=checked_at
            )
            site.status = SiteStatus.down
            if incident_id is not None:
                down_notification = (site.name, reason, site.consecutive_failures)
                await session.execute(
                    update(CheckDiagnostic)
                    .where(
                        CheckDiagnostic.site_id == site.id,
                        CheckDiagnostic.incident_id.is_(None),
                        CheckDiagnostic.created_at >= started,
                    )
                    .values(incident_id=incident_id)
                )
                await publish(
                    session,
                    site.user_id,
                    "incident_opened",
                    {"incident_id": incident_id, "site_id": site.id, "kind": "down"},
                )
        if not result.ok and (site.consecutive_failures == 1 or down_notification is not None):
            diagnostic = _diagnostic(site, result, region_code, resolved, checked_at)
            if down_notification is not None:
                diagnostic.incident_id = await session.scalar(
                    select(Incident.id).where(
                        Incident.site_id == site.id,
                        Incident.kind == "down",
                        Incident.ended_at.is_(None),
                    )
                )
            session.add(diagnostic)
        if region_code == settings.worker_region:
            await _track_latency(session, site, result, checked_at)
        await _remember_response(site, result)
        if site.monitor_type == "dns" and result.records is not None:
            current = list(result.records)
            if site.dns_values is not None and current and sorted(site.dns_values) != current:
                text = (
                    f"🌐 {site.name}\nDNS {site.dns_record_type} изменилась:\n"
                    f"было: {', '.join(site.dns_values) or 'н/д'}\nстало: {', '.join(current)}"
                )
                await enqueue(session, site, text, "dns_changed", None)
            if current:
                site.dns_values = current
        if (
            down_notification is None
            and site.status is SiteStatus.down
            and not result.ok
            and (
                site.last_notified_at is None
                or (checked_at - site.last_notified_at).total_seconds() >= site.cooldown_seconds
            )
        ):
            down_notification = (
                site.name,
                result.error or "Проверка не пройдена",
                site.consecutive_failures,
            )
        if down_notification is not None:
            await enqueue(
                session,
                site,
                format_down_message(*down_notification),
                "down",
                _alert_context(site, result),
            )
            site.last_notified_at = checked_at
        if recovery_notification is not None:
            await enqueue(
                session,
                site,
                format_recovery_message(*recovery_notification),
                "up",
                _alert_context(site, result, recovery_notification[1]),
            )
        await publish(
            session,
            site.user_id,
            "check_completed",
            {
                "site_id": site.id,
                "check_id": check.id,
                "ok": result.ok,
                "status": site.status.value,
                "status_code": result.status_code,
                "response_ms": result.response_ms,
                "checked_at": checked_at,
                "region": region_code,
            },
        )
        if site.status is not previous_status:
            await publish(
                session,
                site.user_id,
                "site_updated",
                {"site_id": site.id, "status": site.status.value},
            )
        notify_user_id = site.user_id
    if notify_user_id is None:
        return
    logger.info(
        "probe_processed",
        extra={
            "extra_fields": {
                "site": site_name,
                "ok": result.ok,
                "status_code": result.status_code,
                "response_ms": result.response_ms,
                "region": region_code,
            }
        },
    )


async def calculate_uptime(session: AsyncSession, site_id: int, since: datetime) -> float | None:
    from app.services.site_views import uptime_many

    return (await uptime_many(session, [site_id], since))[site_id]
