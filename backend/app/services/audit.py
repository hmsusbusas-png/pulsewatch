import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

MASKED_FIELDS = {"headers", "body", "config", "secret", "password", "token", "pending_config"}
HIDDEN = "••••••"


def plain(value: object) -> object:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list | tuple):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    return value


def snapshot(entity: object, fields: set[str] | list[str] | tuple[str, ...]) -> dict[str, object]:
    return {name: plain(getattr(entity, name, None)) for name in fields}


def changes_between(before: dict[str, object], after: dict[str, object]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for name in sorted(after):
        old, new = before.get(name), after[name]
        if old == new:
            continue
        if name in MASKED_FIELDS or name.endswith("_encrypted"):
            old = HIDDEN if old else None
            new = HIDDEN if new else None
        result.append({"field": name.removesuffix("_encrypted"), "old": old, "new": new})
    return result


def record(
    session: AsyncSession,
    user_id: int,
    action: str,
    resource_type: str | None = None,
    resource_id: int | None = None,
    changes: list[dict[str, object]] | None = None,
) -> None:
    session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            changes=changes or None,
        )
    )
