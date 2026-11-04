import re
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select

from app.config import settings
from app.deps import CurrentUser, SessionDep
from app.errors import ApiError, ErrorResponse, Responses
from app.models import (
    PAGE_ACCENTS,
    Site,
    StatusAnnouncement,
    StatusPage,
    StatusPageSite,
    StatusSubscriber,
)
from app.pagination import PAGINATION_HEADERS, paginate
from app.services import audit
from app.services.billing import current_plan, require_feature
from app.validation import RequestModel

router = APIRouter(tags=["status pages"])
Accent = Literal["emerald", "sky", "violet", "amber", "rose", "slate"]
DOMAIN = re.compile(r"^(?=.{4,253}$)(?!-)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
NOT_FOUND: Responses = {404: {"model": ErrorResponse, "description": "Статус-страница не найдена"}}


class PageCreate(RequestModel):
    title: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=2000)
    slug: str | None = Field(
        default=None, min_length=3, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]+[a-z0-9]$"
    )
    site_ids: list[int] = Field(min_length=1, max_length=100)
    show_urls: bool = False
    logo_url: str | None = Field(default=None, max_length=2048)
    accent: Accent = "emerald"
    custom_domain: str | None = Field(default=None, max_length=253)

    @field_validator("logo_url")
    @classmethod
    def https_logo(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if not re.fullmatch(r"https://[^\s\"'<>]+", value.strip()):
            raise ValueError("Логотип: адрес https:// без пробелов и кавычек")
        return value.strip()

    @field_validator("custom_domain")
    @classmethod
    def plain_domain(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        domain = value.strip().lower().rstrip(".")
        if not DOMAIN.match(domain):
            raise ValueError("Домен: например status.example.com")
        return domain


class PageCreated(BaseModel):
    id: int
    slug: str


class PageResponse(BaseModel):
    id: int
    slug: str
    title: str
    description: str
    site_ids: list[int]
    show_urls: bool
    logo_url: str | None
    accent: str
    custom_domain: str | None
    cname_target: str
    subscribers: int
    created_at: datetime | None


class AnnouncementCreate(RequestModel):
    title: str = Field(min_length=1, max_length=150)
    body: str = Field(min_length=1, max_length=2000)
    pinned: bool = True


class AnnouncementResponse(BaseModel):
    id: int
    title: str
    body: str
    pinned: bool
    created_at: datetime


async def owned_page(session: SessionDep, user_id: int, page_id: int) -> StatusPage:
    page = await session.scalar(
        select(StatusPage).where(StatusPage.id == page_id, StatusPage.user_id == user_id)
    )
    if page is None:
        raise ApiError(404, "page_not_found", "Статус-страница не найдена")
    return page


async def page_sites(session: SessionDep, user_id: int, ids: list[int]) -> None:
    owned = set(
        await session.scalars(select(Site.id).where(Site.user_id == user_id, Site.id.in_(ids)))
    )
    if owned != set(ids):
        raise ApiError(404, "site_not_found", "Один из сайтов не найден")


async def ensure_domain_free(session: SessionDep, domain: str | None, page_id: int | None) -> None:
    if domain is None:
        return
    statement = select(StatusPage.id).where(StatusPage.custom_domain == domain)
    if page_id is not None:
        statement = statement.where(StatusPage.id != page_id)
    if await session.scalar(statement):
        raise ApiError(409, "domain_taken", "Домен уже привязан к другой странице")


def apply_branding(page: StatusPage, payload: PageCreate) -> None:
    page.show_urls = payload.show_urls
    page.logo_url = payload.logo_url
    page.accent = payload.accent if payload.accent in PAGE_ACCENTS else "emerald"
    page.custom_domain = payload.custom_domain


@router.post(
    "/status-pages",
    response_model=PageCreated,
    status_code=201,
    summary="Создать статус-страницу",
    description="Свой адрес (slug) и свой домен доступны на тарифах с custom_slug. "
    "Поддерживает Idempotency-Key.",
    responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def create_page(payload: PageCreate, user: CurrentUser, session: SessionDep) -> PageCreated:
    _, plan = await current_plan(session, user)
    if payload.slug or payload.custom_domain:
        require_feature(plan, "custom_slug")
    await page_sites(session, user.id, payload.site_ids)
    slug = payload.slug or uuid.uuid4().hex
    if await session.scalar(select(StatusPage.id).where(StatusPage.slug == slug)):
        raise ApiError(409, "slug_taken", "Этот адрес уже занят")
    await ensure_domain_free(session, payload.custom_domain, None)
    page = StatusPage(
        user_id=user.id, slug=slug, title=payload.title, description=payload.description
    )
    apply_branding(page, payload)
    session.add(page)
    await session.flush()
    for site_id in set(payload.site_ids):
        session.add(StatusPageSite(page_id=page.id, site_id=site_id))
    audit.record(session, user.id, "page.create", "status_page", page.id)
    await session.commit()
    return PageCreated(id=page.id, slug=page.slug)


@router.get(
    "/status-pages",
    response_model=list[PageResponse],
    responses={200: {"headers": PAGINATION_HEADERS}},
)
async def pages(
    request: Request,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    cursor: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
) -> list[PageResponse]:
    rows = list(
        await session.scalars(
            select(StatusPage)
            .where(StatusPage.id > cursor, StatusPage.user_id == user.id)
            .order_by(StatusPage.id)
            .limit(limit)
        )
    )
    paginate(request, response, rows, limit, lambda page: page.id)
    ids = [row.id for row in rows]
    bindings: dict[int, list[int]] = {}
    for page_id, site_id in await session.execute(
        select(StatusPageSite.page_id, StatusPageSite.site_id).where(
            StatusPageSite.page_id.in_(ids)
        )
    ):
        bindings.setdefault(page_id, []).append(site_id)
    counts = dict(
        (
            await session.execute(
                select(StatusSubscriber.page_id, func.count(StatusSubscriber.id))
                .where(StatusSubscriber.page_id.in_(ids), StatusSubscriber.confirmed.is_(True))
                .group_by(StatusSubscriber.page_id)
            )
        )
        .tuples()
        .all()
    )
    return [
        PageResponse(
            id=row.id,
            slug=row.slug,
            title=row.title,
            description=row.description,
            site_ids=sorted(bindings.get(row.id, [])),
            show_urls=row.show_urls,
            logo_url=row.logo_url,
            accent=row.accent,
            custom_domain=row.custom_domain,
            cname_target=settings.status_cname_target,
            subscribers=counts.get(row.id, 0),
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.put("/status-pages/{page_id}", status_code=204, responses=NOT_FOUND)
async def update_page(
    page_id: int, payload: PageCreate, user: CurrentUser, session: SessionDep
) -> None:
    page = await owned_page(session, user.id, page_id)
    _, plan = await current_plan(session, user)
    before = audit.snapshot(
        page, ("title", "description", "slug", "show_urls", "logo_url", "accent", "custom_domain")
    )
    if payload.slug and payload.slug != page.slug:
        require_feature(plan, "custom_slug")
        if await session.scalar(
            select(StatusPage.id).where(StatusPage.slug == payload.slug, StatusPage.id != page.id)
        ):
            raise ApiError(409, "slug_taken", "Этот адрес уже занят")
        page.slug = payload.slug
    if payload.custom_domain and payload.custom_domain != page.custom_domain:
        require_feature(plan, "custom_slug")
    await ensure_domain_free(session, payload.custom_domain, page.id)
    await page_sites(session, user.id, payload.site_ids)
    page.title, page.description = payload.title, payload.description
    apply_branding(page, payload)
    await session.execute(delete(StatusPageSite).where(StatusPageSite.page_id == page.id))
    for site_id in set(payload.site_ids):
        session.add(StatusPageSite(page_id=page.id, site_id=site_id))
    changes = audit.changes_between(before, audit.snapshot(page, tuple(before)))
    audit.record(session, user.id, "page.update", "status_page", page.id, changes)
    await session.commit()


@router.delete("/status-pages/{page_id}", status_code=204, responses=NOT_FOUND)
async def delete_page(page_id: int, user: CurrentUser, session: SessionDep) -> None:
    page = await owned_page(session, user.id, page_id)
    audit.record(
        session,
        user.id,
        "page.delete",
        "status_page",
        page.id,
        [{"field": "title", "old": page.title, "new": None}],
    )
    await session.delete(page)
    await session.commit()


@router.get(
    "/status-pages/{page_id}/announcements",
    response_model=list[AnnouncementResponse],
    responses=NOT_FOUND,
)
async def announcements(
    page_id: int, user: CurrentUser, session: SessionDep
) -> list[AnnouncementResponse]:
    page = await owned_page(session, user.id, page_id)
    rows = await session.scalars(
        select(StatusAnnouncement)
        .where(StatusAnnouncement.page_id == page.id)
        .order_by(StatusAnnouncement.pinned.desc(), StatusAnnouncement.created_at.desc())
    )
    return [
        AnnouncementResponse(
            id=row.id, title=row.title, body=row.body, pinned=row.pinned, created_at=row.created_at
        )
        for row in rows
    ]


@router.post(
    "/status-pages/{page_id}/announcements",
    response_model=AnnouncementResponse,
    status_code=201,
    responses=NOT_FOUND,
)
async def create_announcement(
    page_id: int, payload: AnnouncementCreate, user: CurrentUser, session: SessionDep
) -> AnnouncementResponse:
    page = await owned_page(session, user.id, page_id)
    row = StatusAnnouncement(
        page_id=page.id, title=payload.title, body=payload.body, pinned=payload.pinned
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    audit.record(session, user.id, "page.announcement", "status_page", page.id)
    await session.commit()
    return AnnouncementResponse(
        id=row.id, title=row.title, body=row.body, pinned=row.pinned, created_at=row.created_at
    )


@router.delete(
    "/status-pages/{page_id}/announcements/{announcement_id}",
    status_code=204,
    responses=NOT_FOUND,
)
async def delete_announcement(
    page_id: int, announcement_id: int, user: CurrentUser, session: SessionDep
) -> None:
    page = await owned_page(session, user.id, page_id)
    await session.execute(
        delete(StatusAnnouncement).where(
            StatusAnnouncement.id == announcement_id, StatusAnnouncement.page_id == page.id
        )
    )
    await session.commit()
