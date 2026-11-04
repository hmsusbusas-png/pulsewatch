import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.errors import ERROR_CATALOG, ErrorResponse, Responses, install_error_handlers
from app.logging_conf import configure_logging
from app.routers import (
    account,
    admin,
    agents,
    alerting,
    auth,
    billing,
    channels,
    dashboard,
    export,
    health,
    heartbeat,
    incidents,
    insights,
    keys,
    me,
    pages,
    public,
    sites,
    stream,
    telegram,
    transfer,
)
from app.scheduler import start_scheduler, stop_scheduler
from app.services.events import hub
from app.services.telegram import is_enabled, poll_telegram_updates

logger = logging.getLogger("pulsewatch.app")

COMMON_RESPONSES: Responses = {
    401: {"model": ErrorResponse, "description": "Нет или истёк токен"},
    422: {"model": ErrorResponse, "description": "Ошибка проверки; поля перечислены в details"},
    429: {"model": ErrorResponse, "description": "Превышен лимит; см. Retry-After"},
    500: {"model": ErrorResponse, "description": "Внутренняя ошибка; передайте request_id"},
}

TAGS = [
    ("auth", "Регистрация, вход, refresh-ротация и сброс пароля."),
    ("me", "Профиль текущего пользователя."),
    (
        "sites",
        "Мониторы: CRUD, история проверок, ручной запуск, импорт, экспорт, массовые операции.",
    ),
    ("analytics", "Статистика за период, перцентили, суточные полосы аптайма, SLO."),
    ("incidents", "Инциденты: лента событий, диагностика, заметки, постмортем."),
    ("reports", "SLA-отчёты с исключением окон обслуживания."),
    ("heartbeat", "Публичные адреса пинга для cron-задач."),
    ("stream", "Поток событий Server-Sent Events."),
    ("channels", "Каналы уведомлений: Telegram, email, webhook, Slack, Discord, Teams, ntfy."),
    ("alerting", "Правила эскалации и шаблоны уведомлений."),
    ("status pages", "Управление статус-страницами и объявлениями."),
    ("public", "Публичные статус-страницы, RSS, SVG-бейдж, подписки."),
    ("regions", "Регионы проверок и API региональных агентов."),
    ("dashboard", "Сводка для главного экрана."),
    ("billing", "Тарифы, подписка, оплата, понижение тарифа."),
    ("account", "Сессии, журнал действий, смена email и пароля."),
    ("keys", "API-ключи для интеграций."),
    ("export", "CSV-выгрузки проверок и инцидентов."),
    ("admin", "Показатели сервиса и управление пользователями."),
    ("telegram", "Webhook и привязка Telegram-бота."),
    ("health", "Проверки живости и готовности."),
]

DESCRIPTION = """
API сервиса мониторинга доступности. Все пути начинаются с `/api`.

**Аутентификация.** `Authorization: Bearer <access JWT>` (15 минут, обновление через
`POST /api/auth/refresh` с HttpOnly-cookie) или `Bearer pw_…` для API-ключа тарифа Business
для разделов sites, dashboard, export, incidents, reports.

**Версионирование.** Пути не версионируются. Каждый ответ несёт `X-API-Version` с датой
ревизии контракта. Обратно совместимые изменения (новые поля, эндпоинты, значения) выходят
без смены версии; клиенты обязаны игнорировать неизвестные поля. Ломающие изменения
объявляются в CHANGELOG и сопровождаются новой датой версии.

**Пагинация.** Курсорная: `cursor` (id последнего элемента) и `limit`. Если страница
полная, ответ содержит `Link: <…>; rel="next"` и `X-Next-Cursor`.

**Идемпотентность.** POST, создающие сущности, принимают `Idempotency-Key` (до 255
символов, хранится 24 часа). Повтор с тем же телом возвращает сохранённый ответ и
`Idempotent-Replayed: true`; другое тело вызывает 422 `idempotency_key_reused`.

**Лимиты.** Ответы содержат `X-RateLimit-Limit`, `X-RateLimit-Remaining`,
`X-RateLimit-Reset` (unix-время); при 429 также `Retry-After`.

**Ошибки.** Единый формат `{code, message, details, request_id}`. `X-Request-ID`
принимается от клиента (`[A-Za-z0-9_.-]{1,64}`) и возвращается в каждом ответе.

| code | значение |
|---|---|
""" + "\n".join(f"| `{code}` | {text} |" for code, text in ERROR_CATALOG.items())


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    from app.config import validate_runtime

    validate_runtime()
    configure_logging(settings.log_level)
    if settings.embedded_worker:
        start_scheduler()
    telegram_task: asyncio.Task[None] | None = None
    if settings.embedded_worker and is_enabled() and not settings.public_url:
        telegram_task = asyncio.create_task(poll_telegram_updates(), name="telegram-polling")
        logger.info("telegram_polling_task_started")
    logger.info("application_started")
    try:
        yield
    finally:
        await hub.close()
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
        title="Pulsewatch API",
        version="2026-11-04",
        description=DESCRIPTION,
        openapi_tags=[{"name": name, "description": text} for name, text in TAGS],
        lifespan=lifespan,
        docs_url=None if settings.app_env == "production" else "/docs",
        redoc_url=None if settings.app_env == "production" else "/redoc",
        openapi_url=None if settings.app_env == "production" else "/openapi.json",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "Link",
            "X-Next-Cursor",
            "X-Request-ID",
            "X-API-Version",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "Retry-After",
            "Idempotent-Replayed",
        ],
    )

    from app.http_guard import HttpGuard
    from app.idempotency import IdempotencyGuard

    application.add_middleware(IdempotencyGuard)
    application.add_middleware(HttpGuard)
    install_error_handlers(application)
    for router in (
        auth.router,
        me.router,
        insights.router,
        transfer.router,
        sites.router,
        incidents.router,
        heartbeat.router,
        stream.router,
        dashboard.router,
        public.router,
        health.router,
        billing.router,
    ):
        application.include_router(router, prefix="/api", responses=COMMON_RESPONSES)
    if settings.app_env == "dev":
        from app.routers import dev_billing, dev_tools

        application.include_router(dev_billing.router, prefix="/api")
        application.include_router(dev_tools.router, prefix="/api", responses=COMMON_RESPONSES)
    for router in (
        channels.router,
        alerting.router,
        telegram.router,
        account.router,
        admin.router,
        keys.router,
        pages.router,
        export.router,
        agents.router,
    ):
        application.include_router(router, prefix="/api", responses=COMMON_RESPONSES)
    application.include_router(health.router, include_in_schema=False)
    return application


app = create_app()
