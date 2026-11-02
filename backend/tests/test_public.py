from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.database import session_factory
from app.models import Incident, Site, User
from app.services.monitoring import process_probe_result
from app.services.probe import ProbeResult
from app.services.telegram import format_down_message, format_recovery_message, parse_start_token


async def seed_public_site(show_url: bool, with_incident: bool = True) -> tuple[str, int, str]:
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.email == "owner@example.com"))
        assert user is not None
        site = Site(
            user_id=user.id,
            name="Public Page",
            url="https://internal-host.example.com/secret-path",
            interval_seconds=60,
            is_public=True,
            show_url_publicly=show_url,
        )
        session.add(site)
        await session.commit()
        await session.refresh(site)
        site_id = site.id
        slug = str(site.public_slug)
        if with_incident:
            session.add(
                Incident(
                    site_id=site_id,
                    started_at=datetime.now(UTC) - timedelta(hours=2),
                    ended_at=datetime.now(UTC) - timedelta(hours=1),
                    reason="connection refused",
                )
            )
            await session.commit()
        return slug, site_id, user.email


async def test_public_status_page_is_accessible_without_auth(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    slug, _, _ = await seed_public_site(show_url=False)
    response = await client.get(f"/api/status/{slug}")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Public Page"
    assert body["status"] == "unknown"
    assert body["uptime_30d"] is None
    assert len(body["incidents"]) == 1


async def test_public_status_page_hides_url_by_default(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    slug, _, _ = await seed_public_site(show_url=False)
    response = await client.get(f"/api/status/{slug}")
    body = response.json()
    assert body["url"] is None
    assert "internal-host" not in response.text
    assert set(body.keys()) == {
        "name",
        "url",
        "status",
        "uptime_30d",
        "last_checked_at",
        "incidents",
        "updated_at",
    }
    assert "public_slug" not in body
    assert "user_id" not in body


async def test_public_status_page_reveals_url_when_owner_opts_in(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    slug, _, _ = await seed_public_site(show_url=True)
    response = await client.get(f"/api/status/{slug}")
    assert response.json()["url"] == "https://internal-host.example.com/secret-path"


async def test_public_status_page_requires_is_public_flag(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/sites",
        json={"name": "hidden", "url": "http://93.184.216.34/", "interval_seconds": 60},
        headers=auth_headers,
    )
    slug = created.json()["public_slug"]
    response = await client.get(f"/api/status/{slug}")
    assert response.status_code == 404
    assert response.json()["code"] == "status_page_not_found"


async def test_public_status_page_rejects_unknown_slug(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/status/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    malformed = await client.get("/api/status/not-a-uuid")
    assert malformed.status_code == 404


async def test_public_page_hides_incident_internals(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    slug, _, _ = await seed_public_site(show_url=False)
    body = (await client.get(f"/api/status/{slug}")).json()
    incident = body["incidents"][0]
    assert set(incident.keys()) == {"started_at", "ended_at", "duration_seconds", "reason"}
    assert incident["duration_seconds"] == pytest.approx(3600, abs=2)


async def test_public_page_reflects_live_status_after_failures(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    slug, site_id, _ = await seed_public_site(show_url=False, with_incident=False)
    await process_probe_result(site_id, ProbeResult(False, None, None, "timeout"))
    await process_probe_result(site_id, ProbeResult(False, None, None, "timeout"))
    body = (await client.get(f"/api/status/{slug}")).json()
    assert body["status"] == "down"
    assert len(body["incidents"]) == 1


def test_parse_start_token_variants() -> None:
    assert parse_start_token("/start abc123") == "abc123"
    assert parse_start_token("/start@PulsewatchBot abc123") == "abc123"
    assert parse_start_token("/start") is None
    assert parse_start_token("/help abc123") is None
    assert parse_start_token("/start a b") is None


def test_notification_message_formats() -> None:
    down = format_down_message("api.example.com", "connection refused", 2)
    assert "api.example.com" in down
    assert "connection refused" in down
    recovery = format_recovery_message("api.example.com", 180)
    assert "3 min" in recovery
    assert "api.example.com" in recovery


async def test_telegram_send_is_noop_without_token() -> None:
    from app.services import telegram

    assert telegram.is_enabled() is False
    await telegram.send_message(12345, "hello")
