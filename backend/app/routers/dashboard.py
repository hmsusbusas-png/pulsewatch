from fastapi import APIRouter
from sqlalchemy import func, select

from app.deps import CurrentUser, SessionDep
from app.models import Incident, Site, SiteStatus
from app.schemas import DashboardSiteSummary, DashboardSummary
from app.services.site_views import site_responses

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

SPARKLINE_POINTS = 24


@router.get("/summary", response_model=DashboardSummary)
async def read_summary(user: CurrentUser, session: SessionDep) -> DashboardSummary:
    sites = (
        await session.scalars(select(Site).where(Site.user_id == user.id).order_by(Site.name))
    ).all()

    summaries: list[DashboardSiteSummary] = []
    response_samples: list[int] = []
    views = await site_responses(session, sites)
    for site in views:
        passive = site.monitor_type in {"heartbeat", "ssl-only"}
        if site.latest_response_ms is not None and not passive:
            response_samples.append(site.latest_response_ms)
        summaries.append(
            DashboardSiteSummary(
                id=site.id,
                name=site.name,
                url=site.url,
                status=site.status,
                response_ms=site.latest_response_ms,
                uptime_24h=site.uptime_24h,
                last_checked_at=site.last_checked_at,
                sparkline=[point.ms for point in site.response_series],
            )
        )

    open_incidents = await session.scalar(
        select(func.count(Incident.id))
        .join(Site, Site.id == Incident.site_id)
        .where(Site.user_id == user.id, Incident.ended_at.is_(None))
    )
    up_count = sum(1 for site in views if site.status is SiteStatus.up)
    down_count = sum(1 for site in views if site.status is SiteStatus.down)
    unknown_count = sum(1 for site in views if site.status is SiteStatus.unknown)
    average = int(sum(response_samples) / len(response_samples)) if response_samples else None

    return DashboardSummary(
        degraded_count=sum(1 for site in views if site.degraded),
        paused_count=sum(1 for site in views if site.paused),
        total_sites=len(sites),
        up_count=up_count,
        down_count=down_count,
        unknown_count=unknown_count,
        average_response_ms=average,
        open_incidents=open_incidents or 0,
        sites=summaries,
    )
