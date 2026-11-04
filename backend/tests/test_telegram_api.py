import asyncio

import httpx
import pytest

from app.config import settings
from app.services import telegram


async def test_api_username_updates_and_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "bot_token", "test-bot-token")
    responses = [
        httpx.Response(200, json={"result": {"username": "PulsewatchTestBot"}}),
        httpx.Response(200, json={"result": [{"update_id": 1}]}),
        httpx.Response(503),
    ]
    real_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        assert "test-bot-token" in request.url.path
        return responses.pop(0)

    monkeypatch.setattr(
        telegram.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    assert await telegram.fetch_bot_username() == "PulsewatchTestBot"
    assert await telegram._fetch_updates(10) == [{"update_id": 1}]
    assert await telegram.fetch_bot_username() is None


async def test_send_429_retry_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "bot_token", "test-bot-token")
    responses = [httpx.Response(429, json={"parameters": {"retry_after": 0}}), httpx.Response(200)]

    async def request(method: str, payload: dict[str, object]) -> httpx.Response:
        assert method == "sendMessage" and payload["chat_id"] == 123
        response = responses.pop(0)
        response.request = httpx.Request("POST", "https://api.telegram.org/")
        return response

    monkeypatch.setattr(telegram, "telegram_request", request)
    await telegram.send_message(123, "test")
    assert responses == []


async def test_global_send_limiter_and_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "bot_token", "test-bot-token")
    real_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(
        telegram.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    response = await telegram.telegram_request("sendMessage", {"chat_id": 123, "text": "test"})
    assert response.status_code == 200


async def test_poll_dispatch_and_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    received = asyncio.Event()

    async def fetch(offset: int) -> list[dict[str, object]]:
        if offset:
            await asyncio.Event().wait()
        return [
            {"update_id": 10, "message": {"chat": {"id": 123, "type": "private"}, "text": "/help"}}
        ]

    async def send(chat_id: int, text: str) -> None:
        assert "/status" in text
        received.set()

    monkeypatch.setattr(telegram, "_fetch_updates", fetch)
    monkeypatch.setattr(telegram, "send_message", send)
    task = asyncio.create_task(telegram.poll_telegram_updates())
    await asyncio.wait_for(received.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await telegram.receive_update({})
    await telegram.receive_update({"message": {"chat": {"type": "group"}, "text": "/start token"}})
    assert "команда" in (
        await telegram.bot_command(123, "/unrecognized")
    ).lower() or "Сначала" in await telegram.bot_command(123, "/unrecognized")
