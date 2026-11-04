import asyncio
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.database import engine, session_factory
from app.models import WorkerHeartbeat


async def healthy(name: str) -> None:
    async with session_factory() as session:
        seen = await session.scalar(
            select(WorkerHeartbeat.seen_at).where(WorkerHeartbeat.name == name)
        )
        if seen is None or seen < datetime.now(UTC) - timedelta(seconds=90):
            raise SystemExit(1)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(healthy(sys.argv[1] if len(sys.argv) > 1 else "worker"))
