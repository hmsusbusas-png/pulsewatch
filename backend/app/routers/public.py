import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import select

from app.deps import SessionDep
from app.errors import ApiError
from app.models import Incident, Site
from app.schemas import PublicIncidentResponse, PublicStatusResponse
from app.services.monitoring import calculate_uptime

router = APIRouter(prefix="/status", tags=["public"])

UPTIME_WINDOW_HOURS = 720
MAX_PUBLIC_INCIDENTS = 10


@router.get("/{slug}", response_model=PublicStatusResponse)
async def read_public_status(slug: str, session: SessionDep) -> PublicStatusResponse:
    try:
        site_uuid = uuid.UUID(slug)
    except ValueError as exc:
        raise ApiError(404, "status_page_not_found", "Status page does not exist") from exc
    site = await session.scalar(
        select(Site).where(Site.public_slug == site_uuid, Site.is_public.is_(True))
    )
    if site is None:
        raise ApiError(404, "status_page_not_found", "Status page does not exist")

    now = datetime.now(UTC)
    uptime = await calculate_uptime(session, site.id, now - timedelta(hours=UPTIME_WINDOW_HOURS))
    incidents = (
        await session.scalars(
            select(Incident)
            .where(Incident.site_id == site.id)
            .order_by(Incident.started_at.desc())
            .limit(MAX_PUBLIC_INCIDENTS)
        )
    ).all()

    return PublicStatusResponse(
        name=site.name,
        url=site.url if site.show_url_publicly else None,
        status=site.status,
        uptime_30d=uptime,
        last_checked_at=site.last_checked_at,
        incidents=[
            PublicIncidentResponse(
                started_at=incident.started_at,
                ended_at=incident.ended_at,
                duration_seconds=int(
                    ((incident.ended_at or now) - incident.started_at).total_seconds()
                ),
                reason=incident.reason,
            )
            for incident in incidents
        ],
        updated_at=now,
    )
