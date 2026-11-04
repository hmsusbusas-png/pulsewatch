import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, select, update

from app.config import settings
from app.deps import CurrentUser, SessionDep
from app.errors import ApiError, ErrorResponse, Responses
from app.models import Check, Incident, NotificationChannel, Site, SiteRegionState, SiteStatus
from app.pagination import PAGINATION_HEADERS, paginate
from app.schemas import (
    CheckResponse,
    IncidentResponse,
    SiteCreateRequest,
    SiteResponse,
    SiteUpdateRequest,
)
from app.security import hash_refresh_token
from app.services import audit
from app.services.billing import current_plan, enforce_site_limit
from app.services.dns_probe import public_hostname
from app.services.events import publish, wake_worker
from app.services.monitors import FIELDS, configure_monitor
from app.services.rate_limit import allow_persistent
from app.services.site_views import site_responses
from app.ssrf import ensure_target_allowed, normalize_url

router = APIRouter(prefix="/sites", tags=["sites"])

UPTIME_WINDOWS = {"24h": 24, "7d": 168, "30d": 720, "90d": 2160}
AUDITED = (*sorted(FIELDS), "name", "url", "interval_seconds", "is_public", "show_url_publicly")
NOT_FOUND: Responses = {404: {"model": ErrorResponse, "description": "Монитор не найден"}}


class CheckNowResponse(BaseModel):
    site_id: int
    queued_at: datetime


async def load_owned_site(session: SessionDep, user_id: int, site_id: int) -> Site:
    site = await session.scalar(select(Site).where(Site.id == site_id, Site.user_id == user_id))
    if site is None:
        raise ApiError(404, "site_not_found", "Site does not exist")
    return site


async def build_site_response(session: SessionDep, site: Site) -> SiteResponse:
    return (await site_responses(session, [site]))[0]


async def target_url(monitor_type: str, raw: str) -> str:
    if monitor_type == "dns":
        return public_hostname(raw)
    url = normalize_url(f"http://{raw}" if monitor_type == "tcp" and "://" not in raw else raw)
    await ensure_target_allowed(url, settings.allow_private_targets)
    return url


def issue_heartbeat(site: Site) -> str:
    token = f"hb_{secrets.token_urlsafe(24)}"
    site.heartbeat_token_hash = hash_refresh_token(token)
    site.heartbeat_prefix = token[:10]
    return token


@router.get(
    "",
    response_model=list[SiteResponse],
    summary="Список мониторов",
    responses={200: {"headers": PAGINATION_HEADERS}},
)
async def list_sites(
    request: Request,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    cursor: int = Query(0, ge=0, description="id последнего монитора предыдущей страницы"),
    limit: int = Query(100, ge=1, le=100),
    search: str = Query("", max_length=100),
    tag: str | None = None,
    group: str | None = None,
    paused: bool | None = None,
    status: SiteStatus | None = None,
    monitor_type: str | None = Query(None, max_length=16),
) -> list[SiteResponse]:
    statement = select(Site).where(Site.user_id == user.id, Site.id > cursor)
    if search:
        statement = statement.where(
            Site.name.icontains(search, autoescape=True)
            | Site.url.icontains(search, autoescape=True)
        )
    if group is not None:
        statement = statement.where(Site.group == group)
    if paused is not None:
        statement = statement.where(Site.paused == paused)
    if status is not None:
        statement = statement.where(Site.status == status)
    if monitor_type is not None:
        statement = statement.where(Site.monitor_type == monitor_type)
    if tag is not None:
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSONB

        statement = statement.where(cast(Site.tags, JSONB).contains([tag]))
    sites = (await session.scalars(statement.order_by(Site.id).limit(limit))).all()
    paginate(request, response, sites, limit, lambda site: site.id)
    return await site_responses(session, sites)


@router.post(
    "",
    response_model=SiteResponse,
    status_code=201,
    summary="Создать монитор",
    description=(
        "Для `heartbeat` поле url не нужно: ответ содержит `heartbeat_token`, "
        "он показывается один раз. Для `dns` в url передаётся имя хоста. "
        "Поддерживает заголовок Idempotency-Key."
    ),
    responses={402: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def create_site(
    payload: SiteCreateRequest, user: CurrentUser, session: SessionDep
) -> SiteResponse:
    plan = await enforce_site_limit(session, user)
    token = None
    site = Site(
        user_id=user.id,
        name=payload.name,
        url="",
        interval_seconds=payload.interval_seconds,
        is_public=payload.is_public,
        show_url_publicly=payload.show_url_publicly,
    )
    if payload.monitor_type == "heartbeat":
        token = issue_heartbeat(site)
        site.next_check_at = datetime.now(UTC) + timedelta(
            seconds=(payload.heartbeat_period_seconds or 3600)
            + (payload.heartbeat_grace_seconds or 0)
        )
    else:
        if not payload.url.strip():
            raise ApiError(
                422,
                "validation_error",
                "url: обязательное поле",
                [{"field": "url", "message": "Укажите адрес", "type": "missing"}],
            )
        site.url = await target_url(payload.monitor_type, payload.url)
    session.add(site)
    await session.flush()
    values = payload.model_dump()
    if "channel_ids" not in payload.model_fields_set:
        values["channel_ids"] = list(
            await session.scalars(
                select(NotificationChannel.id).where(
                    NotificationChannel.user_id == user.id,
                    NotificationChannel.type == "telegram",
                    NotificationChannel.enabled.is_(True),
                )
            )
        )
    await configure_monitor(session, site, values, plan)
    audit.record(session, user.id, "site.create", "site", site.id)
    await publish(session, user.id, "site_updated", {"site_id": site.id, "created": True})
    if token is None:
        await wake_worker(session, site.id)
    await session.commit()
    await session.refresh(site)
    result = await build_site_response(session, site)
    result.heartbeat_token = token
    return result


@router.get("/{site_id:int}", response_model=SiteResponse, responses=NOT_FOUND)
async def read_site(site_id: int, user: CurrentUser, session: SessionDep) -> SiteResponse:
    site = await load_owned_site(session, user.id, site_id)
    return await build_site_response(session, site)


@router.patch(
    "/{site_id:int}",
    response_model=SiteResponse,
    responses=NOT_FOUND,
    summary="Изменить монитор",
    description="Изменения попадают в журнал действий с прежними и новыми значениями.",
)
async def update_site(
    site_id: int, payload: SiteUpdateRequest, user: CurrentUser, session: SessionDep
) -> SiteResponse:
    _, plan = await current_plan(session, user)
    site = await load_owned_site(session, user.id, site_id)
    before = audit.snapshot(site, (*AUDITED, "headers_encrypted"))
    effective_type = payload.monitor_type or site.monitor_type
    if (site.monitor_type == "heartbeat") != (effective_type == "heartbeat"):
        raise ApiError(
            422, "monitor_type_locked", "Тип heartbeat нельзя сменить; создайте новый монитор"
        )
    if payload.url is not None and effective_type != "heartbeat":
        url = await target_url(effective_type, payload.url)
        if url != site.url:
            site.url = url
            site.ssl_expires_at = None
            site.ssl_checked_at = None
            site.ssl_details = None
            site.dns_values = None
    if payload.name is not None:
        site.name = payload.name
    if payload.interval_seconds is not None:
        site.interval_seconds = payload.interval_seconds
    if payload.is_public is not None:
        site.is_public = payload.is_public
    if payload.show_url_publicly is not None:
        site.show_url_publicly = payload.show_url_publicly
    await configure_monitor(session, site, payload.model_dump(exclude_unset=True), plan)
    changes = audit.changes_between(before, audit.snapshot(site, (*AUDITED, "headers_encrypted")))
    if changes:
        audit.record(session, user.id, "site.update", "site", site.id, changes)
        visible = {
            change["field"]: change["new"]
            for change in changes
            if change["field"] in {"name", "paused", "url", "interval_seconds", "tags", "group"}
        }
        await publish(session, user.id, "site_updated", {"site_id": site.id, **visible})
    await session.commit()
    await session.refresh(site)
    return await build_site_response(session, site)


@router.delete("/{site_id:int}", status_code=204, responses=NOT_FOUND)
async def delete_site(site_id: int, user: CurrentUser, session: SessionDep) -> None:
    site = await load_owned_site(session, user.id, site_id)
    audit.record(
        session,
        user.id,
        "site.delete",
        "site",
        site.id,
        [{"field": "name", "old": site.name, "new": None}],
    )
    await publish(session, user.id, "site_updated", {"site_id": site.id, "deleted": True})
    await session.execute(delete(Site).where(Site.id == site.id))
    await session.commit()


@router.get(
    "/{site_id:int}/checks",
    response_model=list[CheckResponse],
    summary="История проверок",
    description=(
        "Сырые проверки за период с этапами запроса. Для 30d и 90d данные ограничены сроком "
        "хранения тарифа; агрегаты за длинные периоды отдаёт /stats по суточным свёрткам."
    ),
    responses={200: {"headers": PAGINATION_HEADERS}, **NOT_FOUND},
)
async def list_checks(
    site_id: int,
    request: Request,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    cursor: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=1000),
    range_key: str = Query("24h", alias="range", pattern="^(24h|7d|30d|90d)$"),
    order: Literal["asc", "desc"] = "asc",
    failed: bool | None = None,
) -> list[CheckResponse]:
    site = await load_owned_site(session, user.id, site_id)
    since = datetime.now(UTC) - timedelta(hours=UPTIME_WINDOWS[range_key])
    statement = select(Check).where(Check.site_id == site.id, Check.checked_at >= since)
    if failed is not None:
        statement = statement.where(Check.ok.is_(not failed))
    if order == "asc":
        statement = statement.where(Check.id > cursor).order_by(Check.id)
    else:
        if cursor:
            statement = statement.where(Check.id < cursor)
        statement = statement.order_by(Check.id.desc())
    rows = list((await session.scalars(statement.limit(limit))).all())
    paginate(request, response, rows, limit, lambda check: check.id)
    return [
        CheckResponse(
            id=check.id,
            checked_at=check.checked_at,
            status_code=check.status_code,
            response_ms=check.response_ms,
            ok=check.ok,
            error=check.error,
            dns_ms=check.dns_ms,
            connect_ms=check.connect_ms,
            tls_ms=check.tls_ms,
            ttfb_ms=check.ttfb_ms,
            transfer_ms=check.transfer_ms,
            region=check.region,
        )
        for check in rows
    ]


def incident_response(incident: Incident, now: datetime) -> IncidentResponse:
    return IncidentResponse(
        id=incident.id,
        site_id=incident.site_id,
        kind=incident.kind,
        started_at=incident.started_at,
        confirmed_at=incident.confirmed_at,
        acknowledged_at=incident.acknowledged_at,
        ended_at=incident.ended_at,
        duration_seconds=int(((incident.ended_at or now) - incident.started_at).total_seconds()),
        reason=incident.reason,
        is_open=incident.ended_at is None,
    )


@router.get(
    "/{site_id:int}/incidents",
    response_model=list[IncidentResponse],
    responses={200: {"headers": PAGINATION_HEADERS}, **NOT_FOUND},
)
async def list_incidents(
    site_id: int,
    request: Request,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    cursor: int = Query(0, ge=0, description="id последнего инцидента предыдущей страницы"),
    limit: int = Query(100, ge=1, le=100),
    kind: Literal["down", "degraded"] | None = None,
) -> list[IncidentResponse]:
    site = await load_owned_site(session, user.id, site_id)
    statement = select(Incident).where(Incident.site_id == site.id)
    if cursor:
        statement = statement.where(Incident.id < cursor)
    if kind is not None:
        statement = statement.where(Incident.kind == kind)
    incidents = list(
        (await session.scalars(statement.order_by(Incident.id.desc()).limit(limit))).all()
    )
    paginate(request, response, incidents, limit, lambda incident: incident.id)
    now = datetime.now(UTC)
    return [incident_response(incident, now) for incident in incidents]


@router.post(
    "/{site_id:int}/check-now",
    response_model=CheckNowResponse,
    status_code=202,
    summary="Проверить сейчас",
    description=(
        "Ставит монитор в начало очереди и будит воркер через NOTIFY. Результат приходит "
        "событием check_completed в /stream. Лимиты: на пользователя в минуту и один запуск "
        "на монитор за окно; ответ содержит X-RateLimit-* и Retry-After."
    ),
    responses={409: {"model": ErrorResponse}, 429: {"model": ErrorResponse}, **NOT_FOUND},
)
async def check_now(
    site_id: int, response: Response, user: CurrentUser, session: SessionDep
) -> CheckNowResponse:
    site = await load_owned_site(session, user.id, site_id)
    if site.paused:
        raise ApiError(409, "site_paused", "Монитор на паузе")
    if site.monitor_type == "heartbeat":
        raise ApiError(409, "heartbeat_passive", "Heartbeat-монитор ждёт сигналов, а не опрашивает")
    user_gate = await allow_persistent(
        f"check-now:user:{user.id}", settings.check_now_per_user_per_minute, 60
    )
    if not user_gate:
        raise ApiError(
            429, "rate_limited", "Слишком много ручных проверок", headers=user_gate.headers()
        )
    site_gate = await allow_persistent(
        f"check-now:site:{site.id}", 1, settings.check_now_site_cooldown_seconds
    )
    if not site_gate:
        raise ApiError(
            429,
            "check_rate_limited",
            "Проверка этого монитора уже запрошена, подождите",
            headers=site_gate.headers(),
        )
    now = datetime.now(UTC)
    site.next_check_at = now
    await session.execute(
        update(SiteRegionState)
        .where(SiteRegionState.site_id == site.id, SiteRegionState.region.in_(site.regions or [""]))
        .values(next_check_at=now)
    )
    await wake_worker(session, site.id)
    await session.commit()
    response.headers.update(user_gate.headers())
    return CheckNowResponse(site_id=site.id, queued_at=now)


@router.post(
    "/{site_id:int}/heartbeat/rotate",
    response_model=SiteResponse,
    summary="Перевыпустить адрес пинга",
    description="Старый адрес перестаёт работать сразу. Новый токен показывается один раз.",
    responses={409: {"model": ErrorResponse}, **NOT_FOUND},
)
async def rotate_heartbeat(site_id: int, user: CurrentUser, session: SessionDep) -> SiteResponse:
    site = await load_owned_site(session, user.id, site_id)
    if site.monitor_type != "heartbeat":
        raise ApiError(409, "not_heartbeat", "Монитор не является heartbeat")
    token = issue_heartbeat(site)
    audit.record(session, user.id, "site.heartbeat_rotate", "site", site.id)
    await session.commit()
    await session.refresh(site)
    result = await build_site_response(session, site)
    result.heartbeat_token = token
    return result
