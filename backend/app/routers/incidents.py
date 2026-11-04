from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.crypto import decrypt_text
from app.deps import CurrentUser, SessionDep
from app.errors import ApiError, ErrorResponse, Responses
from app.models import CheckDiagnostic, Incident, IncidentEscalation, IncidentNote, Site
from app.pagination import PAGINATION_HEADERS, paginate
from app.routers.sites import incident_response
from app.schemas import (
    DiagnosticResponse,
    IncidentDetail,
    IncidentNoteResponse,
    IncidentResponse,
    TimelineEntry,
)
from app.services import audit
from app.services.events import publish
from app.validation import RequestModel

router = APIRouter(prefix="/incidents", tags=["incidents"])
NOT_FOUND: Responses = {404: {"model": ErrorResponse, "description": "Инцидент не найден"}}
NoteStatus = Literal["investigating", "identified", "monitoring", "resolved"]


class NoteCreate(RequestModel):
    status: NoteStatus
    text: str = Field(min_length=1, max_length=2000)


class NoteCreated(BaseModel):
    id: int


class PostmortemUpdate(RequestModel):
    text: str = Field(max_length=20000)


class IncidentListItem(IncidentResponse):
    site_name: str


async def owned_incident(session: SessionDep, user_id: int, incident_id: int) -> Incident:
    incident = await session.scalar(
        select(Incident).join(Site).where(Incident.id == incident_id, Site.user_id == user_id)
    )
    if incident is None:
        raise ApiError(404, "incident_not_found", "Инцидент не найден")
    return incident


@router.get(
    "",
    response_model=list[IncidentListItem],
    summary="Инциденты всех мониторов",
    responses={200: {"headers": PAGINATION_HEADERS}},
)
async def list_all(
    request: Request,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    cursor: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    state: Literal["open", "resolved"] | None = None,
    kind: Literal["down", "degraded"] | None = None,
) -> list[IncidentListItem]:
    statement = select(Incident, Site.name).join(Site).where(Site.user_id == user.id)
    if cursor:
        statement = statement.where(Incident.id < cursor)
    if state == "open":
        statement = statement.where(Incident.ended_at.is_(None))
    elif state == "resolved":
        statement = statement.where(Incident.ended_at.is_not(None))
    if kind is not None:
        statement = statement.where(Incident.kind == kind)
    rows = (await session.execute(statement.order_by(Incident.id.desc()).limit(limit))).all()
    paginate(request, response, rows, limit, lambda row: row[0].id)
    now = datetime.now(UTC)
    return [
        IncidentListItem(**incident_response(incident, now).model_dump(), site_name=name)
        for incident, name in rows
    ]


def build_timeline(
    incident: Incident,
    notes: list[IncidentNote],
    escalations: list[datetime],
    now: datetime,
) -> list[TimelineEntry]:
    entries = [TimelineEntry(at=incident.started_at, kind="started", text=incident.reason)]
    if incident.confirmed_at and incident.confirmed_at > incident.started_at:
        entries.append(TimelineEntry(at=incident.confirmed_at, kind="confirmed"))
    if incident.acknowledged_at:
        entries.append(TimelineEntry(at=incident.acknowledged_at, kind="acknowledged"))
    entries.extend(TimelineEntry(at=moment, kind="escalated") for moment in escalations)
    entries.extend(
        TimelineEntry(at=note.created_at, kind="note", status=note.status, text=note.text)
        for note in notes
    )
    if incident.ended_at:
        entries.append(TimelineEntry(at=incident.ended_at, kind="resolved"))
    entries.sort(key=lambda entry: entry.at)
    for current, following in zip(entries, [*entries[1:], None], strict=True):
        if following is not None:
            current.duration_seconds = int((following.at - current.at).total_seconds())
        elif incident.ended_at is None:
            current.duration_seconds = int((now - current.at).total_seconds())
    return entries


@router.get(
    "/{incident_id}",
    response_model=IncidentDetail,
    summary="Карточка инцидента",
    description="Лента событий с длительностью этапов и диагностика в момент сбоя: код, "
    "заголовки, первые 2 КБ тела без секретов, IP, DNS, этапы запроса, ошибка.",
    responses=NOT_FOUND,
)
async def read_incident(incident_id: int, user: CurrentUser, session: SessionDep) -> IncidentDetail:
    incident = await owned_incident(session, user.id, incident_id)
    site = await session.get(Site, incident.site_id)
    assert site is not None
    notes = list(
        await session.scalars(
            select(IncidentNote)
            .where(IncidentNote.incident_id == incident.id)
            .order_by(IncidentNote.id)
        )
    )
    diagnostics = list(
        await session.scalars(
            select(CheckDiagnostic)
            .where(CheckDiagnostic.incident_id == incident.id)
            .order_by(CheckDiagnostic.created_at)
            .limit(20)
        )
    )
    escalations = list(
        await session.scalars(
            select(IncidentEscalation.notified_at).where(
                IncidentEscalation.incident_id == incident.id
            )
        )
    )
    now = datetime.now(UTC)
    base = incident_response(incident, now)
    return IncidentDetail(
        **base.model_dump(),
        site_name=site.name,
        site_url=site.url,
        postmortem=incident.postmortem,
        notes=[
            IncidentNoteResponse(id=n.id, status=n.status, text=n.text, created_at=n.created_at)
            for n in notes
        ],
        diagnostics=[
            DiagnosticResponse(
                id=row.id,
                created_at=row.created_at,
                region=row.region,
                status_code=row.status_code,
                error=row.error,
                ip=row.ip,
                resolved=row.resolved or [],
                headers=row.headers or {},
                body=decrypt_text(row.body_encrypted) if row.body_encrypted else None,
                timings=row.timings or {},
            )
            for row in diagnostics
        ],
        timeline=build_timeline(incident, notes, escalations, now),
    )


@router.get(
    "/{incident_id}/notes",
    response_model=list[IncidentNoteResponse],
    responses=NOT_FOUND,
)
async def list_notes(
    incident_id: int, user: CurrentUser, session: SessionDep
) -> list[IncidentNoteResponse]:
    incident = await owned_incident(session, user.id, incident_id)
    rows = await session.scalars(
        select(IncidentNote)
        .where(IncidentNote.incident_id == incident.id)
        .order_by(IncidentNote.id)
    )
    return [
        IncidentNoteResponse(id=row.id, status=row.status, text=row.text, created_at=row.created_at)
        for row in rows
    ]


@router.post(
    "/{incident_id}/notes",
    response_model=NoteCreated,
    status_code=201,
    summary="Добавить заметку",
    description="Заметки публикуются на статус-страницах, куда входит монитор.",
    responses=NOT_FOUND,
)
async def create_note(
    incident_id: int, payload: NoteCreate, user: CurrentUser, session: SessionDep
) -> NoteCreated:
    incident = await owned_incident(session, user.id, incident_id)
    note = IncidentNote(incident_id=incident.id, status=payload.status, text=payload.text)
    session.add(note)
    await session.flush()
    audit.record(
        session,
        user.id,
        "incident.note",
        "incident",
        incident.id,
        [{"field": "status", "old": None, "new": payload.status}],
    )
    await session.commit()
    return NoteCreated(id=note.id)


@router.post(
    "/{incident_id}/acknowledge",
    response_model=IncidentResponse,
    summary="Взять инцидент в работу",
    description="Подтверждённый инцидент не эскалируется на дополнительные каналы.",
    responses=NOT_FOUND,
)
async def acknowledge(incident_id: int, user: CurrentUser, session: SessionDep) -> IncidentResponse:
    incident = await owned_incident(session, user.id, incident_id)
    if incident.acknowledged_at is None:
        incident.acknowledged_at = datetime.now(UTC)
        audit.record(session, user.id, "incident.acknowledge", "incident", incident.id)
        await publish(
            session,
            user.id,
            "site_updated",
            {"site_id": incident.site_id, "incident_id": incident.id, "acknowledged": True},
        )
        await session.commit()
    return incident_response(incident, datetime.now(UTC))


@router.put(
    "/{incident_id}/postmortem",
    response_model=IncidentResponse,
    summary="Сохранить постмортем",
    responses=NOT_FOUND,
)
async def save_postmortem(
    incident_id: int, payload: PostmortemUpdate, user: CurrentUser, session: SessionDep
) -> IncidentResponse:
    incident = await owned_incident(session, user.id, incident_id)
    incident.postmortem = payload.text.strip() or None
    audit.record(session, user.id, "incident.postmortem", "incident", incident.id)
    await session.commit()
    return incident_response(incident, datetime.now(UTC))
