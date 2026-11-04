import asyncio

import httpx
import pytest

from app.config import settings
from app.scheduler import heartbeat


async def test_worker_drains_checks_on_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import worker

    callbacks = []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, callback: callbacks.append(callback)
    )
    monkeypatch.setattr(worker, "configure_logging", lambda level: None)
    checks = []

    async def alive() -> None:
        await heartbeat()
        callbacks[0]()

    async def check() -> None:
        await asyncio.sleep(0)
        checks.append("drained")

    monkeypatch.setattr(worker, "heartbeat", alive)
    monkeypatch.setattr(worker, "run_due_checks", check)
    await worker.main()
    assert checks == ["drained"]


@pytest.mark.parametrize("public_url", ["", "https://public.example.org"])
async def test_bot_startup_mode(public_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import bot

    calls = []
    monkeypatch.setattr(settings, "public_url", public_url)
    monkeypatch.setattr(settings, "bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "test-webhook-secret")
    monkeypatch.setattr(bot, "configure_logging", lambda level: None)

    async def request(method: str, payload: dict[str, object]) -> httpx.Response:
        calls.append(method)
        return httpx.Response(
            200, json={"ok": True}, request=httpx.Request("POST", "https://api.telegram.org/")
        )

    async def poll() -> None:
        raise asyncio.CancelledError

    async def alive(name: str) -> None:
        assert name == "bot"
        raise asyncio.CancelledError

    monkeypatch.setattr(bot, "telegram_request", request)
    monkeypatch.setattr(bot, "poll_telegram_updates", poll)
    monkeypatch.setattr("app.scheduler.heartbeat", alive)
    with pytest.raises(asyncio.CancelledError):
        await bot.main()
    assert calls == ["setWebhook" if public_url else "deleteWebhook"]


async def test_healthcheck_uses_heartbeat() -> None:
    from app.healthcheck import healthy

    with pytest.raises(SystemExit):
        await healthy("worker")
    await heartbeat()
    await healthy("worker")
