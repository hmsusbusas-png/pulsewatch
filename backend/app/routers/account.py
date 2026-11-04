import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request, Response
from pydantic import EmailStr, Field
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.crypto import encrypt_text
from app.deps import CurrentUser, SessionDep
from app.errors import ApiError
from app.models import AccountToken, AuditLog, MailJob, RefreshToken, User
from app.pagination import PAGINATION_HEADERS, paginate
from app.schemas import AuditChange, AuditEntry, SessionResponse
from app.security import (
    access_claims,
    hash_password,
    hash_refresh_token,
    link_claims,
    signed_link,
    verify_password,
)
from app.services.billing import grant_trial
from app.services.devices import describe_agent
from app.services.rate_limit import allow_persistent
from app.validation import RequestModel

router = APIRouter(tags=["account"])


class PasswordChange(RequestModel):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)


class EmailChange(RequestModel):
    password: str = Field(min_length=1, max_length=72)
    email: EmailStr


class ResetRequest(RequestModel):
    email: EmailStr


class ResetConfirm(RequestModel):
    token: str = Field(min_length=20, max_length=2048)
    password: str = Field(min_length=8, max_length=72)


class TokenConfirm(RequestModel):
    token: str = Field(min_length=20, max_length=2048)


async def revoke_sessions(session: SessionDep, user_id: int) -> None:
    user = await session.get(User, user_id)
    if user:
        user.auth_version += 1
    await session.execute(delete(AccountToken).where(AccountToken.user_id == user_id))
    await session.execute(
        update(MailJob)
        .where(
            MailJob.user_id == user_id,
            MailJob.dedupe_key.startswith("account:"),
            MailJob.status == "pending",
        )
        .values(status="canceled", body="")
    )
    await session.execute(
        update(RefreshToken).where(RefreshToken.user_id == user_id).values(revoked=True)
    )


@router.post("/account/password", status_code=204)
async def change_password(payload: PasswordChange, user: CurrentUser, session: SessionDep) -> None:
    if not verify_password(payload.current_password, user.password_hash):
        raise ApiError(403, "invalid_password", "Неверный текущий пароль")
    user.password_hash = hash_password(payload.new_password)
    await revoke_sessions(session, user.id)
    session.add(
        AuditLog(
            user_id=user.id,
            action="account.password",
            resource_type="account",
            changes=[{"field": "password", "old": "••••••", "new": "••••••"}],
        )
    )
    await session.commit()


async def issue_token(session: SessionDep, user: User, purpose: str, address: str) -> None:
    await session.execute(
        delete(AccountToken).where(AccountToken.user_id == user.id, AccountToken.purpose == purpose)
    )
    token = signed_link(purpose, {"nonce": secrets.token_urlsafe(32)}, 1800)
    session.add(
        AccountToken(
            token_hash=hash_refresh_token(token),
            user_id=user.id,
            purpose=purpose,
            value=address,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
    )
    values = {
        "user_id": user.id,
        "recipient": address,
        "subject": "Pulsewatch: подтверждение",
        "body": encrypt_text(
            f"Подтвердите действие: {settings.frontend_base_url}/account/{purpose}"
            f"?token={token}\nСсылка действует 30 минут."
        ),
        "dedupe_key": f"account:{purpose}:{user.id}",
        "status": "pending",
        "attempts": 0,
        "next_attempt_at": datetime.now(UTC),
    }
    await session.execute(
        insert(MailJob)
        .values(**values)
        .on_conflict_do_update(index_elements=["dedupe_key"], set_=values)
    )


@router.post("/account/email", status_code=202)
async def change_email(
    payload: EmailChange, user: CurrentUser, session: SessionDep
) -> dict[str, bool]:
    if not verify_password(payload.password, user.password_hash):
        raise ApiError(403, "invalid_password", "Неверный пароль")
    address = payload.email.lower()
    if await session.scalar(select(User.id).where(User.email == address)):
        raise ApiError(409, "email_taken", "Этот email уже используется")
    await issue_token(session, user, "email", address)
    await session.commit()
    return {"sent": True}


async def consume_token(session: SessionDep, token: str, purpose: str) -> AccountToken:
    if link_claims(token, purpose) is None:
        raise ApiError(400, "invalid_token", "Ссылка недействительна или истекла")
    stored = await session.scalar(
        select(AccountToken)
        .where(
            AccountToken.token_hash == hash_refresh_token(token), AccountToken.purpose == purpose
        )
        .with_for_update()
    )
    if stored is None or stored.expires_at <= datetime.now(UTC):
        raise ApiError(400, "invalid_token", "Ссылка недействительна или истекла")
    await session.delete(stored)
    return stored


@router.post("/account/email/confirm", status_code=204)
async def confirm_email(payload: TokenConfirm, session: SessionDep) -> None:
    token = await consume_token(session, payload.token, "email")
    user = await session.get(User, token.user_id)
    if user is None or token.value is None:
        raise ApiError(400, "invalid_token", "Аккаунт недоступен")
    if await session.scalar(select(User.id).where(User.email == token.value, User.id != user.id)):
        raise ApiError(409, "email_taken", "Этот email уже используется")
    session.add(
        AuditLog(
            user_id=user.id,
            action="account.email",
            resource_type="account",
            changes=[{"field": "email", "old": user.email, "new": token.value}],
        )
    )
    user.email = token.value
    user.email_verified_at = datetime.now(UTC)
    await revoke_sessions(session, user.id)
    await session.commit()


@router.post("/auth/password/reset", status_code=202)
async def request_reset(payload: ResetRequest, session: SessionDep) -> dict[str, bool]:
    if not await allow_persistent(f"reset:{payload.email.lower()}", 3, 3600):
        raise ApiError(429, "rate_limited", "Повторите запрос позже")
    user = await session.scalar(select(User).where(User.email == payload.email.lower()))
    if user:
        await issue_token(session, user, "reset", user.email)
        await session.commit()
    return {"sent": True}


@router.post("/auth/password/confirm", status_code=204)
async def confirm_reset(payload: ResetConfirm, session: SessionDep) -> None:
    token = await consume_token(session, payload.token, "reset")
    user = await session.get(User, token.user_id)
    if user is None:
        raise ApiError(400, "invalid_token", "Аккаунт недоступен")
    user.password_hash = hash_password(payload.password)
    user.email_verified_at = datetime.now(UTC)
    await grant_trial(session, user)
    await revoke_sessions(session, user.id)
    await session.commit()


@router.get(
    "/account/sessions",
    response_model=list[SessionResponse],
    summary="Активные сессии",
    description="Браузер и ОС разобраны из User-Agent; IP хранится хешем, показывается маской.",
    responses={200: {"headers": PAGINATION_HEADERS}},
)
async def sessions(
    request: Request,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    cursor: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
) -> list[SessionResponse]:
    rows = list(
        await session.scalars(
            select(RefreshToken)
            .where(
                RefreshToken.id > cursor,
                RefreshToken.user_id == user.id,
                RefreshToken.revoked.is_(False),
                RefreshToken.expires_at > datetime.now(UTC),
            )
            .order_by(RefreshToken.id)
            .limit(limit)
        )
    )
    paginate(request, response, rows, limit, lambda row: row.id)
    _, _, token = request.headers.get("authorization", "").partition(" ")
    claims = access_claims(token) or {}
    current = claims.get("sid")
    result = []
    for row in rows:
        device = describe_agent(row.user_agent)
        result.append(
            SessionResponse(
                id=row.id,
                browser=device.browser,
                os=device.os,
                ip=row.ip_mask,
                created_at=row.started_at,
                last_used_at=row.last_used_at,
                expires_at=row.expires_at,
                current=current is not None and row.family == current,
            )
        )
    return result


@router.delete("/account/sessions", status_code=204)
async def logout_all(user: CurrentUser, session: SessionDep) -> None:
    await revoke_sessions(session, user.id)
    await session.commit()


@router.delete("/account/sessions/{session_id}", status_code=204)
async def revoke_session(session_id: int, user: CurrentUser, session: SessionDep) -> None:
    token = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.id == session_id, RefreshToken.user_id == user.id)
        .with_for_update()
    )
    if token is None:
        raise ApiError(404, "session_not_found", "Сессия не найдена")
    token.revoked = True
    session.add(
        AuditLog(
            user_id=user.id, action="session.revoke", resource_type="session", resource_id=token.id
        )
    )
    await session.commit()


@router.delete("/account", status_code=204)
async def delete_account(payload: EmailChange, user: CurrentUser, session: SessionDep) -> None:
    if (
        not verify_password(payload.password, user.password_hash)
        or payload.email.lower() != user.email
    ):
        raise ApiError(403, "invalid_password", "Подтвердите email и пароль аккаунта")
    await session.execute(delete(User).where(User.id == user.id))
    await session.commit()


@router.get(
    "/account/audit",
    response_model=list[AuditEntry],
    summary="Журнал действий",
    description="Новые записи сверху. cursor: id последней записи предыдущей страницы.",
    responses={200: {"headers": PAGINATION_HEADERS}},
)
async def audit(
    request: Request,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    cursor: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    resource_type: str | None = Query(None, max_length=30),
) -> list[AuditEntry]:
    statement = select(AuditLog).where(AuditLog.user_id == user.id)
    if cursor:
        statement = statement.where(AuditLog.id < cursor)
    if resource_type:
        statement = statement.where(AuditLog.resource_type == resource_type)
    rows = list(await session.scalars(statement.order_by(AuditLog.id.desc()).limit(limit)))
    paginate(request, response, rows, limit, lambda row: row.id)
    return [
        AuditEntry(
            id=row.id,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            changes=[AuditChange.model_validate(change) for change in row.changes or []],
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/auth/email/confirm", status_code=204)
async def confirm_signup(payload: ResetConfirm, session: SessionDep) -> None:
    token = await consume_token(session, payload.token, "signup")
    user = await session.get(User, token.user_id)
    if user is None:
        raise ApiError(400, "invalid_token", "Аккаунт недоступен")
    user.password_hash = hash_password(payload.password)
    user.email_verified_at = datetime.now(UTC)
    await grant_trial(session, user)
    await revoke_sessions(session, user.id)
    await session.commit()
