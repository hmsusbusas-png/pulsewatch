from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.deps import CurrentUser, SessionDep
from app.errors import ApiError, ErrorResponse, Responses
from app.models import EscalationRule, NotificationChannel, NotificationTemplate, Site
from app.services import audit
from app.services.billing import current_plan, require_feature
from app.services.templates import (
    DEFAULT_TEMPLATES,
    PLACEHOLDERS,
    SAMPLE_CONTEXT,
    render_template,
    validate_template,
)
from app.validation import RequestModel

router = APIRouter(tags=["alerting"])
TemplateEvent = Literal["down", "up", "degraded", "expiring"]
NOT_FOUND: Responses = {404: {"model": ErrorResponse}}


class RuleCreate(RequestModel):
    after_minutes: int = Field(ge=1, le=1440)
    channel_id: int
    site_id: int | None = Field(
        default=None, description="Без значения правило действует для всех мониторов"
    )
    enabled: bool = True


class RuleResponse(BaseModel):
    id: int
    after_minutes: int
    channel_id: int
    site_id: int | None
    enabled: bool
    created_at: datetime


class TemplateBody(RequestModel):
    body: str = Field(min_length=1, max_length=2000)


class TemplateResponse(BaseModel):
    event: str
    body: str
    custom: bool
    updated_at: datetime | None


class TemplatePreview(BaseModel):
    text: str
    placeholders: list[str]


def rule_response(rule: EscalationRule) -> RuleResponse:
    return RuleResponse(
        id=rule.id,
        after_minutes=rule.after_minutes,
        channel_id=rule.channel_id,
        site_id=rule.site_id,
        enabled=rule.enabled,
        created_at=rule.created_at,
    )


@router.get("/escalations", response_model=list[RuleResponse], tags=["alerting"])
async def list_rules(user: CurrentUser, session: SessionDep) -> list[RuleResponse]:
    rows = await session.scalars(
        select(EscalationRule).where(EscalationRule.user_id == user.id).order_by(EscalationRule.id)
    )
    return [rule_response(row) for row in rows]


@router.post(
    "/escalations",
    response_model=RuleResponse,
    status_code=201,
    summary="Правило эскалации",
    description="Если инцидент не закрыт и не взят в работу через N минут, канал получает "
    "отдельное уведомление о затянувшемся простое. Поддерживает Idempotency-Key.",
    responses={403: {"model": ErrorResponse}, **NOT_FOUND},
)
async def create_rule(payload: RuleCreate, user: CurrentUser, session: SessionDep) -> RuleResponse:
    _, plan = await current_plan(session, user)
    require_feature(plan, "escalations")
    channel = await session.scalar(
        select(NotificationChannel.id).where(
            NotificationChannel.id == payload.channel_id, NotificationChannel.user_id == user.id
        )
    )
    if channel is None:
        raise ApiError(404, "channel_not_found", "Канал не найден")
    if payload.site_id is not None and not await session.scalar(
        select(Site.id).where(Site.id == payload.site_id, Site.user_id == user.id)
    ):
        raise ApiError(404, "site_not_found", "Монитор не найден")
    rule = EscalationRule(
        user_id=user.id,
        site_id=payload.site_id,
        channel_id=payload.channel_id,
        after_minutes=payload.after_minutes,
        enabled=payload.enabled,
    )
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    audit.record(
        session,
        user.id,
        "escalation.create",
        "escalation",
        rule.id,
        [{"field": "after_minutes", "old": None, "new": payload.after_minutes}],
    )
    await session.commit()
    return rule_response(rule)


@router.delete("/escalations/{rule_id}", status_code=204, responses=NOT_FOUND)
async def delete_rule(rule_id: int, user: CurrentUser, session: SessionDep) -> None:
    result = await session.execute(
        delete(EscalationRule)
        .where(EscalationRule.id == rule_id, EscalationRule.user_id == user.id)
        .returning(EscalationRule.id)
    )
    if result.scalar() is None:
        raise ApiError(404, "rule_not_found", "Правило не найдено")
    audit.record(session, user.id, "escalation.delete", "escalation", rule_id)
    await session.commit()


@router.get(
    "/templates",
    response_model=list[TemplateResponse],
    summary="Шаблоны уведомлений",
    description="Подстановки: " + ", ".join(f"{{{name}}}" for name in PLACEHOLDERS) + ".",
)
async def templates(user: CurrentUser, session: SessionDep) -> list[TemplateResponse]:
    rows = {
        row.event: row
        for row in await session.scalars(
            select(NotificationTemplate).where(NotificationTemplate.user_id == user.id)
        )
    }
    return [
        TemplateResponse(
            event=event,
            body=rows[event].body if event in rows else default,
            custom=event in rows,
            updated_at=rows[event].updated_at if event in rows else None,
        )
        for event, default in DEFAULT_TEMPLATES.items()
    ]


@router.put(
    "/templates/{event}",
    response_model=TemplateResponse,
    responses={403: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def save_template(
    event: TemplateEvent, payload: TemplateBody, user: CurrentUser, session: SessionDep
) -> TemplateResponse:
    _, plan = await current_plan(session, user)
    require_feature(plan, "templates")
    body = validate_template(payload.body)
    now = datetime.now(UTC)
    await session.execute(
        insert(NotificationTemplate)
        .values(user_id=user.id, event=event, body=body, updated_at=now)
        .on_conflict_do_update(
            index_elements=["user_id", "event"], set_={"body": body, "updated_at": now}
        )
    )
    audit.record(
        session,
        user.id,
        "template.update",
        "template",
        None,
        [{"field": event, "old": None, "new": body[:200]}],
    )
    await session.commit()
    return TemplateResponse(event=event, body=body, custom=True, updated_at=now)


@router.delete("/templates/{event}", status_code=204)
async def reset_template(event: TemplateEvent, user: CurrentUser, session: SessionDep) -> None:
    await session.execute(
        delete(NotificationTemplate).where(
            NotificationTemplate.user_id == user.id, NotificationTemplate.event == event
        )
    )
    await session.commit()


@router.post(
    "/templates/preview",
    response_model=TemplatePreview,
    summary="Предпросмотр шаблона",
    description="Рендер на образцовых данных; неизвестные подстановки дают 422.",
)
async def preview_template(payload: TemplateBody, user: CurrentUser) -> TemplatePreview:
    body = validate_template(payload.body)
    return TemplatePreview(
        text=render_template(body, SAMPLE_CONTEXT), placeholders=list(PLACEHOLDERS)
    )
