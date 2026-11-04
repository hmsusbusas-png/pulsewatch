from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crypto import decrypt_config
from app.models import (
    Check,
    DailyUptime,
    Incident,
    Region,
    Site,
    SiteChannel,
    SiteRegionState,
    SiteStatus,
)
from app.schemas import DayUptime, RegionState, SeriesPoint, SiteDays, SiteResponse
from app.services.monitors import FIELDS, in_maintenance


async def uptime_many(
    session: AsyncSession, ids: list[int], since: datetime
) -> dict[int, float | None]:
    now = datetime.now(UTC)
    first = datetime.combine(since.date() + timedelta(days=1), time.min, UTC)
    today = datetime.combine(now.date(), time.min, UTC)
    totals = {site_id: [0, 0] for site_id in ids}
    daily = await session.execute(
        select(DailyUptime.site_id, func.sum(DailyUptime.total), func.sum(DailyUptime.successful))
        .where(
            DailyUptime.site_id.in_(ids),
            DailyUptime.day >= first.date(),
            DailyUptime.day < today.date(),
        )
        .group_by(DailyUptime.site_id)
    )
    for site_id, total, successful in daily:
        totals[site_id] = [total, successful]
    raw = await session.execute(
        select(Check.site_id, func.count(Check.id), func.count(Check.id).filter(Check.ok.is_(True)))
        .where(
            Check.site_id.in_(ids),
            Check.checked_at >= since,
            Check.checked_at <= now,
            (Check.checked_at < first) | (Check.checked_at >= today),
        )
        .group_by(Check.site_id)
    )
    for site_id, total, successful in raw:
        totals[site_id][0] += total
        totals[site_id][1] += successful
    return {
        site_id: round(successful * 100 / total, 3) if total else None
        for site_id, (total, successful) in totals.items()
    }


def incident_seconds_by_day(
    incidents: Sequence[tuple[int, datetime, datetime | None]], days: list[date], now: datetime
) -> dict[int, dict[date, tuple[int, int]]]:
    result: dict[int, dict[date, tuple[int, int]]] = {}
    for site_id, started_at, ended_at in incidents:
        finish = ended_at or now
        for day in days:
            day_start = datetime.combine(day, time.min, UTC)
            day_end = day_start + timedelta(days=1)
            low, high = max(started_at, day_start), min(finish, day_end)
            starts_here = day_start <= started_at < day_end
            if high > low or starts_here:
                seconds = max(0, int((high - low).total_seconds()))
                count, total = result.setdefault(site_id, {}).get(day, (0, 0))
                result[site_id][day] = (count + (1 if starts_here else 0), total + seconds)
    return result


async def daily_uptime(session: AsyncSession, ids: list[int], days: int) -> list[SiteDays]:
    now = datetime.now(UTC)
    today = now.date()
    calendar = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    rows = await session.execute(
        select(DailyUptime.site_id, DailyUptime.day, DailyUptime.total, DailyUptime.successful)
        .where(DailyUptime.site_id.in_(ids), DailyUptime.day >= calendar[0])
        .order_by(DailyUptime.site_id, DailyUptime.day)
    )
    rollups: dict[int, dict[date, tuple[int, int]]] = {}
    for site_id, day, total, successful in rows:
        rollups.setdefault(site_id, {})[day] = (total, successful)
    incidents = (
        await session.execute(
            select(Incident.site_id, Incident.started_at, Incident.ended_at).where(
                Incident.site_id.in_(ids),
                Incident.kind == "down",
                (Incident.ended_at.is_(None))
                | (Incident.ended_at >= datetime.combine(calendar[0], time.min, UTC)),
            )
        )
    ).all()
    downtime = incident_seconds_by_day(
        [(row[0], row[1], row[2]) for row in incidents], calendar, now
    )
    result = []
    for site_id in ids:
        entries = []
        for day in calendar:
            total, successful = rollups.get(site_id, {}).get(day, (0, 0))
            count, seconds = downtime.get(site_id, {}).get(day, (0, 0))
            entries.append(
                DayUptime(
                    day=day,
                    uptime=round(successful * 100 / total, 3) if total else None,
                    checks=total,
                    incidents=count,
                    downtime_seconds=seconds,
                )
            )
        result.append(SiteDays(site_id=site_id, days=entries))
    return result


async def region_names(session: AsyncSession) -> dict[str, str]:
    names = dict((await session.execute(select(Region.code, Region.name))).tuples().all())
    names.setdefault(settings.worker_region, settings.worker_region_name)
    return names


async def site_responses(session: AsyncSession, sites: Sequence[Site]) -> list[SiteResponse]:
    if not sites:
        return []
    ids = [site.id for site in sites]
    now = datetime.now(UTC)
    uptime = {
        days: await uptime_many(session, ids, now - timedelta(days=days)) for days in (1, 7, 30)
    }
    recent_checks = (
        select(Check.checked_at, Check.response_ms)
        .where(Check.site_id == Site.id, Check.ok.is_(True))
        .order_by(Check.checked_at.desc())
        .limit(24)
        .lateral()
    )
    recent = await session.execute(
        select(Site.id, recent_checks.c.checked_at, recent_checks.c.response_ms)
        .join(recent_checks, true())
        .where(Site.id.in_(ids))
        .order_by(recent_checks.c.checked_at)
    )
    series: dict[int, list[SeriesPoint]] = {site_id: [] for site_id in ids}
    for site_id, checked_at, response_ms in recent:
        series[site_id].append(SeriesPoint(t=checked_at, ms=response_ms))
    bindings: dict[int, list[int]] = {site_id: [] for site_id in ids}
    for site_id, channel_id in await session.execute(
        select(SiteChannel.site_id, SiteChannel.channel_id).where(SiteChannel.site_id.in_(ids))
    ):
        bindings[site_id].append(channel_id)
    open_incidents: dict[tuple[int, str], int] = {}
    for incident_id, site_id, kind in await session.execute(
        select(Incident.id, Incident.site_id, Incident.kind).where(
            Incident.site_id.in_(ids), Incident.ended_at.is_(None)
        )
    ):
        open_incidents[(site_id, kind)] = incident_id
    states: dict[int, list[SiteRegionState]] = {}
    for state in await session.scalars(
        select(SiteRegionState).where(SiteRegionState.site_id.in_(ids))
    ):
        states.setdefault(state.site_id, []).append(state)
    names = await region_names(session) if states else {}
    responses = []
    for site in sites:
        values = {field: getattr(site, field) for field in FIELDS}
        values.update(
            {
                field: getattr(site, field)
                for field in (
                    "id",
                    "name",
                    "url",
                    "interval_seconds",
                    "is_public",
                    "show_url_publicly",
                    "status",
                    "consecutive_failures",
                    "last_checked_at",
                    "next_check_at",
                    "ssl_expires_at",
                    "ssl_checked_at",
                    "ssl_details",
                    "domain_expires_at",
                    "dns_values",
                    "heartbeat_prefix",
                    "last_ping_at",
                    "baseline_ms",
                    "created_at",
                    "pause_reason",
                )
            }
        )
        values.update(
            pause_message="Сайт приостановлен из-за ограничения тарифа"
            if site.pause_reason and site.pause_reason.startswith("plan_")
            else ("Остановлен пользователем" if site.paused else None),
            maintenance_active=in_maintenance(site, now),
            public_slug=str(site.public_slug),
            uptime_24h=uptime[1][site.id],
            uptime_7d=uptime[7][site.id],
            uptime_30d=uptime[30][site.id],
            latest_response_ms=series[site.id][-1].ms if series[site.id] else None,
            response_series=series[site.id],
            channel_ids=bindings[site.id],
            degraded=(site.id, "degraded") in open_incidents,
            open_incident_id=open_incidents.get((site.id, "down")),
            region_states=[
                RegionState(
                    region=state.region,
                    name=names.get(state.region, state.region),
                    status=state.status,
                    consecutive_failures=state.consecutive_failures,
                    last_checked_at=state.last_checked_at,
                    last_response_ms=state.last_response_ms,
                    last_error=state.last_error,
                )
                for state in sorted(states.get(site.id, []), key=lambda item: item.region)
                if state.region in site.regions
            ],
            headers=dict.fromkeys(decrypt_config(site.headers_encrypted), "********")
            if site.headers_encrypted
            else {},
        )
        if in_maintenance(site, now):
            values["status"] = SiteStatus.unknown
        responses.append(SiteResponse.model_validate(values))
    return responses
