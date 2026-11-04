from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.database import session_factory
from app.models import Check, Incident, Site, SiteStatus
from app.schemas import SiteCreateRequest
from app.services.monitoring import (
    calculate_uptime,
    close_open_incident,
    open_incident,
    record_probe,
)
from app.services.probe import ProbeResult, probe_site
from app.ssrf import ensure_target_allowed


async def incidents_for(site_id: int) -> list[Incident]:
    async with session_factory() as session:
        return list(await session.scalars(select(Incident).where(Incident.site_id == site_id)))


async def make_site(name: str = "uptime", user_email: str = "owner@example.com") -> int:
    async with session_factory() as session:
        from app.models import User

        user = await session.scalar(select(User).where(User.email == user_email))
        assert user is not None
        site = Site(
            user_id=user.id,
            name=name,
            url="http://93.184.216.34/",
            interval_seconds=60,
        )
        session.add(site)
        await session.commit()
        await session.refresh(site)
        return site.id


def ok_result() -> ProbeResult:
    return ProbeResult(True, 200, 120, None)


def fail_result(message: str = "connection refused") -> ProbeResult:
    return ProbeResult(False, None, None, message)


async def test_single_failure_keeps_site_up_state(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    site_id = await make_site()
    await record_probe(site_id, fail_result())

    async with session_factory() as session:
        site = await session.get(Site, site_id)
        assert site is not None
        assert site.consecutive_failures == 1
        assert site.status is SiteStatus.unknown
        incident_count = await session.scalar(
            select(func.count(Incident.id)).where(Incident.site_id == site_id)
        )
        assert incident_count == 0


async def test_two_failures_open_incident_and_mark_down(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    site_id = await make_site()
    await record_probe(site_id, fail_result("timeout"))
    await record_probe(site_id, fail_result("timeout"))

    async with session_factory() as session:
        site = await session.get(Site, site_id)
        assert site is not None
        assert site.status is SiteStatus.down
        assert site.consecutive_failures == 2
        incidents = list(await session.scalars(select(Incident).where(Incident.site_id == site_id)))
        assert len(incidents) == 1
        assert incidents[0].ended_at is None
        assert incidents[0].reason == "timeout"


async def test_repeated_failures_do_not_duplicate_open_incident(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    site_id = await make_site()
    for _ in range(5):
        await record_probe(site_id, fail_result("server error"))

    async with session_factory() as session:
        open_incidents = (
            await session.scalars(
                select(Incident).where(Incident.site_id == site_id, Incident.ended_at.is_(None))
            )
        ).all()
        assert len(open_incidents) == 1
        total = await session.scalar(
            select(func.count(Incident.id)).where(Incident.site_id == site_id)
        )
        assert total == 1


async def test_recovery_closes_incident(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    site_id = await make_site()
    await record_probe(site_id, fail_result())
    await record_probe(site_id, fail_result())
    await record_probe(site_id, ok_result())

    async with session_factory() as session:
        site = await session.get(Site, site_id)
        assert site is not None
        assert site.status is SiteStatus.up
        assert site.consecutive_failures == 0
        incidents = list(await session.scalars(select(Incident).where(Incident.site_id == site_id)))
        assert len(incidents) == 1
        assert incidents[0].ended_at is not None


async def test_new_failure_after_recovery_opens_second_incident(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    site_id = await make_site()
    await record_probe(site_id, fail_result())
    await record_probe(site_id, fail_result())
    await record_probe(site_id, ok_result())
    await record_probe(site_id, fail_result())
    await record_probe(site_id, fail_result())

    async with session_factory() as session:
        incidents = list(await session.scalars(select(Incident).where(Incident.site_id == site_id)))
        assert len(incidents) == 2
        open_incidents = [item for item in incidents if item.ended_at is None]
        assert len(open_incidents) == 1


async def test_concurrent_failures_create_single_incident(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    import asyncio

    site_id = await make_site()
    await asyncio.gather(
        record_probe(site_id, fail_result()),
        record_probe(site_id, fail_result()),
        record_probe(site_id, fail_result()),
    )

    async with session_factory() as session:
        total = await session.scalar(
            select(func.count(Incident.id)).where(Incident.site_id == site_id)
        )
        assert total == 1


async def test_open_incident_is_idempotent_at_db_level(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    site_id = await make_site()
    now = datetime.now(UTC)
    async with session_factory() as session:
        assert await open_incident(session, site_id, "first", now) is True
        await session.commit()
    async with session_factory() as session:
        assert await open_incident(session, site_id, "second", now) is False
        await session.commit()
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count(Incident.id)).where(Incident.site_id == site_id)
        )
        assert count == 1


async def test_close_open_incident_returns_start_time(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    site_id = await make_site()
    started = datetime.now(UTC) - timedelta(minutes=10)
    async with session_factory() as session:
        await open_incident(session, site_id, "boom", started)
        await session.commit()
    async with session_factory() as session:
        closed_start = await close_open_incident(session, site_id, datetime.now(UTC))
        await session.commit()
        assert closed_start is not None
        assert abs((closed_start - started).total_seconds()) < 1
    async with session_factory() as session:
        assert await close_open_incident(session, site_id, datetime.now(UTC)) is None


async def test_uptime_calculation(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    site_id = await make_site()
    async with session_factory() as session:
        now = datetime.now(UTC)
        for index in range(4):
            session.add(
                Check(
                    site_id=site_id,
                    checked_at=now - timedelta(minutes=index),
                    status_code=200 if index < 3 else 500,
                    response_ms=100,
                    ok=index < 3,
                    error=None if index < 3 else "unexpected status code 500",
                )
            )
        await session.commit()

    async with session_factory() as session:
        uptime = await calculate_uptime(session, site_id, datetime.now(UTC) - timedelta(hours=24))
        assert uptime == 75.0
        future_window = await calculate_uptime(
            session, site_id, datetime.now(UTC) + timedelta(hours=1)
        )
        assert future_window is None


async def test_uptime_is_none_without_checks(
    client: httpx.AsyncClient, registered_user: dict[str, str]
) -> None:
    site_id = await make_site()
    async with session_factory() as session:
        uptime = await calculate_uptime(session, site_id, datetime.now(UTC) - timedelta(hours=24))
        assert uptime is None


async def test_incidents_endpoint_lists_history(
    client: httpx.AsyncClient, registered_user: dict[str, str], auth_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/sites",
        json={"name": "hist", "url": "http://93.184.216.34/", "interval_seconds": 60},
        headers=auth_headers,
    )
    site_id = created.json()["id"]
    await record_probe(site_id, fail_result("dns failure"))
    await record_probe(site_id, fail_result("dns failure"))

    response = await client.get(f"/api/sites/{site_id}/incidents", headers=auth_headers)
    assert response.status_code == 200
    incidents = response.json()
    assert len(incidents) == 1
    assert incidents[0]["is_open"] is True
    assert incidents[0]["reason"] == "dns failure"
    assert incidents[0]["duration_seconds"] >= 0


async def test_probe_reports_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_request(url: str, allow_private: bool) -> object:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("app.services.probe._fetch_following_redirects", failing_request)
    result = await probe_site("http://93.184.216.34/", allow_private=False)
    assert result.ok is False
    assert result.status_code is None
    assert result.error == "connection refused"


async def test_probe_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def timing_out(url: str, allow_private: bool) -> object:
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr("app.services.probe._fetch_following_redirects", timing_out)
    result = await probe_site("http://93.184.216.34/", allow_private=False)
    assert result.ok is False
    assert result.error == "request timed out"


async def test_redirect_to_private_address_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import probe

    calls = []

    async def fake_target(url: str, allow_private: bool) -> str:
        if "127.0.0.1" in url:
            raise probe.RedirectRejected("target address is not allowed")
        return "93.184.216.34"

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            302, headers={"location": "http://127.0.0.1/internal"}, stream=httpx.ByteStream(b"")
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(probe, "target_ip", fake_target)
    monkeypatch.setattr(
        probe.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    result = await probe_site("http://93.184.216.34/", allow_private=False)
    assert not result.ok
    assert result.error and "not allowed" in result.error
    assert len(calls) == 1


async def test_redirect_to_private_allowed_when_flag_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import probe

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(
                302, headers={"location": "/internal"}, stream=httpx.ByteStream(b"")
            )
        return httpx.Response(200, stream=httpx.ByteStream(b""))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        probe.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    result = await probe_site("http://127.0.0.1/", allow_private=True)
    assert result.ok


async def test_private_target_allowed_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    await ensure_target_allowed("http://192.168.1.10/", allow_private=True)


def test_site_create_schema_validates_interval() -> None:
    with pytest.raises(ValueError):
        SiteCreateRequest(name="x", url="http://example.com", interval_seconds=29)
