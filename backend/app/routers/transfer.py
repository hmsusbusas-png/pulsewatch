import csv
import io
import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, func, select

from app.deps import CurrentUser, SessionDep
from app.errors import ApiError, ErrorResponse
from app.models import Site
from app.routers.sites import issue_heartbeat, target_url
from app.schemas import SiteCreateRequest
from app.services import audit
from app.services.billing import current_plan
from app.services.events import publish
from app.services.monitors import configure_monitor
from app.validation import RequestModel

router = APIRouter(prefix="/sites", tags=["sites"])
MAX_IMPORT_ROWS = 200
EXPORT_FIELDS = (
    "name",
    "url",
    "monitor_type",
    "interval_seconds",
    "method",
    "expected_codes",
    "timeout_seconds",
    "follow_redirects",
    "keyword",
    "keyword_present",
    "failure_threshold",
    "cooldown_seconds",
    "tags",
    "group",
    "tcp_send",
    "tcp_expect",
    "assertions",
    "expiry_thresholds",
    "check_domain",
    "slo_target",
    "dns_record_type",
    "dns_expected",
    "heartbeat_period_seconds",
    "heartbeat_grace_seconds",
    "regions",
    "is_public",
    "show_url_publicly",
    "maintenance",
)
CSV_FIELDS = ("name", "url", "monitor_type", "interval_seconds", "tags", "group", "keyword")


class DryRunRollback(Exception):
    pass


class BulkRequest(RequestModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal["pause", "resume", "delete", "set_tags", "set_group", "set_channels"]
    tags: list[str] | None = Field(default=None, max_length=20)
    group: str | None = Field(default=None, max_length=100)
    channel_ids: list[int] | None = Field(default=None, max_length=100)


class BulkItem(BaseModel):
    id: int
    ok: bool
    error: str | None = None


class BulkResult(BaseModel):
    updated: int
    results: list[BulkItem]


class ImportRequest(RequestModel):
    format: Literal["json", "csv"]
    content: str = Field(min_length=1, max_length=524288)
    dry_run: bool = False


class ImportRow(BaseModel):
    row: int
    name: str | None
    status: Literal["created", "valid", "error"]
    site_id: int | None = None
    heartbeat_token: str | None = None
    error: str | None = None


class ImportReport(BaseModel):
    created: int
    valid: int
    failed: int
    rows: list[ImportRow]


@router.post(
    "/bulk",
    response_model=BulkResult,
    summary="Массовые операции",
    description="Каждый монитор обрабатывается отдельно: ошибка одного не откатывает другие. "
    "Чужие и несуществующие id возвращаются с ошибкой site_not_found.",
)
async def bulk(payload: BulkRequest, user: CurrentUser, session: SessionDep) -> BulkResult:
    _, plan = await current_plan(session, user)
    sites = {
        site.id: site
        for site in await session.scalars(
            select(Site).where(Site.user_id == user.id, Site.id.in_(payload.ids))
        )
    }
    results = []
    for site_id in dict.fromkeys(payload.ids):
        site = sites.get(site_id)
        if site is None:
            results.append(BulkItem(id=site_id, ok=False, error="site_not_found"))
            continue
        values: dict[str, object] = {}
        if payload.action in ("pause", "resume"):
            values["paused"] = payload.action == "pause"
        elif payload.action == "set_tags":
            values["tags"] = payload.tags or []
        elif payload.action == "set_group":
            values["group"] = payload.group or None
        elif payload.action == "set_channels":
            values["channel_ids"] = payload.channel_ids or []
        try:
            async with session.begin_nested():
                if payload.action == "delete":
                    await session.execute(delete(Site).where(Site.id == site.id))
                else:
                    before = audit.snapshot(site, tuple(values))
                    await configure_monitor(session, site, values, plan)
                    changes = audit.changes_between(before, audit.snapshot(site, tuple(values)))
                    if changes:
                        audit.record(session, user.id, "site.update", "site", site.id, changes)
            results.append(BulkItem(id=site_id, ok=True))
            await publish(
                session,
                user.id,
                "site_updated",
                {"site_id": site_id, "deleted": True}
                if payload.action == "delete"
                else {
                    "site_id": site_id,
                    **{k: v for k, v in values.items() if k != "channel_ids"},
                },
            )
        except ApiError as exc:
            results.append(BulkItem(id=site_id, ok=False, error=exc.code))
    updated = sum(1 for item in results if item.ok)
    audit.record(
        session,
        user.id,
        f"site.bulk_{payload.action}",
        "site",
        None,
        [{"field": "count", "old": None, "new": updated}],
    )
    await session.commit()
    return BulkResult(updated=updated, results=results)


def parse_rows(payload: ImportRequest) -> list[dict[str, object]]:
    if payload.format == "json":
        try:
            document = json.loads(payload.content)
        except ValueError as exc:
            raise ApiError(422, "invalid_import", f"JSON не разобран: {exc}") from exc
        rows = document.get("monitors") if isinstance(document, dict) else document
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ApiError(
                422, "invalid_import", "Ожидается массив мониторов или {monitors: [...]}"
            )
        return rows
    reader = csv.DictReader(io.StringIO(payload.content.lstrip("﻿")))
    if not reader.fieldnames or not {"name", "url"} <= set(reader.fieldnames):
        raise ApiError(422, "invalid_import", "В CSV нужны столбцы name и url")
    rows = []
    for record in reader:
        row: dict[str, object] = {
            key: value.strip() for key, value in record.items() if key and value and value.strip()
        }
        if "type" in row and "monitor_type" not in row:
            row["monitor_type"] = row.pop("type")
        if isinstance(row.get("tags"), str):
            row["tags"] = [tag.strip() for tag in str(row["tags"]).split(";") if tag.strip()]
        rows.append(row)
    return rows


def describe_validation(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', 'invalid')}" if location else str(first.get("msg"))


@router.post(
    "/import",
    response_model=ImportReport,
    summary="Импорт мониторов",
    description=(
        f"JSON (массив или {{monitors: [...]}}) или CSV со столбцами {', '.join(CSV_FIELDS)}; "
        f"теги в CSV через «;». До {MAX_IMPORT_ROWS} строк. dry_run только проверяет. Лимит "
        "тарифа применяется к сумме существующих и новых мониторов."
    ),
    responses={422: {"model": ErrorResponse}},
)
async def import_sites(
    payload: ImportRequest, user: CurrentUser, session: SessionDep
) -> ImportReport:
    _, plan = await current_plan(session, user)
    rows = parse_rows(payload)
    if len(rows) > MAX_IMPORT_ROWS:
        raise ApiError(422, "invalid_import", f"Не более {MAX_IMPORT_ROWS} строк за раз")
    existing = await session.scalar(select(func.count(Site.id)).where(Site.user_id == user.id))
    slots = plan.max_sites - (existing or 0)
    report: list[ImportRow] = []
    for number, raw in enumerate(rows, start=1):
        name = str(raw.get("name")) if raw.get("name") else None
        try:
            request = SiteCreateRequest.model_validate(raw)
        except ValidationError as exc:
            report.append(
                ImportRow(row=number, name=name, status="error", error=describe_validation(exc))
            )
            continue
        if slots <= 0:
            report.append(
                ImportRow(
                    row=number,
                    name=name,
                    status="error",
                    error=f"plan_limit_sites: тариф {plan.name}: не более {plan.max_sites}",
                )
            )
            continue
        try:
            async with session.begin_nested():
                site = Site(
                    user_id=user.id,
                    name=request.name,
                    url="",
                    interval_seconds=request.interval_seconds,
                    is_public=request.is_public,
                    show_url_publicly=request.show_url_publicly,
                )
                token = None
                if request.monitor_type == "heartbeat":
                    token = issue_heartbeat(site)
                else:
                    if not request.url.strip():
                        raise ApiError(422, "validation_error", "url: обязательное поле")
                    site.url = await target_url(request.monitor_type, request.url)
                session.add(site)
                await session.flush()
                await configure_monitor(session, site, request.model_dump(), plan)
                if payload.dry_run:
                    raise DryRunRollback
            slots -= 1
            audit.record(session, user.id, "site.import", "site", site.id)
            report.append(
                ImportRow(
                    row=number,
                    name=request.name,
                    status="created",
                    site_id=site.id,
                    heartbeat_token=token,
                )
            )
        except DryRunRollback:
            slots -= 1
            report.append(ImportRow(row=number, name=request.name, status="valid"))
        except ApiError as exc:
            report.append(
                ImportRow(
                    row=number,
                    name=request.name,
                    status="error",
                    error=f"{exc.code}: {exc.message}",
                )
            )
    await session.commit()
    return ImportReport(
        created=sum(1 for row in report if row.status == "created"),
        valid=sum(1 for row in report if row.status == "valid"),
        failed=sum(1 for row in report if row.status == "error"),
        rows=report,
    )


def export_value(site: Site, field: str) -> object:
    value = getattr(site, field)
    if field == "url" and site.monitor_type == "heartbeat":
        return ""
    return value


@router.get(
    "/export",
    summary="Экспорт мониторов",
    description="Конфигурация без секретов: заголовки и токены не выгружаются. Формат JSON "
    "совместим с импортом.",
    response_class=Response,
    responses={200: {"content": {"application/json": {}, "text/csv": {}}}},
)
async def export_sites(
    user: CurrentUser,
    session: SessionDep,
    export_format: Literal["json", "csv"] = Query("json", alias="format"),
) -> Response:
    sites = list(
        await session.scalars(select(Site).where(Site.user_id == user.id).order_by(Site.id))
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    if export_format == "json":
        document = {
            "exported_at": datetime.now(UTC).isoformat(),
            "monitors": [
                {field: export_value(site, field) for field in EXPORT_FIELDS} for site in sites
            ],
        }
        return Response(
            json.dumps(document, ensure_ascii=False, indent=2, default=str),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="pulsewatch-monitors-{stamp}.json"'
            },
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_FIELDS)
    for site in sites:
        row = []
        for field in CSV_FIELDS:
            value = export_value(site, field)
            text = (
                ";".join(value) if isinstance(value, list) else "" if value is None else str(value)
            )
            row.append(f"'{text}" if text.lstrip().startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return Response(
        buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="pulsewatch-monitors-{stamp}.csv"'},
    )
