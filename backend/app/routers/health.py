import hmac
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.deps import SessionDep
from app.errors import ApiError
from app.models import NotificationDelivery, Site, WorkerHeartbeat
from app.scheduler import scheduler
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def ready(session: SessionDep) -> dict[str, str]:
    try:
        await session.execute(text("select 1"))
        seen = await session.scalar(
            select(WorkerHeartbeat.seen_at).where(WorkerHeartbeat.name == "worker")
        )
    except SQLAlchemyError as exc:
        raise ApiError(503, "database_unavailable", "База данных недоступна") from exc
    if seen is None or seen < datetime.now(UTC) - timedelta(seconds=90):
        raise ApiError(503, "worker_unavailable", "Воркер не отвечает")
    return {"status": "ok"}


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request, session: SessionDep) -> str:
    if not settings.metrics_token or not hmac.compare_digest(
        request.headers.get("authorization", ""), f"Bearer {settings.metrics_token}"
    ):
        raise ApiError(403, "metrics_forbidden", "Нужен токен метрик")
    sites = await session.scalar(select(func.count(Site.id)))
    queued = await session.scalar(
        select(func.count(NotificationDelivery.id)).where(NotificationDelivery.status == "pending")
    )
    return (
        f"# TYPE pulsewatch_sites gauge\npulsewatch_sites {sites}\n"
        "# TYPE pulsewatch_notifications_pending gauge\n"
        f"pulsewatch_notifications_pending {queued}\n"
    )


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
        environment=settings.app_env,
        timestamp=datetime.now(UTC),
    )
