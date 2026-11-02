from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import delete, func, select

from app.config import settings
from app.deps import CurrentUser, SessionDep
from app.errors import ApiError
from app.models import Check, Incident, Site, SiteStatus, UserTariff
from app.schemas import (
    CheckResponse,
    IncidentResponse,
    SeriesPoint,
    SiteCreateRequest,
    SiteResponse,
    SiteUpdateRequest,
)
from app.services.monitoring import calculate_uptime
from app.ssrf import ensure_target_allowed, normalize_url

router = APIRouter(prefix="/sites", tags=["sites"])

SPARKLINE_POINTS = 24
UPTIME_WINDOWS = {"24h": 24, "7d": 168, "30d": 720}


async def load_owned_site(session: SessionDep, user_id: int, site_id: int) -> Site:
    site = await session.scalar(select(Site).where(Site.id == site_id, Site.user_id == user_id))
    if site is None:
        raise ApiError(404, "site_not_found", "Site does not exist")
    return site


async def _recent_series(
    session: SessionDep, site_id: int, points: int
) -> list[tuple[datetime, int | None]]:
    rows = (
        await session.execute(
            select(Check.checked_at, Check.response_ms)
            .where(Check.site_id == site_id, Check.ok.is_(True))
            .order_by(Check.checked_at.desc())
            .limit(points)
        )
    ).all()
    return [(row[0], row[1]) for row in reversed(rows)]


async def build_site_response(session: SessionDep, site: Site) -> SiteResponse:
    now = datetime.now(UTC)
    uptime_24h = await calculate_uptime(session, site.id, now - timedelta(hours=24))
    uptime_30d = await calculate_uptime(session, site.id, now - timedelta(hours=720))
    series = await _recent_series(session, site.id, SPARKLINE_POINTS)
    latest_ms = await session.scalar(
        select(Check.response_ms)
        .where(Check.site_id == site.id, Check.ok.is_(True))
        .order_by(Check.checked_at.desc())
        .limit(1)
    )
    return SiteResponse(
        id=site.id,
        name=site.name,
        url=site.url,
        interval_seconds=site.interval_seconds,
        is_public=site.is_public,
        show_url_publicly=site.show_url_publicly,
        public_slug=str(site.public_slug),
        status=site.status,
        consecutive_failures=site.consecutive_failures,
        last_checked_at=site.last_checked_at,
        ssl_expires_at=site.ssl_expires_at,
        ssl_checked_at=site.ssl_checked_at,
        created_at=site.created_at,
        uptime_24h=uptime_24h,
        uptime_30d=uptime_30d,
        latest_response_ms=latest_ms,
        response_series=[SeriesPoint(t=checked_at, ms=ms) for checked_at, ms in series],
    )


async def _enforce_tariff_limit(session: SessionDep, user: CurrentUser) -> None:
    if user.tariff is not UserTariff.free:
        return
    count = await session.scalar(select(func.count(Site.id)).where(Site.user_id == user.id))
    if (count or 0) >= settings.free_tariff_site_limit:
        raise ApiError(
            403,
            "tariff_limit_reached",
            f"Free tariff allows up to {settings.free_tariff_site_limit} sites. "
            "Upgrade to Pro to add more.",
        )


@router.get("", response_model=list[SiteResponse])
async def list_sites(user: CurrentUser, session: SessionDep) -> list[SiteResponse]:
    sites = (
        await session.scalars(select(Site).where(Site.user_id == user.id).order_by(Site.created_at))
    ).all()
    return [await build_site_response(session, site) for site in sites]


@router.post("", response_model=SiteResponse, status_code=201)
async def create_site(
    payload: SiteCreateRequest, user: CurrentUser, session: SessionDep
) -> SiteResponse:
    await _enforce_tariff_limit(session, user)
    url = normalize_url(payload.url)
    await ensure_target_allowed(url, settings.allow_private_targets)
    site = Site(
        user_id=user.id,
        name=payload.name,
        url=url,
        interval_seconds=payload.interval_seconds,
        is_public=payload.is_public,
        show_url_publicly=payload.show_url_publicly,
    )
    session.add(site)
    await session.commit()
    await session.refresh(site)
    return await build_site_response(session, site)


@router.get("/{site_id}", response_model=SiteResponse)
async def read_site(site_id: int, user: CurrentUser, session: SessionDep) -> SiteResponse:
    site = await load_owned_site(session, user.id, site_id)
    return await build_site_response(session, site)


@router.patch("/{site_id}", response_model=SiteResponse)
async def update_site(
    site_id: int, payload: SiteUpdateRequest, user: CurrentUser, session: SessionDep
) -> SiteResponse:
    site = await load_owned_site(session, user.id, site_id)
    if payload.url is not None:
        url = normalize_url(payload.url)
        if url != site.url:
            await ensure_target_allowed(url, settings.allow_private_targets)
            site.url = url
            site.ssl_expires_at = None
            site.ssl_checked_at = None
    if payload.name is not None:
        site.name = payload.name
    if payload.interval_seconds is not None:
        site.interval_seconds = payload.interval_seconds
    if payload.is_public is not None:
        site.is_public = payload.is_public
    if payload.show_url_publicly is not None:
        site.show_url_publicly = payload.show_url_publicly
    await session.commit()
    await session.refresh(site)
    return await build_site_response(session, site)


@router.delete("/{site_id}", status_code=204)
async def delete_site(site_id: int, user: CurrentUser, session: SessionDep) -> None:
    site = await load_owned_site(session, user.id, site_id)
    await session.execute(delete(Site).where(Site.id == site.id))
    await session.commit()


@router.get("/{site_id}/checks", response_model=list[CheckResponse])
async def list_checks(
    site_id: int,
    user: CurrentUser,
    session: SessionDep,
    range_key: str = Query("24h", alias="range", pattern="^(24h|7d)$"),
) -> list[CheckResponse]:
    site = await load_owned_site(session, user.id, site_id)
    hours = UPTIME_WINDOWS[range_key]
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = (
        await session.execute(
            select(Check)
            .where(Check.site_id == site.id, Check.checked_at >= since)
            .order_by(Check.checked_at)
        )
    ).scalars()
    return [
        CheckResponse(
            checked_at=check.checked_at,
            status_code=check.status_code,
            response_ms=check.response_ms,
            ok=check.ok,
            error=check.error,
        )
        for check in rows
    ]


@router.get("/{site_id}/incidents", response_model=list[IncidentResponse])
async def list_incidents(
    site_id: int, user: CurrentUser, session: SessionDep
) -> list[IncidentResponse]:
    site = await load_owned_site(session, user.id, site_id)
    incidents = (
        await session.scalars(
            select(Incident)
            .where(Incident.site_id == site.id)
            .order_by(Incident.started_at.desc())
            .limit(100)
        )
    ).all()
    now = datetime.now(UTC)
    return [
        IncidentResponse(
            id=incident.id,
            started_at=incident.started_at,
            ended_at=incident.ended_at,
            duration_seconds=int(
                ((incident.ended_at or now) - incident.started_at).total_seconds()
            ),
            reason=incident.reason,
            is_open=incident.ended_at is None,
        )
        for incident in incidents
    ]


def _status_counts(sites: list[Site]) -> tuple[int, int, int]:
    up = sum(1 for site in sites if site.status is SiteStatus.up)
    down = sum(1 for site in sites if site.status is SiteStatus.down)
    unknown = sum(1 for site in sites if site.status is SiteStatus.unknown)
    return up, down, unknown
