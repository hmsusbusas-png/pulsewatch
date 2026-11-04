import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.database import session_factory
from app.models import NotificationChannel, User
from app.security import hash_refresh_token
from app.services.telegram import bot_command, link_chat_by_token, receive_update
from tests.conftest import activate


@pytest.fixture(autouse=True)
def fake_username(monkeypatch: pytest.MonkeyPatch) -> None:
    async def username() -> str:
        return "PulsewatchTestBot"

    monkeypatch.setattr("app.routers.telegram.fetch_bot_username", username)


async def test_link_success_hash_and_replay(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post("/api/telegram/link", headers=auth_headers)
    assert created.status_code == 200
    data = created.json()
    assert data["ttl"] == 600 and data["url"].endswith(data["link_token"])
    async with session_factory() as session:
        user = await session.scalar(select(User))
        assert user and user.telegram_link_token == hash_refresh_token(data["link_token"])
    answers = await asyncio.gather(
        link_chat_by_token(data["link_token"], 123), link_chat_by_token(data["link_token"], 456)
    )
    assert sum(answer is not None for answer in answers) == 1
    assert await link_chat_by_token(data["link_token"], 789) is None
    status = (await client.get("/api/telegram/status", headers=auth_headers)).json()
    assert status["linked"]
    assert (await client.post("/api/telegram/link", headers=auth_headers)).status_code == 409
    assert (await client.delete("/api/telegram/link", headers=auth_headers)).status_code == 204
    assert not (await client.get("/api/telegram/status", headers=auth_headers)).json()["linked"]
    async with session_factory() as session:
        assert await session.scalar(select(func.count(NotificationChannel.id))) == 0


async def test_link_expired_unknown_replaced(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    old = (await client.post("/api/telegram/link", headers=auth_headers)).json()["link_token"]
    new = (await client.post("/api/telegram/link", headers=auth_headers)).json()["link_token"]
    assert await link_chat_by_token(old, 123) is None
    assert await link_chat_by_token("someone-elses-invented-code", 123) is None
    async with session_factory() as session:
        user = await session.scalar(select(User))
        assert user
        user.telegram_link_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert await link_chat_by_token(new, 123) is None


async def test_chat_cannot_take_another_account(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    first = (await client.post("/api/telegram/link", headers=auth_headers)).json()["link_token"]
    assert await link_chat_by_token(first, 123)
    other = await client.post(
        "/api/auth/register", json={"email": "second@example.com", "password": "supersecret1"}
    )
    await activate(client, "second@example.com", "supersecret1")
    other = await client.post(
        "/api/auth/login", json={"email": "second@example.com", "password": "supersecret1"}
    )
    second_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    second = (await client.post("/api/telegram/link", headers=second_headers)).json()["link_token"]
    assert await link_chat_by_token(second, 123) is None
    assert (await client.get("/api/telegram/status", headers=second_headers)).json()[
        "linked"
    ] is False


async def test_commands_and_mocked_api(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await client.post(
        "/api/sites", headers=auth_headers, json={"name": "API", "url": "https://8.8.8.8"}
    )
    site_id = created.json()["id"]
    token = (await client.post("/api/telegram/link", headers=auth_headers)).json()["link_token"]
    sent = []

    async def send(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    monkeypatch.setattr("app.services.telegram.send_message", send)
    await receive_update(
        {
            "message": {
                "chat": {"id": 123, "type": "private", "username": "owner"},
                "text": f"/start {token}",
            }
        }
    )
    assert len(sent) == 1
    assert "API" in await bot_command(123, "/sites")
    assert "1" in await bot_command(123, "/status")
    await bot_command(123, f"/pause {site_id}")
    assert (await client.get(f"/api/sites/{site_id}", headers=auth_headers)).json()["paused"]
    await bot_command(123, "/resume API")
    assert not (await client.get(f"/api/sites/{site_id}", headers=auth_headers)).json()["paused"]
    await bot_command(123, "/mute 8h")
    assert (await client.get("/api/telegram/status", headers=auth_headers)).json()["muted_until"]
    await bot_command(123, "/unlink")
    assert not (await client.get("/api/telegram/status", headers=auth_headers)).json()["linked"]
    assert "/status" in await bot_command(123, "/help")
