from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LATENCY_BUCKETS, Check, DailyUptime, Incident, Site
from app.schemas import (
    SiteStats,
    SlaReport,
    SlaRow,
    SloReport,
    Span,
    StatsPoint,
    StatsRange,
    TimingBreakdown,
)
from app.services.monitors import maintenance_spans, merge_spans, overlap_seconds

RANGES: dict[str, tuple[timedelta, int]] = {
    "24h": (timedelta(hours=24), 300),
    "7d": (timedelta(days=7), 3600),
    "30d": (timedelta(days=30), 86400),
    "90d": (timedelta(days=90), 86400),
}
SLO_WINDOW_DAYS = 30
MONTH_SECONDS = 30 * 86400


def histogram_percentile(counts: Sequence[int], quantile: float) -> int | None:
    total = sum(counts)
    if not total:
        return None
    rank = quantile * total
    cumulative = 0
    for index, count in enumerate(counts):
        if count and cumulative + count >= rank:
            low = LATENCY_BUCKETS[index]
            high = (
                LATENCY_BUCKETS[index + 1] if index + 1 < len(LATENCY_BUCKETS) else int(low * 1.5)
            )
            return round(low + (high - low) * (rank - cumulative) / count)
        cumulative += count
    return LATENCY_BUCKETS[-1]


def add_histograms(rows: Sequence[Sequence[int] | None]) -> list[int]:
    total = [0] * len(LATENCY_BUCKETS)
    for row in rows:
        for index, value in enumerate((row or [])[: len(total)]):
            total[index] += value or 0
    return total


async def incident_spans(
    session: AsyncSession, site_ids: list[int], start: datetime, end: datetime
) -> list[tuple[int, int, str, datetime, datetime | None]]:
    rows = await session.execute(
        select(Incident.id, Incident.site_id, Incident.kind, Incident.started_at, Incident.ended_at)
        .where(
            Incident.site_id.in_(site_ids),
            Incident.started_at < end,
            (Incident.ended_at.is_(None)) | (Incident.ended_at > start),
        )
        .order_by(Incident.started_at)
    )
    return [(row[0], row[1], row[2], row[3], row[4]) for row in rows.all()]


def downtime_seconds(
    spans: list[tuple[datetime, datetime]], maintenance: list[tuple[datetime, datetime]]
) -> int:
    merged = merge_spans(spans)
    total = sum((finish - begin).total_seconds() for begin, finish in merged)
    return max(0, int(total - overlap_seconds(merged, maintenance)))


def _clip(
    begin: datetime, finish: datetime | None, start: datetime, end: datetime
) -> tuple[datetime, datetime]:
    return max(begin, start), min(finish or end, end)


async def _raw_points(
    session: AsyncSession, site_id: int, start: datetime, step: int
) -> list[StatsPoint]:
    rows = await session.execute(
        text(
            """
            SELECT to_timestamp(floor(extract(epoch FROM checked_at) / :step) * :step) AS bucket,
                   count(*) AS checks,
                   count(*) FILTER (WHERE NOT ok) AS failures,
                   round(avg(response_ms) FILTER (WHERE ok)) AS avg_ms,
                   min(response_ms) FILTER (WHERE ok) AS min_ms,
                   max(response_ms) FILTER (WHERE ok) AS max_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY response_ms)
                       FILTER (WHERE ok AND response_ms IS NOT NULL) AS p95_ms
            FROM checks
            WHERE site_id = :site AND checked_at >= :start
            GROUP BY bucket ORDER BY bucket
            """
        ),
        {"site": site_id, "start": start, "step": step},
    )
    return [
        StatsPoint(
            t=row.bucket,
            avg_ms=int(row.avg_ms) if row.avg_ms is not None else None,
            p95_ms=round(row.p95_ms) if row.p95_ms is not None else None,
            min_ms=row.min_ms,
            max_ms=row.max_ms,
            checks=row.checks,
            failures=row.failures,
        )
        for row in rows
    ]


async def site_stats(session: AsyncSession, site: Site, range_key: StatsRange) -> SiteStats:
    span, step = RANGES[range_key]
    source: Literal["checks", "daily_rollups"]
    end = datetime.now(UTC)
    start = end - span
    timings_row = (
        await session.execute(
            text(
                """
                SELECT round(avg(dns_ms)) AS dns_ms, round(avg(connect_ms)) AS connect_ms,
                       round(avg(tls_ms)) AS tls_ms, round(avg(ttfb_ms)) AS ttfb_ms,
                       round(avg(transfer_ms)) AS transfer_ms,
                       count(*) FILTER (WHERE ttfb_ms IS NOT NULL OR dns_ms IS NOT NULL) AS samples
                FROM checks WHERE site_id = :site AND checked_at >= :start AND ok
                """
            ),
            {"site": site.id, "start": max(start, end - timedelta(days=7))},
        )
    ).one()
    timings = TimingBreakdown(
        **{
            name: int(getattr(timings_row, name))
            if getattr(timings_row, name) is not None
            else None
            for name in ("dns_ms", "connect_ms", "tls_ms", "ttfb_ms", "transfer_ms")
        },
        samples=timings_row.samples or 0,
    )
    if step < 86400:
        points = await _raw_points(session, site.id, start, step)
        summary = (
            await session.execute(
                text(
                    """
                    SELECT count(*) AS checks, count(*) FILTER (WHERE NOT ok) AS failures,
                           round(avg(response_ms) FILTER (WHERE ok)) AS avg_ms,
                           percentile_cont(ARRAY[0.5, 0.95, 0.99]) WITHIN GROUP
                               (ORDER BY response_ms) FILTER (WHERE ok AND response_ms IS NOT NULL)
                               AS quantiles
                    FROM checks WHERE site_id = :site AND checked_at >= :start
                    """
                ),
                {"site": site.id, "start": start},
            )
        ).one()
        checks, failures = summary.checks or 0, summary.failures or 0
        average = int(summary.avg_ms) if summary.avg_ms is not None else None
        quantiles = [round(value) for value in summary.quantiles] if summary.quantiles else None
        source = "checks"
    else:
        rows = (
            await session.execute(
                select(
                    DailyUptime.day,
                    DailyUptime.total,
                    DailyUptime.successful,
                    DailyUptime.response_ms_sum,
                    DailyUptime.histogram,
                )
                .where(DailyUptime.site_id == site.id, DailyUptime.day >= start.date())
                .order_by(DailyUptime.day)
            )
        ).all()
        points = [
            StatsPoint(
                t=datetime(row.day.year, row.day.month, row.day.day, tzinfo=UTC),
                avg_ms=round(row.response_ms_sum / row.successful) if row.successful else None,
                p95_ms=histogram_percentile(row.histogram or [], 0.95),
                min_ms=None,
                max_ms=None,
                checks=row.total,
                failures=row.total - row.successful,
            )
            for row in rows
        ]
        checks = sum(row.total for row in rows)
        successful = sum(row.successful for row in rows)
        failures = checks - successful
        latency_sum = sum(row.response_ms_sum for row in rows)
        average = round(latency_sum / successful) if successful else None
        combined = add_histograms([row.histogram for row in rows])
        quantiles = (
            [histogram_percentile(combined, q) or 0 for q in (0.5, 0.95, 0.99)]
            if sum(combined)
            else None
        )
        source = "daily_rollups"
    incidents = await incident_spans(session, [site.id], start, end)
    maintenance = maintenance_spans(site.maintenance, start, end)
    down = [
        _clip(begin, finish, start, end)
        for _, _, kind, begin, finish in incidents
        if kind == "down"
    ]
    return SiteStats(
        range=range_key,
        start=start,
        end=end,
        resolution_seconds=step,
        source=source,
        uptime=round((checks - failures) * 100 / checks, 3) if checks else None,
        checks=checks,
        failures=failures,
        avg_ms=average,
        p50_ms=quantiles[0] if quantiles else None,
        p95_ms=quantiles[1] if quantiles else None,
        p99_ms=quantiles[2] if quantiles else None,
        incidents=sum(1 for _, _, kind, begin, _ in incidents if kind == "down" and begin >= start),
        downtime_seconds=downtime_seconds(down, maintenance),
        baseline_ms=site.baseline_ms,
        timings=timings,
        points=points,
        incident_spans=[
            Span(start=begin, end=finish, kind=kind, incident_id=incident_id)
            for incident_id, _, kind, begin, finish in incidents
        ],
        maintenance_spans=[
            Span(start=begin, end=finish, kind="maintenance") for begin, finish in maintenance
        ],
    )


async def _has_checks(session: AsyncSession, site_id: int, since: datetime) -> bool:
    found = await session.scalar(
        select(Check.id).where(Check.site_id == site_id, Check.checked_at >= since).limit(1)
    )
    if found is not None:
        return True
    return (
        await session.scalar(
            select(DailyUptime.day)
            .where(DailyUptime.site_id == site_id, DailyUptime.day >= since.date())
            .limit(1)
        )
    ) is not None


async def slo_report(session: AsyncSession, site: Site) -> SloReport:
    now = datetime.now(UTC)
    start = now - timedelta(days=SLO_WINDOW_DAYS)
    maintenance = maintenance_spans(site.maintenance, start, now)
    maintenance_total = int(sum((finish - begin).total_seconds() for begin, finish in maintenance))
    incidents = await incident_spans(session, [site.id], start, now)
    down = [
        _clip(begin, finish, start, now)
        for _, _, kind, begin, finish in incidents
        if kind == "down"
    ]
    window = max(1, SLO_WINDOW_DAYS * 86400 - maintenance_total)
    spent = downtime_seconds(down, maintenance)
    measured = await _has_checks(session, site.id, start)
    target = site.slo_target
    error_share = (1 - target / 100) if target is not None else None
    budget = round(window * error_share) if error_share is not None else 0

    def burn(hours: int) -> float | None:
        if error_share is None or not measured:
            return None
        since = now - timedelta(hours=hours)
        recent = [_clip(begin, finish, since, now) for begin, finish in down if finish > since]
        recent_maintenance = [
            _clip(begin, finish, since, now) for begin, finish in maintenance if finish > since
        ]
        share = downtime_seconds(recent, recent_maintenance) / (hours * 3600)
        return round(share / error_share, 2)

    burn_24h = burn(24)
    remaining = budget - spent
    exhausted_at = None
    if error_share is not None and measured and remaining > 0 and burn_24h:
        spend_per_second = burn_24h * error_share
        exhausted_at = now + timedelta(seconds=remaining / spend_per_second)
    return SloReport(
        site_id=site.id,
        target=target,
        window_days=SLO_WINDOW_DAYS,
        uptime=round((window - spent) * 100 / window, 4) if measured else None,
        budget_seconds=budget,
        spent_seconds=spent,
        remaining_seconds=remaining,
        remaining_percent=round(max(remaining, 0) * 100 / budget, 2) if budget else None,
        burn_rate_1h=burn(1),
        burn_rate_24h=burn_24h,
        exhausted=error_share is not None and measured and remaining <= 0,
        exhausted_at=exhausted_at,
        maintenance_seconds=maintenance_total,
        allowed_downtime_month_seconds=round(MONTH_SECONDS * error_share) if error_share else 0,
    )


async def sla_report(
    session: AsyncSession, sites: Sequence[Site], start: datetime, end: datetime
) -> SlaReport:
    incidents = await incident_spans(session, [site.id for site in sites], start, end)
    rows = []
    for site in sites:
        maintenance = maintenance_spans(site.maintenance, start, end)
        maintenance_total = int(
            sum((finish - begin).total_seconds() for begin, finish in maintenance)
        )
        down = [
            _clip(begin, finish, start, end)
            for _, site_id, kind, begin, finish in incidents
            if site_id == site.id and kind == "down"
        ]
        window = max(1, int((end - start).total_seconds()) - maintenance_total)
        spent = downtime_seconds(down, maintenance)
        measured = await _has_checks(session, site.id, start)
        uptime = round((window - spent) * 100 / window, 4) if measured else None
        rows.append(
            SlaRow(
                site_id=site.id,
                name=site.name,
                target=site.slo_target,
                uptime=uptime,
                downtime_seconds=spent,
                maintenance_seconds=maintenance_total,
                incidents=sum(
                    1
                    for _, site_id, kind, begin, _ in incidents
                    if site_id == site.id and kind == "down" and begin >= start
                ),
                met=uptime >= site.slo_target
                if uptime is not None and site.slo_target is not None
                else None,
            )
        )
    return SlaReport(start=start, end=end, sites=rows)
