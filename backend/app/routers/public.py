import secrets
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from html import escape
from typing import Literal
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select

from app.config import settings
from app.crypto import encrypt_text
from app.deps import SessionDep
from app.errors import ApiError, ErrorResponse, Responses
from app.models import (
    Incident,
    IncidentNote,
    MailJob,
    Site,
    SiteStatus,
    StatusAnnouncement,
    StatusPage,
    StatusPageSite,
    StatusSubscriber,
)
from app.schemas import DayUptime, PublicIncidentResponse, PublicStatusResponse
from app.security import hash_refresh_token, link_claims, signed_link
from app.services.monitoring import calculate_uptime
from app.services.monitors import in_maintenance
from app.services.notifications import frontend_link
from app.services.site_views import daily_uptime, uptime_many
from app.validation import RequestModel

router = APIRouter(tags=["public"])

UPTIME_WINDOW_HOURS = 720
MAX_PUBLIC_INCIDENTS = 10
OVERALL_LABELS = {
    "operational": "Все системы работают",
    "degraded": "Снижена производительность",
    "partial_outage": "Частичный сбой",
    "major_outage": "Серьёзный сбой",
    "maintenance": "Плановые работы",
}
PAGE_NOT_FOUND: Responses = {
    404: {"model": ErrorResponse, "description": "Статус-страница не найдена"}
}


class SubscribeRequest(RequestModel):
    email: EmailStr


class TokenRequest(RequestModel):
    token: str = Field(min_length=20, max_length=2048)


class PublicNote(BaseModel):
    status: str
    text: str
    created_at: datetime


class PublicPageIncident(BaseModel):
    id: int
    site_id: int
    site_name: str
    kind: str
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int
    notes: list[PublicNote]


class PublicPageSite(BaseModel):
    id: int
    name: str
    status: SiteStatus
    degraded: bool
    maintenance: bool
    url: str | None
    uptime_30d: float | None
    uptime_90d: float | None
    days: list[DayUptime]


class PublicAnnouncement(BaseModel):
    id: int
    title: str
    body: str
    pinned: bool
    created_at: datetime


OverallCode = Literal["operational", "degraded", "partial_outage", "major_outage", "maintenance"]


class Overall(BaseModel):
    status: OverallCode
    label: str


class PublicPageResponse(BaseModel):
    slug: str
    title: str
    description: str
    logo_url: str | None
    accent: str
    overall: Overall
    announcements: list[PublicAnnouncement]
    sites: list[PublicPageSite]
    incidents: list[PublicPageIncident]
    updated_at: datetime


@router.get("/status/{slug}", response_model=PublicStatusResponse, responses=PAGE_NOT_FOUND)
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
            .where(Incident.site_id == site.id, Incident.kind == "down")
            .order_by(Incident.started_at.desc())
            .limit(MAX_PUBLIC_INCIDENTS)
        )
    ).all()
    return PublicStatusResponse(
        name=escape(site.name),
        url=site.url if site.show_url_publicly else None,
        status=SiteStatus.unknown if in_maintenance(site, now) else site.status,
        uptime_30d=uptime,
        last_checked_at=site.last_checked_at,
        incidents=[
            PublicIncidentResponse(
                started_at=incident.started_at,
                ended_at=incident.ended_at,
                duration_seconds=int(
                    ((incident.ended_at or now) - incident.started_at).total_seconds()
                ),
                reason=escape(incident.reason)
                if site.show_url_publicly
                else "Проверка недоступности",
            )
            for incident in incidents
        ],
        updated_at=now,
    )


async def page_by_slug(session: SessionDep, slug: str) -> StatusPage:
    page = await session.scalar(select(StatusPage).where(StatusPage.slug == slug))
    if page is None:
        raise ApiError(404, "page_not_found", "Статус-страница не найдена")
    return page


async def page_members(session: SessionDep, page: StatusPage) -> list[Site]:
    return list(
        await session.scalars(
            select(Site)
            .join(StatusPageSite)
            .where(StatusPageSite.page_id == page.id)
            .order_by(Site.name)
        )
    )


def overall_status(sites: list[PublicPageSite]) -> Overall:
    down = sum(1 for site in sites if site.status is SiteStatus.down)
    code: OverallCode
    if sites and down == len(sites):
        code = "major_outage"
    elif down:
        code = "partial_outage"
    elif any(site.degraded for site in sites):
        code = "degraded"
    elif any(site.maintenance for site in sites):
        code = "maintenance"
    else:
        code = "operational"
    return Overall(status=code, label=OVERALL_LABELS[code])


@router.get(
    "/public/pages/{slug}",
    response_model=PublicPageResponse,
    summary="Публичная статус-страница",
    description="Суточный аптайм за 90 дней по каждому монитору, общий статус, "
    "закреплённые объявления и инциденты с заметками. Причины сбоев не раскрываются.",
    responses=PAGE_NOT_FOUND,
)
async def public_page(slug: str, session: SessionDep) -> PublicPageResponse:
    page = await page_by_slug(session, slug)
    sites = await page_members(session, page)
    ids = [site.id for site in sites]
    now = datetime.now(UTC)
    incidents = list(
        await session.scalars(
            select(Incident)
            .where(Incident.site_id.in_(ids), Incident.started_at >= now - timedelta(days=90))
            .order_by(Incident.started_at.desc())
            .limit(200)
        )
    )
    notes = list(
        await session.scalars(
            select(IncidentNote)
            .where(IncidentNote.incident_id.in_([i.id for i in incidents]))
            .order_by(IncidentNote.id)
        )
    )
    uptime_30 = await uptime_many(session, ids, now - timedelta(days=30))
    uptime_90 = await uptime_many(session, ids, now - timedelta(days=90))
    days = (
        {entry.site_id: entry.days for entry in await daily_uptime(session, ids, 90)} if ids else {}
    )
    open_degraded = {i.site_id for i in incidents if i.kind == "degraded" and i.ended_at is None}
    names = {site.id: site.name for site in sites}
    members = [
        PublicPageSite(
            id=site.id,
            name=escape(site.name),
            status=SiteStatus.unknown if in_maintenance(site, now) else site.status,
            degraded=site.id in open_degraded,
            maintenance=in_maintenance(site, now),
            url=site.url if page.show_urls and site.monitor_type != "heartbeat" else None,
            uptime_30d=uptime_30.get(site.id),
            uptime_90d=uptime_90.get(site.id),
            days=days.get(site.id, []),
        )
        for site in sites
    ]
    pinned = await session.scalars(
        select(StatusAnnouncement)
        .where(StatusAnnouncement.page_id == page.id, StatusAnnouncement.pinned.is_(True))
        .order_by(StatusAnnouncement.created_at.desc())
        .limit(5)
    )
    return PublicPageResponse(
        slug=page.slug,
        title=escape(page.title),
        description=escape(page.description),
        logo_url=page.logo_url,
        accent=page.accent,
        overall=overall_status(members),
        announcements=[
            PublicAnnouncement(
                id=row.id,
                title=escape(row.title),
                body=escape(row.body),
                pinned=row.pinned,
                created_at=row.created_at,
            )
            for row in pinned
        ],
        sites=members,
        incidents=[
            PublicPageIncident(
                id=incident.id,
                site_id=incident.site_id,
                site_name=escape(names.get(incident.site_id, "")),
                kind=incident.kind,
                started_at=incident.started_at,
                ended_at=incident.ended_at,
                duration_seconds=int(
                    ((incident.ended_at or now) - incident.started_at).total_seconds()
                ),
                notes=[
                    PublicNote(
                        status=note.status, text=escape(note.text), created_at=note.created_at
                    )
                    for note in notes
                    if note.incident_id == incident.id
                ],
            )
            for incident in incidents
        ],
        updated_at=now,
    )


@router.get(
    "/public/pages/{slug}/feed.xml",
    response_class=Response,
    summary="RSS инцидентов",
    responses={200: {"content": {"application/rss+xml": {}}}, **PAGE_NOT_FOUND},
)
async def page_feed(slug: str, session: SessionDep) -> Response:
    page = await page_by_slug(session, slug)
    sites = {site.id: site.name for site in await page_members(session, page)}
    incidents = list(
        await session.scalars(
            select(Incident)
            .where(Incident.site_id.in_(list(sites)))
            .order_by(Incident.started_at.desc())
            .limit(50)
        )
    )
    notes: dict[int, list[IncidentNote]] = {}
    for note in await session.scalars(
        select(IncidentNote)
        .where(IncidentNote.incident_id.in_([i.id for i in incidents]))
        .order_by(IncidentNote.id)
    ):
        notes.setdefault(note.incident_id, []).append(note)
    link = f"{settings.frontend_base_url.rstrip('/')}/public/{page.slug}"
    items = []
    for incident in incidents:
        label = "снижение производительности" if incident.kind == "degraded" else "недоступность"
        state = "решён" if incident.ended_at else "продолжается"
        updates = "\n".join(
            f"[{note.status}] {note.text}" for note in notes.get(incident.id, [])
        ) or ("Инцидент закрыт." if incident.ended_at else "Идёт расследование.")
        items.append(
            "<item>"
            f"<title>{xml_escape(sites.get(incident.site_id, ''))}: {label} ({state})</title>"
            f"<link>{xml_escape(link)}#incident-{incident.id}</link>"
            f'<guid isPermaLink="false">pulsewatch-incident-{incident.id}</guid>'
            f"<pubDate>{format_datetime(incident.started_at)}</pubDate>"
            f"<description>{xml_escape(updates)}</description>"
            "</item>"
        )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        f"<title>{xml_escape(page.title)}: инциденты</title>"
        f"<link>{xml_escape(link)}</link>"
        f"<description>{xml_escape(page.description or page.title)}</description>"
        "<language>ru</language>"
        f"<lastBuildDate>{format_datetime(datetime.now(UTC))}</lastBuildDate>"
        f"{''.join(items)}</channel></rss>"
    )
    return Response(
        document,
        media_type="application/rss+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.post(
    "/public/pages/{slug}/subscribe",
    status_code=202,
    summary="Подписаться на обновления",
    description="Письмо со ссылкой на страницу фронтенда /public/subscribers/confirm.",
    responses=PAGE_NOT_FOUND,
)
async def subscribe(slug: str, payload: SubscribeRequest, session: SessionDep) -> dict[str, bool]:
    page = await page_by_slug(session, slug)
    address = payload.email.lower()
    subscriber = await session.scalar(
        select(StatusSubscriber)
        .where(StatusSubscriber.page_id == page.id, StatusSubscriber.email == address)
        .with_for_update()
    )
    if subscriber is None:
        subscriber = StatusSubscriber(page_id=page.id, email=address)
        session.add(subscriber)
    token = signed_link("status_confirm", {"nonce": secrets.token_urlsafe(32)}, 3600)
    subscriber.token_hash = hash_refresh_token(token)
    subscriber.expires_at = datetime.now(UTC) + timedelta(hours=1)
    session.add(
        MailJob(
            user_id=page.user_id,
            recipient=address,
            subject=f"Подписка: {page.title}",
            body=encrypt_text(
                f"Подтвердите подписку: {frontend_link('/public/subscribers/confirm', token)}"
            ),
        )
    )
    await session.commit()
    return {"sent": True}


@router.post(
    "/public/subscribers/confirm",
    summary="Подтвердить подписку",
    description="Вызывается страницей фронтенда по ссылке из письма.",
    responses={400: {"model": ErrorResponse}},
)
async def confirm_subscriber(payload: TokenRequest, session: SessionDep) -> dict[str, bool]:
    token = payload.token
    if link_claims(token, "status_confirm") is None:
        raise ApiError(400, "invalid_token", "Ссылка недействительна")
    row = await session.scalar(
        select(StatusSubscriber)
        .where(StatusSubscriber.token_hash == hash_refresh_token(token))
        .with_for_update()
    )
    if row is None or row.expires_at is None or row.expires_at <= datetime.now(UTC):
        raise ApiError(400, "invalid_token", "Ссылка недействительна или истекла")
    row.confirmed = True
    row.token_hash = None
    await session.commit()
    return {"subscribed": True}


@router.post(
    "/public/subscribers/unsubscribe",
    summary="Отписаться",
    responses={400: {"model": ErrorResponse}},
)
async def unsubscribe(payload: TokenRequest, session: SessionDep) -> dict[str, bool]:
    claims = link_claims(payload.token, "status_unsubscribe")
    if claims is None:
        raise ApiError(400, "invalid_token", "Ссылка недействительна")
    subscriber_id = int(str(claims["subscriber"]))
    await session.execute(delete(StatusSubscriber).where(StatusSubscriber.id == subscriber_id))
    await session.commit()
    return {"unsubscribed": True}


BADGE_COLORS = (
    (99.9, "#10b981"),
    (99.0, "#eab308"),
    (95.0, "#f97316"),
    (0.0, "#ef4444"),
)
CHAR_WIDTHS = {" ": 3.6, ".": 3.6, ",": 3.6, ":": 3.6, "%": 9.3, "1": 7.0, "i": 3.1, "l": 3.1}


def text_width(value: str) -> int:
    return round(sum(CHAR_WIDTHS.get(char, 7.0 if char.isdigit() else 6.6) for char in value)) + 10


def badge_svg(label: str, value: str, color: str) -> str:
    left, right = text_width(label), text_width(value)
    width = left + right
    safe_label, safe_value = xml_escape(label), xml_escape(value)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20" role="img" '
        f'aria-label="{safe_label}: {safe_value}"><title>{safe_label}: {safe_value}</title>'
        '<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" '
        'stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<clipPath id="r"><rect width="{width}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)"><rect width="{left}" height="20" fill="#27272a"/>'
        f'<rect x="{left}" width="{right}" height="20" fill="{color}"/>'
        f'<rect width="{width}" height="20" fill="url(#s)"/></g>'
        '<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" '
        'font-size="11">'
        f'<text x="{left / 2}" y="14">{safe_label}</text>'
        f'<text x="{left + right / 2}" y="14">{safe_value}</text></g></svg>'
    )


@router.get(
    "/badge/{slug}.svg",
    response_class=Response,
    summary="SVG-бейдж аптайма",
    description="Аптайм мониторов статус-страницы за период. Кешируется на 5 минут.",
    responses={200: {"content": {"image/svg+xml": {}}}, **PAGE_NOT_FOUND},
)
async def badge(
    slug: str,
    session: SessionDep,
    period: Literal["24h", "7d", "30d", "90d"] = Query("30d"),
    label: str = Query("uptime", max_length=30, pattern=r"^[\w .:-]+$"),
) -> Response:
    page = await page_by_slug(session, slug)
    ids = [site.id for site in await page_members(session, page)]
    hours = {"24h": 24, "7d": 168, "30d": 720, "90d": 2160}[period]
    values = (
        await uptime_many(session, ids, datetime.now(UTC) - timedelta(hours=hours)) if ids else {}
    )
    measured = [value for value in values.values() if value is not None]
    if measured:
        uptime = sum(measured) / len(measured)
        value = f"{uptime:.2f}%".replace(".00%", "%")
        color = next(color for threshold, color in BADGE_COLORS if uptime >= threshold)
    else:
        value, color = "нет данных", "#71717a"
    return Response(
        badge_svg(label, value, color),
        media_type="image/svg+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )
