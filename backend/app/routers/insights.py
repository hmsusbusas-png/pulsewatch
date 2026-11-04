import json
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.crypto import decrypt_text
from app.deps import CurrentUser, SessionDep
from app.errors import ApiError, ErrorResponse
from app.models import Site
from app.routers.sites import NOT_FOUND, load_owned_site
from app.schemas import Assertion, SiteDays, SiteStats, SlaReport, SloReport, StatsRange
from app.services.assertions import evaluate_assertions, validate_assertions
from app.services.billing import current_plan, require_feature
from app.services.site_views import daily_uptime
from app.services.stats import site_stats, sla_report, slo_report
from app.validation import RequestModel

router = APIRouter(tags=["analytics"])


class AssertionPreviewRequest(RequestModel):
    assertions: list[Assertion] = Field(min_length=1, max_length=10)


class AssertionPreviewItem(BaseModel):
    index: int
    passed: bool
    actual: str | None
    message: str


class AssertionPreview(BaseModel):
    status_code: int | None
    body_sample: str
    results: list[AssertionPreviewItem]


@router.get(
    "/sites/daily",
    response_model=list[SiteDays],
    summary="Суточный аптайм для полос",
    description="По суткам UTC: аптайм, число проверок, инцидентов и простой в секундах.",
)
async def sites_daily(
    user: CurrentUser,
    session: SessionDep,
    days: int = Query(90, ge=1, le=90),
    site_id: Annotated[list[int] | None, Query(max_length=100)] = None,
) -> list[SiteDays]:
    statement = select(Site.id).where(Site.user_id == user.id).order_by(Site.id).limit(500)
    if site_id:
        statement = statement.where(Site.id.in_(site_id))
    ids = list(await session.scalars(statement))
    return await daily_uptime(session, ids, days) if ids else []


@router.get(
    "/sites/{site_id:int}/stats",
    response_model=SiteStats,
    summary="Статистика монитора за период",
    description=(
        "24h: 5-минутные корзины, 7d: часовые, обе по сырым проверкам. 30d и 90d строятся по "
        "суточным свёрткам; перцентили считаются по гистограмме задержек (44 корзины)."
    ),
    responses=NOT_FOUND,
)
async def stats(
    site_id: int,
    user: CurrentUser,
    session: SessionDep,
    range_key: Annotated[StatsRange, Query(alias="range")] = "24h",
) -> SiteStats:
    site = await load_owned_site(session, user.id, site_id)
    return await site_stats(session, site, range_key)


@router.get(
    "/sites/{site_id:int}/slo",
    response_model=SloReport,
    summary="Бюджет ошибок за 30 дней",
    description="Окна обслуживания исключаются из окна и из простоя. Скорость сжигания: "
    "доля простоя за 1 ч и 24 ч, делённая на допустимую долю ошибок.",
    responses=NOT_FOUND,
)
async def slo(site_id: int, user: CurrentUser, session: SessionDep) -> SloReport:
    site = await load_owned_site(session, user.id, site_id)
    return await slo_report(session, site)


@router.get(
    "/sites/{site_id:int}/daily",
    response_model=SiteDays,
    responses=NOT_FOUND,
)
async def site_daily(
    site_id: int, user: CurrentUser, session: SessionDep, days: int = Query(90, ge=1, le=90)
) -> SiteDays:
    site = await load_owned_site(session, user.id, site_id)
    return (await daily_uptime(session, [site.id], days))[0]


@router.post(
    "/sites/{site_id:int}/assertions/preview",
    response_model=AssertionPreview,
    summary="Проверить условия на последнем ответе",
    responses={409: {"model": ErrorResponse}, **NOT_FOUND},
)
async def preview_assertions(
    site_id: int, payload: AssertionPreviewRequest, user: CurrentUser, session: SessionDep
) -> AssertionPreview:
    site = await load_owned_site(session, user.id, site_id)
    if not site.last_response_encrypted:
        raise ApiError(409, "no_response_yet", "Ответ ещё не получен: дождитесь первой проверки")
    snapshot = json.loads(decrypt_text(site.last_response_encrypted))
    items = validate_assertions([item.model_dump() for item in payload.assertions])
    body = str(snapshot.get("body", ""))
    outcomes = evaluate_assertions(items, dict(snapshot.get("headers", {})), body)
    return AssertionPreview(
        status_code=snapshot.get("status_code"),
        body_sample=body[:2048],
        results=[
            AssertionPreviewItem(
                index=item.index, passed=item.passed, actual=item.actual, message=item.message
            )
            for item in outcomes
        ],
    )


@router.get(
    "/reports/sla",
    response_model=SlaReport,
    tags=["reports"],
    summary="SLA-отчёт за период",
    description="Аптайм по времени с исключением окон обслуживания; период до 366 дней.",
)
async def sla(
    user: CurrentUser,
    session: SessionDep,
    start: Annotated[datetime, Query(alias="from")],
    end: Annotated[datetime, Query(alias="to")],
) -> SlaReport:
    _, plan = await current_plan(session, user)
    require_feature(plan, "slo")
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ApiError(422, "invalid_period", "Укажите from и to с часовым поясом, to позже from")
    if end - start > timedelta(days=366):
        raise ApiError(422, "invalid_period", "Период не длиннее 366 дней")
    end = min(end, datetime.now(UTC))
    sites = list(
        await session.scalars(select(Site).where(Site.user_id == user.id).order_by(Site.name))
    )
    return await sla_report(session, sites, start, end)
