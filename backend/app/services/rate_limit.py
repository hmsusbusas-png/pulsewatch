import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from app.database import session_factory
from app.models import RateBucket


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_at: int

    def __bool__(self) -> bool:
        return self.allowed

    def headers(self) -> dict[str, str]:
        values = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_at),
        }
        if not self.allowed:
            seconds = max(1, self.reset_at - int(datetime.now(UTC).timestamp()))
            values["Retry-After"] = str(seconds)
        return values


async def allow_persistent(key: str, limit: int, window_seconds: int = 60) -> RateDecision:
    now = datetime.now(UTC)
    window_start = int(now.timestamp()) // window_seconds * window_seconds
    started = datetime.fromtimestamp(window_start, UTC)
    digest = hashlib.sha256(key.encode()).hexdigest()
    async with session_factory() as session, session.begin():
        count = await session.scalar(
            insert(RateBucket)
            .values(key=digest, started_at=started, count=1)
            .on_conflict_do_update(
                index_elements=["key", "started_at"], set_={"count": RateBucket.count + 1}
            )
            .returning(RateBucket.count)
        )
        await session.execute(
            delete(RateBucket).where(RateBucket.started_at < now - timedelta(days=1))
        )
    used = count or limit + 1
    return RateDecision(
        allowed=used <= limit,
        limit=limit,
        remaining=max(0, limit - used),
        reset_at=window_start + window_seconds,
    )
