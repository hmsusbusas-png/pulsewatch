import asyncio
import logging
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.config import settings
from app.deps import SessionDep
from app.errors import ApiError
from app.models import AuditLog, RefreshToken, User
from app.schemas import (
    AccessTokenResponse,
    LoginRequest,
    RegisterRequest,
)
from app.security import (
    DUMMY_HASH,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    refresh_token_expiry,
    verify_password,
)
from app.services.devices import client_ip, hash_ip, mask_ip
from app.services.rate_limit import allow_persistent

logger = logging.getLogger("pulsewatch.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=settings.refresh_token_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
        domain=settings.cookie_domain,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        domain=settings.cookie_domain,
    )


async def _issue_refresh_token(
    session: SessionDep,
    user_id: int,
    request: Request,
    previous: RefreshToken | None = None,
) -> tuple[str, str]:
    token = generate_refresh_token()
    family = previous.family if previous and previous.family else secrets.token_hex(16)
    address = client_ip(request)
    session.add(
        RefreshToken(
            user_id=user_id,
            token_hash=hash_refresh_token(token),
            expires_at=refresh_token_expiry(),
            family=family,
            started_at=previous.started_at if previous else datetime.now(UTC),
            last_used_at=datetime.now(UTC),
            user_agent=(request.headers.get("user-agent") or "")[:300] or None,
            ip_hash=hash_ip(address),
            ip_mask=mask_ip(address),
        )
    )
    return token, family


@router.post("/register", status_code=202)
async def register(
    payload: RegisterRequest, request: Request, session: SessionDep
) -> dict[str, bool]:
    email = payload.email.lower()
    password_hash = await asyncio.to_thread(hash_password, payload.password)
    from sqlalchemy import text

    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:email))"), {"email": email})
    if not await allow_persistent(f"signup:{email}", 3, 3600):
        return {"accepted": True}
    existing = await session.scalar(select(User.id).where(User.email == email))
    user: User | None
    if existing is None:
        user = User(email=email, password_hash=password_hash)
        session.add(user)
        await session.flush()
    else:
        user = await session.get(User, existing)
    if user is not None and user.email_verified_at is None:
        from app.routers.account import issue_token

        await issue_token(session, user, "signup", email)
    await session.commit()
    return {"accepted": True}


@router.post("/login", response_model=AccessTokenResponse)
async def login(
    payload: LoginRequest, request: Request, response: Response, session: SessionDep
) -> AccessTokenResponse:
    if not await allow_persistent(f"login:{payload.email.lower()}", 10, 900):
        raise ApiError(429, "login_locked", "Слишком много попыток. Вход заблокирован на 15 минут")
    user = await session.scalar(select(User).where(User.email == payload.email.lower()))
    valid = await asyncio.to_thread(
        verify_password, payload.password, user.password_hash if user else DUMMY_HASH
    )
    if user is None or not valid or user.email_verified_at is None:
        raise ApiError(401, "invalid_credentials", "Email or password is incorrect")
    session.add(AuditLog(user_id=user.id, action="auth.login", resource_type="session"))
    refresh_token, family = await _issue_refresh_token(session, user.id, request)
    await session.commit()
    _set_refresh_cookie(response, refresh_token)
    logger.info("user_logged_in", extra={"extra_fields": {"user_id": user.id}})
    return AccessTokenResponse(access_token=create_access_token(user.id, user.auth_version, family))


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(request: Request, response: Response, session: SessionDep) -> AccessTokenResponse:
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if not raw_token:
        raise ApiError(401, "refresh_missing", "Refresh token cookie is missing")
    token_hash = hash_refresh_token(raw_token)
    stored = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    )
    if stored is None:
        _clear_refresh_cookie(response)
        raise ApiError(401, "refresh_invalid", "Refresh token is not recognised")

    if stored.revoked or stored.expires_at <= datetime.now(UTC):
        await _revoke_all_tokens(session, stored.user_id)
        await session.commit()
        _clear_refresh_cookie(response)
        logger.warning(
            "refresh_reuse_detected",
            extra={"extra_fields": {"user_id": stored.user_id, "token_id": stored.id}},
        )
        raise ApiError(
            401, "refresh_reused", "Refresh token was already used, all sessions revoked"
        )

    stored.revoked = True
    new_token, family = await _issue_refresh_token(session, stored.user_id, request, stored)
    await session.commit()
    _set_refresh_cookie(response, new_token)
    user = await session.get(User, stored.user_id)
    if user is None:
        raise ApiError(401, "unauthorized", "Аккаунт недоступен")
    return AccessTokenResponse(access_token=create_access_token(user.id, user.auth_version, family))


async def _revoke_all_tokens(session: SessionDep, user_id: int) -> None:
    user = await session.get(User, user_id)
    if user:
        user.auth_version += 1
    tokens = (
        await session.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False)
            )
        )
    ).all()
    for token in tokens:
        token.revoked = True


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, session: SessionDep) -> Response:
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if raw_token:
        token_hash = hash_refresh_token(raw_token)
        stored = await session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        if stored is not None and not stored.revoked:
            stored.revoked = True
            await session.commit()
    _clear_refresh_cookie(response)
    response.status_code = 204
    return response
