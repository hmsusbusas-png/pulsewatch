import asyncio

from app.config import settings
from app.database import engine
from app.logging_conf import configure_logging
from app.services.telegram import is_enabled, poll_telegram_updates, telegram_request


async def main() -> None:
    from app.config import validate_runtime
    from app.scheduler import heartbeat

    validate_runtime()
    configure_logging(settings.log_level)
    if not is_enabled():
        raise RuntimeError("Укажите BOT_TOKEN")
    try:
        if settings.public_url:
            if not settings.telegram_webhook_secret:
                raise RuntimeError("Укажите TELEGRAM_WEBHOOK_SECRET")
            response = await telegram_request(
                "setWebhook",
                {
                    "url": f"{settings.public_url.rstrip('/')}/api/telegram/webhook",
                    "secret_token": settings.telegram_webhook_secret,
                },
            )
            response.raise_for_status()
            while True:
                await heartbeat("bot")
                await asyncio.sleep(20)
        else:
            response = await telegram_request("deleteWebhook", {})
            response.raise_for_status()
            await poll_telegram_updates()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
