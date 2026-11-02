from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import text

from app.deps import SessionDep
from app.scheduler import scheduler
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def read_health(session: SessionDep) -> HealthResponse:
    database_status = "ok"
    try:
        await session.execute(text("select 1"))
    except Exception:
        database_status = "unavailable"
    scheduler_status = "running" if scheduler.running else "stopped"
    overall = "ok" if database_status == "ok" else "degraded"
    return HealthResponse(
        status=overall,
        database=database_status,
        scheduler=scheduler_status,
        timestamp=datetime.now(UTC),
    )
