from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from app.deps import CurrentUser, SessionDep
from app.models import Check, Incident, Site, SiteStatus
from app.schemas import DashboardSiteSummary, DashboardSummary
from app.services.monitoring import calculate_uptime

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

SPARKLINE_POINTS = 24


@router.get("/summary", response_model=DashboardSummary)
async def read_summary(user: CurrentUser, session: SessionDep) -> DashboardSummary:
    now = datetime.now(UTC)
    since_24h = now - timedelta(hours=24)
    sites = (
        await session.scalars(select(Site).where(Site.user_id == user.id).order_by(Site.name))
    ).all()

    summaries: list[DashboardSiteSummary] = []
    response_samples: list[int] = []
    for site in sites:
        uptime = await calculate_uptime(session, site.id, since_24h)
        recent = (
            await session.execute(
                select(Check.response_ms, Check.ok)
                .where(Check.site_id == site.id, Check.checked_at >= since_24h)
                .order_by(Check.checked_at.desc())
                .limit(SPARKLINE_POINTS)
            )
        ).all()
        ordered = list(reversed(recent))
        sparkline = [row.response_ms if row.ok else None for row in ordered]
        latest_ms = next((row.response_ms for row in recent if row.ok), None)
        if latest_ms is not None:
            response_samples.append(latest_ms)
        summaries.append(
            DashboardSiteSummary(
                id=site.id,
                name=site.name,
                url=site.url,
                status=site.status,
                response_ms=latest_ms,
                uptime_24h=uptime,
                last_checked_at=site.last_checked_at,
                sparkline=sparkline,
            )
        )

    open_incidents = await session.scalar(
        select(func.count(Incident.id))
        .join(Site, Site.id == Incident.site_id)
        .where(Site.user_id == user.id, Incident.ended_at.is_(None))
    )
    up_count = sum(1 for site in sites if site.status is SiteStatus.up)
    down_count = sum(1 for site in sites if site.status is SiteStatus.down)
    unknown_count = sum(1 for site in sites if site.status is SiteStatus.unknown)
    average = int(sum(response_samples) / len(response_samples)) if response_samples else None

    return DashboardSummary(
        total_sites=len(sites),
        up_count=up_count,
        down_count=down_count,
        unknown_count=unknown_count,
        average_response_ms=average,
        open_incidents=open_incidents or 0,
        sites=summaries,
    )
