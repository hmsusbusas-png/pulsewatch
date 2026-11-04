import json
from collections.abc import Coroutine, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.database import session_factory
from app.models import Check, Incident, Region, Site
from tests.test_billing import set_plan
from tests.test_platform_api import create, seed_checks


async def test_slo_budget_burn_and_forecast(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    site_id = await create(client, auth_headers, slo_target=99.9)
    await seed_checks(site_id, 10)
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            Incident(
                site_id=site_id,
                reason="HTTP 503",
                started_at=now - timedelta(minutes=30),
                ended_at=now - timedelta(minutes=20),
            )
        )
        session.add(
            Incident(
                site_id=site_id,
                kind="degraded",
                reason="slow",
                started_at=now - timedelta(hours=3),
                ended_at=now - timedelta(hours=2),
            )
        )
        await session.commit()
    report = (await client.get(f"/api/sites/{site_id}/slo", headers=auth_headers)).json()
    assert report["target"] == 99.9 and report["window_days"] == 30
    assert report["budget_seconds"] == 2592 and abs(report["spent_seconds"] - 600) <= 2
    assert report["remaining_seconds"] == report["budget_seconds"] - report["spent_seconds"]
    assert report["burn_rate_1h"] > report["burn_rate_24h"] > 0
    assert report["exhausted"] is False and report["exhausted_at"] is not None
    assert report["allowed_downtime_month_seconds"] == 2592
    async with session_factory() as session:
        session.add(
            Incident(
                site_id=site_id,
                reason="HTTP 503",
                started_at=now - timedelta(days=2),
                ended_at=now - timedelta(days=2) + timedelta(hours=1),
            )
        )
        await session.commit()
    exhausted = (await client.get(f"/api/sites/{site_id}/slo", headers=auth_headers)).json()
    assert exhausted["exhausted"] is True and exhausted["remaining_percent"] == 0.0
    empty_site = await create(client, auth_headers, name="Empty")
    empty = (await client.get(f"/api/sites/{empty_site}/slo", headers=auth_headers)).json()
    assert empty["uptime"] is None and empty["target"] is None and empty["budget_seconds"] == 0


async def test_sla_report_excludes_maintenance(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await set_plan("business")
    now = datetime.now(UTC).replace(microsecond=0)
    start = now - timedelta(days=1)
    window = {
        "start": (now - timedelta(hours=6)).isoformat(),
        "end": (now - timedelta(hours=4)).isoformat(),
    }
    site_id = await create(client, auth_headers, slo_target=99.0, maintenance=[window])
    await seed_checks(site_id, 5)
    async with session_factory() as session:
        session.add(
            Incident(
                site_id=site_id,
                reason="HTTP 503",
                started_at=now - timedelta(hours=5),
                ended_at=now - timedelta(hours=3),
            )
        )
        await session.commit()
    report = await client.get(
        "/api/reports/sla",
        params={"from": start.isoformat(), "to": now.isoformat()},
        headers=auth_headers,
    )
    assert report.status_code == 200, report.text
    row = report.json()["sites"][0]
    assert row["maintenance_seconds"] == 7200
    assert abs(row["downtime_seconds"] - 3600) <= 2 and row["incidents"] == 1
    assert row["met"] is False and 95 < row["uptime"] < 96
    invalid = await client.get(
        "/api/reports/sla",
        params={"from": now.isoformat(), "to": start.isoformat()},
        headers=auth_headers,
    )
    assert invalid.status_code == 422
    stats = (await client.get(f"/api/sites/{site_id}/stats", headers=auth_headers)).json()
    assert stats["maintenance_spans"][0]["kind"] == "maintenance"
    assert stats["incident_spans"][0]["kind"] == "down"


def test_recurring_maintenance_spans_cross_midnight() -> None:
    from app.services.monitors import maintenance_spans, merge_spans, overlap_seconds

    start = datetime(2026, 11, 2, tzinfo=UTC)
    end = start + timedelta(days=7)
    windows = [
        {"weekdays": "0,3", "start_time": "23:00", "end_time": "01:00", "timezone": "Europe/Moscow"}
    ]
    spans = maintenance_spans(windows, start, end)
    assert len(spans) == 2
    assert all((finish - begin) == timedelta(hours=2) for begin, finish in spans)
    assert spans[0][0] == datetime(2026, 11, 2, 20, tzinfo=UTC)
    merged = merge_spans(
        [
            (start, start + timedelta(hours=2)),
            (start + timedelta(hours=1), start + timedelta(hours=3)),
        ]
    )
    assert merged == [(start, start + timedelta(hours=3))]
    assert overlap_seconds(merged, [(start + timedelta(hours=2), end)]) == 3600


async def test_daily_bars_count_incidents_and_downtime(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    site_id = await create(client, auth_headers)
    await seed_checks(site_id, 20)
    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(
            Incident(
                site_id=site_id,
                reason="HTTP 503",
                started_at=now - timedelta(minutes=10),
                ended_at=now - timedelta(minutes=5),
            )
        )
        await session.commit()
    daily = (await client.get(f"/api/sites/{site_id}/daily?days=30", headers=auth_headers)).json()
    assert len(daily["days"]) == 30
    today = daily["days"][-1]
    assert today["checks"] >= 1 and today["incidents"] == 1
    assert 299 <= today["downtime_seconds"] <= 301
    assert daily["days"][0]["uptime"] is None


async def test_resolver_uses_configured_nameservers(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import dns_probe

    seen: dict[str, object] = {}

    class Answer:
        def __init__(self, items: list[object]) -> None:
            self.items = items

        def __iter__(self) -> Iterator[object]:
            return iter(self.items)

    class Resolver:
        def __init__(self) -> None:
            self.nameservers: list[str] = []

        async def resolve(self, name: str, record_type: str, lifetime: float) -> Answer:
            seen.update(name=name, type=record_type, servers=self.nameservers)
            records = {
                "MX": [
                    SimpleNamespace(
                        preference=10, exchange=SimpleNamespace(to_text=lambda: "Mx.Example.org.")
                    )
                ],
                "TXT": [SimpleNamespace(strings=[b"v=spf1 ", b"-all"])],
                "A": [SimpleNamespace(to_text=lambda: "93.184.216.34")],
            }
            return Answer(records[record_type])

    monkeypatch.setattr(dns_probe.dns.asyncresolver, "Resolver", Resolver)
    monkeypatch.setattr(settings, "dns_resolvers", ["9.9.9.9"])
    assert await dns_probe.resolve_records("example.org", "MX", 2) == ["10 mx.example.org"]
    assert seen["servers"] == ["9.9.9.9"]
    assert await dns_probe.resolve_records("example.org", "TXT", 2) == ["v=spf1 -all"]
    assert await dns_probe.resolve_records("example.org", "A", 2) == ["93.184.216.34"]
    assert dns_probe.public_hostname("ПРИМЕР.рф") == "xn--e1afmkfd.xn--p1ai"


async def test_dns_timeouts_and_expected_values(monkeypatch: pytest.MonkeyPatch) -> None:
    import dns.exception
    import dns.resolver

    from app.services import dns_probe, probe

    errors = iter([dns.exception.Timeout(), dns.resolver.NoAnswer(), dns.exception.DNSException()])

    async def failing(name: str, record_type: str, lifetime: float) -> list[str]:
        raise next(errors)

    monkeypatch.setattr(dns_probe, "resolve_records", failing)
    site = Site(url="example.org", monitor_type="dns", dns_record_type="CNAME", timeout_seconds=1)
    messages = [str((await probe.probe_monitor(site)).error) for _ in range(3)]
    assert messages[0].startswith("Таймаут") and messages[1].startswith("Нет записей")
    assert messages[2].startswith("Ошибка DNS")

    async def ok(name: str, record_type: str, lifetime: float) -> list[str]:
        return ["edge.example.net"]

    monkeypatch.setattr(dns_probe, "resolve_records", ok)
    site.dns_expected = "EDGE.example.net."
    result = await probe.probe_monitor(site)
    assert result.ok and result.records == ("edge.example.net",)


def test_client_ip_respects_trusted_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.requests import HTTPConnection

    from app.services.devices import client_ip, hash_ip

    def connection(peer: str, forwarded: str) -> HTTPConnection:
        return HTTPConnection(
            {
                "type": "http",
                "client": (peer, 1234),
                "headers": [(b"x-forwarded-for", forwarded.encode())],
            }
        )

    assert client_ip(connection("172.18.0.5", "203.0.113.9")) == "172.18.0.5"
    monkeypatch.setattr(settings, "trusted_proxies", ["172.16.0.0/12"])
    assert client_ip(connection("172.18.0.5", "198.51.100.1, 203.0.113.9")) == "203.0.113.9"
    assert client_ip(connection("8.8.8.8", "203.0.113.9")) == "8.8.8.8"
    assert client_ip(connection("172.18.0.5", "garbage, 172.18.0.2")) == "garbage"
    assert hash_ip("203.0.113.9") != hash_ip("203.0.113.10") and hash_ip(None) is None


async def test_certificate_inspection_handles_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import certificates

    assert await certificates.inspect_certificate("http://example.org") is None

    async def target(url: str, allow: bool) -> str:
        return "8.8.8.8"

    def broken(hostname: str, port: int, address: str) -> certificates.CertificateReport:
        raise OSError("connection refused")

    monkeypatch.setattr(certificates, "target_ip", target)
    monkeypatch.setattr(certificates, "read_certificate", broken)
    assert await certificates.inspect_certificate("https://example.org") is None


async def test_scheduler_refreshes_certificate_and_domain(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import scheduler
    from app.services.certificates import CertificateReport
    from app.services.probe import ProbeResult

    expires = datetime.now(UTC) + timedelta(days=200)

    async def inspect(url: str) -> CertificateReport:
        return CertificateReport(
            expires, "Issuer", "example.org", "TLSv1.3", "AES", True, True, None
        )

    async def domain(hostname: str) -> datetime:
        return expires + timedelta(days=100)

    async def probe(site: Site) -> ProbeResult:
        return ProbeResult(True, 200, 10, None)

    monkeypatch.setattr(scheduler, "inspect_certificate", inspect)
    monkeypatch.setattr(scheduler, "domain_expiry", domain)
    monkeypatch.setattr(scheduler, "probe_monitor", probe)
    site_id = await create(client, auth_headers, check_domain=True)
    await scheduler.run_due_checks()
    async with session_factory() as session:
        site = await session.get(Site, site_id)
    assert site and site.ssl_details and site.ssl_details["issuer"] == "Issuer"
    assert site.domain_expires_at and site.domain_expires_at > expires
    assert site.lease_token is None and site.next_check_at > datetime.now(UTC)


async def test_agent_cycle_posts_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import agent
    from app.services.probe import ProbeResult, Timings

    job = {
        "site_id": 5,
        "lease": "lease-1",
        "monitor_type": "http",
        "url": "https://8.8.8.8",
        "method": "GET",
        "headers": {"X-Key": "v"},
        "body": None,
        "expected_codes": [200],
        "timeout_seconds": 3,
        "follow_redirects": True,
        "keyword": None,
        "keyword_present": True,
        "assertions": [],
        "tcp_send": None,
        "tcp_expect": None,
        "dns_record_type": None,
        "dns_expected": None,
    }
    posted: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Agent token"
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=[job])
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"accepted": 1})

    async def fake_probe(site: Site, headers: dict[str, str] | None = None) -> ProbeResult:
        assert headers == {"X-Key": "v"} and site.url == "https://8.8.8.8"
        return ProbeResult(True, 200, 42, None, Timings(dns_ms=1, ttfb_ms=30))

    monkeypatch.setattr(agent, "probe_monitor", fake_probe)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond),
        base_url="http://backend",
        headers={"Authorization": "Agent token"},
    ) as client:
        assert await agent.cycle(client) == 1
    results = posted[0]["results"]
    assert isinstance(results, list)
    result = results[0]
    assert result["response_ms"] == 42 and result["timings"]["ttfb_ms"] == 30
    monkeypatch.setattr(settings, "agent_token", "short")
    with pytest.raises(SystemExit):
        await agent.main()


async def test_demo_target_routes_and_certificates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    from app import demo_target

    monkeypatch.setattr(settings, "extra_ca_file", str(tmp_path / "shared" / "ca.pem"))
    monkeypatch.setattr(demo_target, "pause", _instant)
    service, expiring = demo_target.issue_certificates(tmp_path)
    leaf = x509.load_pem_x509_certificate(expiring[0].read_bytes())
    days = (leaf.not_valid_after_utc - datetime.now(UTC)).days
    assert days == 12 and (tmp_path / "shared" / "ca.pem").exists()
    key = load_pem_private_key(service[1].read_bytes(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    transport = httpx.ASGITransport(app=demo_target.internal)
    async with httpx.AsyncClient(transport=transport, base_url="http://demo") as client:
        assert (await client.get("/api/health")).json()["status"] == "ok"
        assert (await client.get("/shop")).status_code == 200
        down = await client.get("/payments/health")
        assert down.status_code == 503 and down.json()["accepting"] is False
        await client.post("/control/payments/up")
        assert (await client.get("/payments/health")).json()["accepting"] is True
        await client.post("/control/api/down")
        assert (await client.get("/api/health")).status_code == 503
        await client.post("/control/shop/down")
        assert (await client.get("/shop")).status_code == 502
        assert (await client.post("/control/nope/up")).status_code == 404
        assert (await client.get("/")).json()["targets"]["payments"] is True
    demo_target.STATE.update(payments=False, api=True, shop=True)
    public = httpx.ASGITransport(app=demo_target.public)
    async with httpx.AsyncClient(transport=public, base_url="http://demo") as client:
        assert (await client.post("/control/payments/up")).status_code == 404


async def _instant(shape_key: str) -> None:
    return None


async def test_seed_cli_creates_plans_and_regions(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import seed
    from app.models import Plan, User

    monkeypatch.setattr(
        settings,
        "region_agents",
        {"fra": {"name": "Франкфурт", "token": "t" * 40}, "bad": {"token": "short"}},
    )
    monkeypatch.setattr(settings, "demo_email", "starter@example.com")
    monkeypatch.setattr("sys.argv", ["seed"])
    calls: list[tuple[bool, str | None]] = []

    async def prepare(demo: bool, password: str | None) -> None:
        calls.append((demo, password))

    def run_now(coroutine: Coroutine[object, object, None]) -> None:
        coroutine.close()
        calls.append((False, "ran"))

    monkeypatch.setattr(seed, "prepare", prepare)
    monkeypatch.setattr(seed.asyncio, "run", run_now)
    seed.main()
    monkeypatch.setattr("sys.argv", ["seed", "--demo", "--password", "secret-pass-123"])
    seed.main()
    monkeypatch.undo()
    monkeypatch.setattr(
        settings,
        "region_agents",
        {"fra": {"name": "Франкфурт", "token": "t" * 40}, "bad": {"token": "short"}},
    )
    monkeypatch.setattr(settings, "demo_email", "starter@example.com")
    assert calls == [(False, "ran"), (False, "ran")]
    await seed.seed()
    async with session_factory() as session:
        regions = list(await session.scalars(select(Region)))
        plans = {plan.code: plan for plan in await session.scalars(select(Plan))}
        starter = await session.scalar(select(User).where(User.email == "starter@example.com"))
    assert [region.code for region in regions] == ["fra"] and regions[0].name == "Франкфурт"
    assert plans["business"].max_regions == 3 and plans["free"].features["heartbeat"]
    assert starter is not None and starter.email_verified_at is not None


async def test_retention_cleanup(client: httpx.AsyncClient, auth_headers: dict[str, str]) -> None:
    from app.models import CheckDiagnostic, IdempotencyKey, StreamToken, User
    from app.scheduler import cleanup

    site_id = await create(client, auth_headers)
    old = datetime.now(UTC) - timedelta(days=120)
    async with session_factory() as session:
        user = await session.scalar(select(User))
        assert user
        session.add(
            CheckDiagnostic(site_id=site_id, created_at=old, resolved=[], headers={}, timings={})
        )
        session.add(
            IdempotencyKey(
                scope="user:1",
                key="k",
                method="POST",
                path="/api/sites",
                request_hash="h",
                created_at=old,
            )
        )
        session.add(StreamToken(token_hash="x" * 64, user_id=user.id, expires_at=old))
        session.add(Check(site_id=site_id, checked_at=datetime.now(UTC), ok=True, response_ms=5))
        await session.commit()
    await cleanup()
    async with session_factory() as session:
        assert list(await session.scalars(select(CheckDiagnostic))) == []
        assert list(await session.scalars(select(IdempotencyKey))) == []
        assert list(await session.scalars(select(StreamToken))) == []


async def test_agent_main_loop_survives_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from app import agent

    calls = 0

    async def cycle(client: httpx.AsyncClient) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("backend restarting")
        if calls == 2:
            return 3
        raise asyncio.CancelledError

    monkeypatch.setattr(settings, "agent_token", "a" * 40)
    monkeypatch.setattr(agent, "IDLE_SECONDS", 0.01)
    monkeypatch.setattr(agent, "cycle", cycle)
    monkeypatch.setattr(agent, "configure_logging", lambda level: None)
    with pytest.raises(asyncio.CancelledError):
        await agent.main()
    assert calls == 3


async def test_dev_endpoint_fills_empty_account(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    filled = await client.post("/api/dev/demo-data", headers=auth_headers)
    assert filled.status_code == 201, filled.text
    assert filled.json()["monitors"] == 6 and "/api/heartbeat/hb_" in filled.json()["heartbeat_url"]
    sites = (await client.get("/api/sites", headers=auth_headers)).json()
    assert len(sites) == 6
    pages = (await client.get("/api/status-pages", headers=auth_headers)).json()
    assert pages[0]["slug"].startswith("demo-")
    again = await client.post("/api/dev/demo-data", headers=auth_headers)
    assert again.status_code == 409


async def test_demo_target_accepts_webhooks() -> None:
    from app import demo_target

    transport = httpx.ASGITransport(app=demo_target.public)
    async with httpx.AsyncClient(transport=transport, base_url="http://demo") as client:
        response = await client.post("/hooks/slack", json={"text": "hi"})
    assert response.json() == {"ok": True}
