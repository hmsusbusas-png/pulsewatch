import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import cast, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from app.config import settings
from app.crypto import decrypt_config
from app.deps import CurrentUser, SessionDep
from app.errors import ApiError, ErrorResponse
from app.models import Region, Site, SiteRegionState
from app.security import hash_refresh_token
from app.services.monitoring import record_probe
from app.services.probe import ProbeResult, Timings
from app.validation import RequestModel

router = APIRouter(tags=["regions"])
ONLINE_SECONDS = 90
LEASE_MINUTES = 5


class RegionResponse(BaseModel):
    code: str
    name: str
    is_primary: bool
    online: bool
    last_seen_at: datetime | None


class ClaimRequest(RequestModel):
    limit: int = Field(default=20, ge=1, le=50)


class Job(BaseModel):
    site_id: int
    lease: str
    monitor_type: str
    url: str
    method: str
    headers: dict[str, str]
    body: str | None
    expected_codes: list[int]
    timeout_seconds: int
    follow_redirects: bool
    keyword: str | None
    keyword_present: bool
    assertions: list[dict[str, str]]
    tcp_send: str | None
    tcp_expect: str | None
    dns_record_type: str | None
    dns_expected: str | None


class AgentResult(BaseModel):
    model_config = {"extra": "forbid"}
    site_id: int
    lease: str = Field(max_length=64)
    ok: bool
    status_code: int | None = None
    response_ms: int | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=500)
    timings: dict[str, int | None] = Field(default_factory=dict)


class ResultBatch(RequestModel):
    results: list[AgentResult] = Field(max_length=50)


async def agent_region(request: Request, session: SessionDep) -> Region:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "agent" or len(token) < 32:
        raise ApiError(401, "agent_unauthorized", "Нужен заголовок Authorization: Agent <token>")
    digest = hash_refresh_token(token)
    region = await session.scalar(select(Region).where(Region.token_hash == digest))
    if region is None or not hmac.compare_digest(region.token_hash or "", digest):
        raise ApiError(401, "agent_unauthorized", "Токен агента недействителен")
    region.last_seen_at = datetime.now(UTC)
    return region


AgentRegion = Annotated[Region, Depends(agent_region)]


@router.get("/regions", response_model=list[RegionResponse], summary="Регионы проверок")
async def regions(user: CurrentUser, session: SessionDep) -> list[RegionResponse]:
    rows = list(
        await session.scalars(select(Region).order_by(Region.is_primary.desc(), Region.code))
    )
    now = datetime.now(UTC)
    if not any(row.code == settings.worker_region for row in rows):
        rows.insert(
            0,
            Region(code=settings.worker_region, name=settings.worker_region_name, is_primary=True),
        )
    return [
        RegionResponse(
            code=row.code,
            name=row.name,
            is_primary=bool(row.is_primary),
            online=row.last_seen_at is not None
            and row.last_seen_at > now - timedelta(seconds=ONLINE_SECONDS),
            last_seen_at=row.last_seen_at,
        )
        for row in rows
    ]


@router.post(
    "/agent/claim",
    response_model=list[Job],
    summary="Получить задания региона",
    description="Только для региональных агентов. Задания выдаются с арендой на 5 минут.",
    responses={401: {"model": ErrorResponse}},
)
async def claim(payload: ClaimRequest, region: AgentRegion, session: SessionDep) -> list[Job]:
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(SiteRegionState, Site)
            .join(Site, Site.id == SiteRegionState.site_id)
            .where(
                SiteRegionState.region == region.code,
                SiteRegionState.next_check_at <= now,
                or_(SiteRegionState.lease_until.is_(None), SiteRegionState.lease_until < now),
                Site.paused.is_(False),
                cast(Site.regions, JSONB).contains([region.code]),
            )
            .order_by(SiteRegionState.next_check_at)
            .limit(payload.limit)
            .with_for_update(of=SiteRegionState, skip_locked=True)
        )
    ).all()
    jobs = []
    for state, site in rows:
        state.lease_token = secrets.token_hex(16)
        state.lease_until = now + timedelta(minutes=LEASE_MINUTES)
        jobs.append(
            Job(
                site_id=site.id,
                lease=state.lease_token,
                monitor_type=site.monitor_type,
                url=site.url,
                method=site.method,
                headers=decrypt_config(site.headers_encrypted) if site.headers_encrypted else {},
                body=site.body,
                expected_codes=site.expected_codes,
                timeout_seconds=site.timeout_seconds,
                follow_redirects=site.follow_redirects,
                keyword=site.keyword,
                keyword_present=site.keyword_present,
                assertions=site.assertions,
                tcp_send=site.tcp_send,
                tcp_expect=site.tcp_expect,
                dns_record_type=site.dns_record_type,
                dns_expected=site.dns_expected,
            )
        )
    await session.commit()
    return jobs


@router.post(
    "/agent/results",
    summary="Отправить результаты региона",
    description="Результат принимается только с действующей арендой задания.",
    responses={401: {"model": ErrorResponse}},
)
async def results(payload: ResultBatch, region: AgentRegion, session: SessionDep) -> dict[str, int]:
    code = region.code
    await session.commit()
    accepted = 0
    for item in payload.results:
        state = await session.get(SiteRegionState, (item.site_id, code), with_for_update=True)
        if (
            state is None
            or state.lease_token is None
            or not hmac.compare_digest(state.lease_token, item.lease)
        ):
            await session.rollback()
            continue
        site = await session.get(Site, item.site_id)
        interval = site.interval_seconds if site else 300
        state.lease_token = None
        state.lease_until = None
        state.next_check_at = datetime.now(UTC) + timedelta(
            seconds=interval + secrets.randbelow(max(2, interval // 10))
        )
        await session.commit()
        timings = Timings(
            **{
                key: value
                for key, value in item.timings.items()
                if key in {"dns_ms", "connect_ms", "tls_ms", "ttfb_ms", "transfer_ms"}
            }
        )
        await record_probe(
            item.site_id,
            ProbeResult(item.ok, item.status_code, item.response_ms, item.error, timings),
            item.lease,
            region=code,
        )
        accepted += 1
    return {"accepted": accepted}
