import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import cast

import bcrypt
import jwt

from app.config import settings
from app.errors import ApiError


def hash_password(password: str) -> str:
    if not 8 <= len(password.encode()) <= 72 or not password.strip():
        raise ApiError(422, "invalid_password", "Пароль: от 8 до 72 байт UTF-8")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def verify_password(password: str, password_hash: str) -> bool:
    encoded = password.encode()
    if len(encoded) > 72:
        return False
    try:
        return bcrypt.checkpw(encoded, password_hash.encode())
    except ValueError:
        return False


def create_access_token(user_id: int, version: int = 0, session_family: str | None = None) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": secrets.token_hex(16),
        "ver": version,
    }
    if session_family:
        claims["sid"] = session_family
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def access_claims(token: str) -> dict[str, object] | None:
    try:
        data = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iat", "iss", "aud", "jti", "sub", "ver"]},
        )
        if (
            data.get("type") != "access"
            or not str(data["sub"]).isdigit()
            or type(data["ver"]) is not int
            or not isinstance(data["jti"], str)
            or len(data["jti"]) < 16
            or data["exp"] - data["iat"] > 900
        ):
            return None
        return cast(dict[str, object], data)
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None


def decode_access_token(token: str) -> int | None:
    claims = access_claims(token)
    return int(str(claims["sub"])) if claims else None


def signed_link(purpose: str, values: dict[str, object], seconds: int) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            **values,
            "purpose": purpose,
            "iat": now,
            "exp": now + timedelta(seconds=seconds),
            "iss": settings.jwt_issuer,
            "aud": "pulsewatch-links",
            "jti": secrets.token_urlsafe(32),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def link_claims(token: str, purpose: str) -> dict[str, object] | None:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience="pulsewatch-links",
            options={"require": ["exp", "iat", "iss", "aud", "jti", "purpose"]},
        )
        return (
            cast(dict[str, object], claims)
            if hmac.compare_digest(str(claims["purpose"]), purpose)
            else None
        )
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def refresh_token_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.refresh_token_days)


def generate_link_token() -> str:
    return secrets.token_urlsafe(32)
