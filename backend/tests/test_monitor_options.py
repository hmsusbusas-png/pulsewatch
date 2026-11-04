import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import settings
from app.models import Site
from app.services import probe
from app.services.monitors import in_maintenance
from tests.test_billing import set_plan


@pytest.mark.parametrize(
    "extra,code",
    [
        ({"headers": {"Host": "internal"}}, "invalid_headers"),
        ({"headers": {"Authorization": "x" * 8193}}, "headers_too_large"),
        ({"expected_codes": [99]}, "invalid_codes"),
        ({"monitor_type": "keyword"}, "keyword_required"),
        ({"monitor_type": "ssl-only", "url": "http://8.8.8.8"}, "https_required"),
        ({"tags": [" "]}, "invalid_tags"),
        ({"channel_ids": [999]}, "channel_not_found"),
    ],
)
async def test_monitor_validation(
    client: httpx.AsyncClient, auth_headers: dict[str, str], extra: dict[str, object], code: str
) -> None:
    response = await client.post(
        "/api/sites", headers=auth_headers, json={"name": "API", "url": "https://8.8.8.8", **extra}
    )
    assert response.status_code in (404, 422), response.text
    assert response.json()["code"] == code


async def test_secret_headers_and_filters(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/sites",
        headers=auth_headers,
        json={
            "name": "API",
            "url": "https://8.8.8.8",
            "headers": {"Authorization": "supersecret"},
            "tags": ["prod"],
            "group": "payments",
            "expected_codes": "200-299",
        },
    )
    assert created.status_code == 201
    site_id = created.json()["id"]
    assert created.json()["headers"] == {"Authorization": "********"}
    updated = await client.patch(
        f"/api/sites/{site_id}",
        headers=auth_headers,
        json={"headers": {"Authorization": "********"}, "expected_codes": "200-204"},
    )
    assert updated.status_code == 200
    filtered = await client.get(
        "/api/sites?search=api&tag=prod&group=payments&paused=false", headers=auth_headers
    )
    assert len(filtered.json()) == 1
    assert (await client.get(f"/api/sites?cursor={site_id}", headers=auth_headers)).json() == []
    from app.crypto import decrypt_config
    from app.database import session_factory

    async with session_factory() as session:
        site = await session.get(Site, site_id)
        assert site and site.headers_encrypted
        assert decrypt_config(site.headers_encrypted)["Authorization"] == "supersecret"


async def test_maintenance_validation_and_recurrence(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await set_plan("business")
    for window in (
        {"start": "bad", "end": "bad"},
        {"weekdays": "9", "start_time": "02:00", "end_time": "04:00"},
        {"weekdays": "1", "start_time": "02:00", "end_time": "04:00", "timezone": "missing-zone"},
    ):
        result = await client.post(
            "/api/sites",
            headers=auth_headers,
            json={"name": "API", "url": "https://8.8.8.8", "maintenance": [window]},
        )
        assert result.status_code == 422, result.text
    site = Site(
        maintenance=[
            {"weekdays": "0", "start_time": "23:00", "end_time": "02:00", "timezone": "UTC"}
        ]
    )
    assert in_maintenance(site, datetime(2026, 11, 3, 1, 0, tzinfo=UTC))
    assert not in_maintenance(site, datetime(2026, 11, 3, 3, 0, tzinfo=UTC))
    site.maintenance = [{"weekdays": "1", "start_time": "10:00", "end_time": "12:00"}]
    assert in_maintenance(site, datetime(2026, 11, 3, 11, 0, tzinfo=UTC))
    assert not in_maintenance(site, datetime(2026, 11, 3, 9, 0, tzinfo=UTC))


async def test_keyword_post_and_ssl_probe(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.database import session_factory

    calls = []

    async def request(*args: object, **kwargs: object) -> httpx.Response:
        calls.append(args)
        return httpx.Response(201, text="service ready")

    monkeypatch.setattr(probe, "safe_request", request)
    response = await client.post(
        "/api/sites",
        headers=auth_headers,
        json={
            "name": "API",
            "url": "https://8.8.8.8",
            "method": "POST",
            "body": "ping",
            "monitor_type": "keyword",
            "keyword": "ready",
            "expected_codes": [201],
            "headers": {"X-Key": "secret"},
            "timeout_seconds": 3,
        },
    )
    async with session_factory() as session:
        site = await session.get(Site, response.json()["id"])
        assert site
        assert (await probe.probe_monitor(site)).ok
        assert calls[0][2:6] == ("POST", {"X-Key": "secret"}, "ping", 3)
        site.keyword_present = False
        assert not (await probe.probe_monitor(site)).ok
        site.expected_codes = [200]
        assert not (await probe.probe_monitor(site)).ok
        site.monitor_type = "ssl-only"

        async def expiry(url: str) -> datetime:
            return datetime.now(UTC) + timedelta(days=1)

        monkeypatch.setattr(probe, "fetch_ssl_expiry", expiry)
        assert (await probe.probe_monitor(site)).ok


async def test_tcp_probe_real_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "allow_private_targets", True)

    async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    site = Site(monitor_type="tcp", url=f"http://127.0.0.1:{port}", timeout_seconds=2)
    try:
        assert (await probe.probe_monitor(site)).ok
    finally:
        server.close()
        await server.wait_closed()
    assert not (await probe.probe_monitor(site)).ok


async def test_ssl_dns_filter_and_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await probe.fetch_ssl_expiry("http://8.8.8.8") is None
    assert await probe.fetch_ssl_expiry("https://127.0.0.1") is None

    async def target(url: str, allow: bool) -> str:
        return "8.8.8.8"

    monkeypatch.setattr(probe, "target_ip", target)
    monkeypatch.setattr(
        probe, "_read_peer_certificate", lambda *args: {"notAfter": "Dec 31 23:59:59 2030 GMT"}
    )
    assert await probe.fetch_ssl_expiry("https://cert.example.org") == datetime(
        2030, 12, 31, 23, 59, 59, tzinfo=UTC
    )
    monkeypatch.setattr(probe, "_read_peer_certificate", lambda *args: {"notAfter": "bad"})
    assert await probe.fetch_ssl_expiry("https://cert.example.org") is None
