import hmac
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.errors import ApiError
from app.models import ApiKey, User
from app.security import access_claims, hash_refresh_token

SessionDep = Annotated[AsyncSession, Depends(get_session)]
KEY_SECTIONS = {"sites", "dashboard", "export", "incidents", "reports"}


async def get_current_user(request: Request, session: SessionDep) -> User:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "unauthorized", "Access token is missing")
    user_id: int | None
    key = None
    claims = None
    if token.startswith("pw_"):
        key = await session.scalar(
            select(ApiKey).where(
                ApiKey.token_hash == hash_refresh_token(token), ApiKey.revoked.is_(False)
            )
        )
        if key is None or not hmac.compare_digest(key.token_hash, hash_refresh_token(token)):
            raise ApiError(401, "invalid_api_key", "API-ключ недействителен")
        if request.url.path.split("/")[2] not in KEY_SECTIONS:
            raise ApiError(403, "api_key_scope", "Ключ разрешён только для мониторинга и экспорта")
        if key.scope == "read" and request.method not in {"GET", "HEAD", "OPTIONS"}:
            raise ApiError(403, "api_key_scope", "API-ключ разрешает только чтение")
        user_id = key.user_id
    else:
        claims = access_claims(token)
        user_id = int(str(claims["sub"])) if claims else None
    if user_id is None:
        raise ApiError(401, "unauthorized", "Access token is invalid or expired")
    user = await session.get(User, user_id)
    if (
        user is None
        or user.email_verified_at is None
        or (claims is not None and claims["ver"] != user.auth_version)
    ):
        raise ApiError(401, "unauthorized", "User no longer exists")
    if key:
        from app.services.billing import current_plan, require_feature

        _, plan = await current_plan(session, user)
        require_feature(plan, "api_keys")
        await session.execute(
            update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=datetime.now(UTC))
        )
        await session.commit()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise ApiError(403, "admin_required", "Требуются права администратора")
    return user


AdminUser = Annotated[User, Depends(get_admin)]
