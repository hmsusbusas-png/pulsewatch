import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.config import settings
from app.errors import install_error_handlers
from app.logging_conf import configure_logging, request_id_var
from app.routers import auth, dashboard, health, me, public, sites
from app.scheduler import start_scheduler, stop_scheduler
from app.services.telegram import is_enabled, poll_telegram_updates

logger = logging.getLogger("pulsewatch.app")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    start_scheduler()
    telegram_task: asyncio.Task[None] | None = None
    if is_enabled():
        telegram_task = asyncio.create_task(poll_telegram_updates(), name="telegram-polling")
        logger.info("telegram_polling_task_started")
    logger.info("application_started")
    try:
        yield
    finally:
        stop_scheduler()
        if telegram_task is not None:
            telegram_task.cancel()
            try:
                await telegram_task
            except asyncio.CancelledError:
                logger.info("telegram_polling_task_cancelled")
        logger.info("application_stopped")


def create_app() -> FastAPI:
    application = FastAPI(
        title="pulsewatch",
        version="1.0.0",
        description="Uptime monitoring API: sites, checks, incidents and public status pages.",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def attach_request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id")
        request_id = incoming or f"{datetime.now(UTC).timestamp():.0f}-{id(request):x}"
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = request_id
        return response

    install_error_handlers(application)
    application.include_router(auth.router, prefix="/api")
    application.include_router(sites.router, prefix="/api")
    application.include_router(dashboard.router, prefix="/api")
    application.include_router(me.router, prefix="/api")
    application.include_router(public.router, prefix="/api")
    application.include_router(health.router, prefix="/api")
    return application


app = create_app()


def export_openapi() -> dict[str, Any]:
    return app.openapi()
