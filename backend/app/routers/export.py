import csv
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, select

from app.deps import CurrentUser, SessionDep
from app.errors import ApiError
from app.models import Check, Incident, Site
from app.services.billing import current_plan, require_feature

router = APIRouter(prefix="/export", tags=["export"])


def csv_value(value: object) -> str:
    text = "" if value is None else str(value)
    return (
        f"'{text}"
        if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r"))
        else text
    )


@router.get("/{kind}.csv")
async def export(
    kind: Literal["sites", "checks", "incidents"],
    user: CurrentUser,
    session: SessionDep,
    start: datetime | None = None,
    end: datetime | None = None,
    site_id: int | None = Query(None, ge=1),
) -> StreamingResponse:
    _, plan = await current_plan(session, user)
    require_feature(plan, "csv")
    now = datetime.now(UTC)
    start, end = start or now - timedelta(days=7), end or now
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or end <= start
        or (end - start).days > plan.retention_days
    ):
        raise ApiError(
            422, "invalid_period", "Укажите период с часовым поясом в пределах срока хранения"
        )
    sites = select(Site.id).where(Site.user_id == user.id)
    if site_id is not None:
        if await session.scalar(sites.where(Site.id == site_id)) is None:
            raise ApiError(404, "site_not_found", "Сайт не найден")
        sites = sites.where(Site.id == site_id)
    statement: Select[*tuple[Any, ...]]
    if kind == "sites":
        statement = select(Site.id, Site.name, Site.url, Site.status, Site.paused).where(
            Site.id.in_(sites)
        )
    elif kind == "checks":
        statement = select(
            Check.site_id,
            Check.checked_at,
            Check.ok,
            Check.status_code,
            Check.response_ms,
            Check.error,
        ).where(Check.site_id.in_(sites), Check.checked_at >= start, Check.checked_at < end)
    else:
        statement = select(
            Incident.site_id, Incident.started_at, Incident.ended_at, Incident.reason
        ).where(
            Incident.site_id.in_(sites), Incident.started_at >= start, Incident.started_at < end
        )
    result = await session.stream(statement.execution_options(yield_per=1000))

    async def chunks() -> AsyncIterator[str]:

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(result.keys())
        yield buffer.getvalue()
        async for row in result:
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow(csv_value(value) for value in row)
            yield buffer.getvalue()

    return StreamingResponse(
        chunks(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{kind}.csv"'},
    )
