import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import cast

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return cast(str, password_context.hash(password))


def verify_password(password: str, password_hash: str) -> bool:
    return cast(bool, password_context.verify(password, password_hash))


def create_access_token(user_id: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_minutes)).timestamp()),
    }
    return cast(str, jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm))


def decode_access_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
    if payload.get("type") != "access":
        return None
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.isdigit():
        return None
    return int(subject)


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_token_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.refresh_token_days)


def generate_link_token() -> str:
    return secrets.token_urlsafe(32)
