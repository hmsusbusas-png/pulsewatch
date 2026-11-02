import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.config import settings
from app.deps import SessionDep
from app.errors import ApiError
from app.models import RefreshToken, User
from app.schemas import (
    AccessTokenResponse,
    LoginRequest,
    RegisterRequest,
)
from app.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    refresh_token_expiry,
    verify_password,
)
from app.services.rate_limit import SlidingWindowLimiter

logger = logging.getLogger("pulsewatch.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
limiter = SlidingWindowLimiter(limit=settings.auth_rate_limit_per_minute)


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _enforce_rate_limit(request: Request) -> None:
    if not await limiter.allow(_client_key(request)):
        raise ApiError(429, "rate_limited", "Too many authentication attempts, try again later")


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=settings.refresh_token_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        domain=settings.cookie_domain,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        domain=settings.cookie_domain,
    )


async def _issue_refresh_token(session: SessionDep, user_id: int) -> str:
    token = generate_refresh_token()
    session.add(
        RefreshToken(
            user_id=user_id,
            token_hash=hash_refresh_token(token),
            expires_at=refresh_token_expiry(),
        )
    )
    return token


@router.post("/register", response_model=AccessTokenResponse, status_code=201)
async def register(
    payload: RegisterRequest, request: Request, response: Response, session: SessionDep
) -> AccessTokenResponse:
    await _enforce_rate_limit(request)
    email = payload.email.lower()
    existing = await session.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise ApiError(409, "email_taken", "An account with this email already exists")
    user = User(email=email, password_hash=hash_password(payload.password))
    session.add(user)
    await session.flush()
    refresh_token = await _issue_refresh_token(session, user.id)
    await session.commit()
    _set_refresh_cookie(response, refresh_token)
    logger.info("user_registered", extra={"extra_fields": {"user_id": user.id}})
    return AccessTokenResponse(access_token=create_access_token(user.id))


@router.post("/login", response_model=AccessTokenResponse)
async def login(
    payload: LoginRequest, request: Request, response: Response, session: SessionDep
) -> AccessTokenResponse:
    await _enforce_rate_limit(request)
    user = await session.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise ApiError(401, "invalid_credentials", "Email or password is incorrect")
    refresh_token = await _issue_refresh_token(session, user.id)
    await session.commit()
    _set_refresh_cookie(response, refresh_token)
    logger.info("user_logged_in", extra={"extra_fields": {"user_id": user.id}})
    return AccessTokenResponse(access_token=create_access_token(user.id))


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(request: Request, response: Response, session: SessionDep) -> AccessTokenResponse:
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if not raw_token:
        raise ApiError(401, "refresh_missing", "Refresh token cookie is missing")
    token_hash = hash_refresh_token(raw_token)
    stored = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
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
    new_token = await _issue_refresh_token(session, stored.user_id)
    await session.commit()
    _set_refresh_cookie(response, new_token)
    return AccessTokenResponse(access_token=create_access_token(stored.user_id))


async def _revoke_all_tokens(session: SessionDep, user_id: int) -> None:
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
