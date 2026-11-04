import secrets
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import CurrentUser, SessionDep
from app.errors import ApiError
from app.models import ApiKey, AuditLog
from app.security import hash_refresh_token
from app.services.billing import current_plan, require_feature
from app.validation import RequestModel

router = APIRouter(prefix="/keys", tags=["keys"])


class KeyCreate(RequestModel):
    name: str = Field(min_length=1, max_length=100)
    scope: Literal["read", "write"] = "read"


class KeyCreated(BaseModel):
    id: int
    token: str = Field(description="Показывается один раз")
    prefix: str
    scope: str


class KeyResponse(BaseModel):
    id: int
    name: str
    prefix: str
    scope: str
    revoked: bool
    last_used_at: datetime | None
    created_at: datetime | None


@router.post(
    "",
    response_model=KeyCreated,
    status_code=201,
    summary="Выпустить API-ключ",
    description="Ключ показывается один раз и хранится только хешем. Поддерживает Idempotency-Key.",
)
async def create_key(payload: KeyCreate, user: CurrentUser, session: SessionDep) -> KeyCreated:
    _, plan = await current_plan(session, user)
    require_feature(plan, "api_keys")
    token = f"pw_{secrets.token_urlsafe(32)}"
    key = ApiKey(
        user_id=user.id,
        name=payload.name,
        scope=payload.scope,
        prefix=token[:12],
        token_hash=hash_refresh_token(token),
    )
    session.add(key)
    await session.flush()
    session.add(AuditLog(user_id=user.id, action="key.create", resource_id=key.id))
    await session.commit()
    return KeyCreated(id=key.id, token=token, prefix=key.prefix, scope=key.scope)


@router.get("", response_model=list[KeyResponse])
async def list_keys(
    user: CurrentUser,
    session: SessionDep,
    cursor: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
) -> list[KeyResponse]:
    _, plan = await current_plan(session, user)
    require_feature(plan, "api_keys")
    rows = list(
        await session.scalars(
            select(ApiKey)
            .where(ApiKey.id > cursor)
            .limit(limit)
            .where(ApiKey.user_id == user.id)
            .order_by(ApiKey.id)
        )
    )
    await session.commit()
    return [
        KeyResponse(
            id=row.id,
            name=row.name,
            prefix=row.prefix,
            scope=row.scope,
            revoked=row.revoked,
            last_used_at=row.last_used_at,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.delete("/{key_id}", status_code=204)
async def revoke_key(key_id: int, user: CurrentUser, session: SessionDep) -> None:
    key = await session.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id))
    if key is None:
        raise ApiError(404, "key_not_found", "Ключ не найден")
    key.revoked = True
    session.add(AuditLog(user_id=user.id, action="key.revoke", resource_id=key.id))
    await session.commit()
