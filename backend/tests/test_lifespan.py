import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.errors import ApiError
from app.main import app, lifespan
from app.routers.health import ready


async def test_api_lifecycle_drains_embedded_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import main

    calls = []
    monkeypatch.setattr(main, "configure_logging", lambda level: None)
    monkeypatch.setattr(main, "start_scheduler", lambda: calls.append("start"))
    monkeypatch.setattr(main, "stop_scheduler", lambda: calls.append("stop"))

    async def polling() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            calls.append("canceled")

    monkeypatch.setattr(main, "poll_telegram_updates", polling)
    monkeypatch.setattr(main, "is_enabled", lambda: True)
    monkeypatch.setattr(settings, "embedded_worker", True)
    async with lifespan(app):
        await asyncio.sleep(0)
    assert calls == ["start", "stop", "canceled"]


async def test_ready_returns_503_on_database_failure() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = OperationalError("health probe", {}, ConnectionError())
    with pytest.raises(ApiError) as exc:
        await ready(session)
    assert exc.value.status_code == 503 and exc.value.code == "database_unavailable"


async def test_legacy_health_and_telegram_contract(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    health = await client.get("/api/health")
    assert health.status_code == 200 and health.json()["database"] == "ok"
    link = await client.get("/api/me/telegram/link", headers=auth_headers)
    assert link.status_code == 200
    assert set(link.json()) == {"link_token", "bot_username", "instructions"}
