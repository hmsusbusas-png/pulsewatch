import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.database import session_factory
from app.errors import ApiError
from app.models import (
    Check,
    EscalationRule,
    Incident,
    IncidentEscalation,
    NotificationChannel,
    NotificationDelivery,
    Region,
    Site,
    SiteRegionState,
)
from app.services import probe
from app.services.assertions import (
    ensure_safe_pattern,
    evaluate_assertions,
    parse_path,
    validate_assertions,
)
from app.services.probe import ProbeResult
from tests.test_billing import set_plan
from tests.test_platform_api import PUBLIC_IP, create, second_user


def test_jsonpath_subset_and_operators() -> None:
    body = json.dumps(
        {"status": "ok", "items": [{"id": 1, "tags": ["a"]}, {"id": 2}], "meta": {"a-b": 5}}
    )
    items = validate_assertions(
        [
            {"source": "json", "path": "$.status", "op": "equals", "value": "ok"},
            {"source": "json", "path": "$.items[1].id", "op": "gt", "value": "1"},
            {"source": "json", "path": "$.items[*].id", "op": "contains", "value": "2"},
            {"source": "json", "path": "$['meta']['a-b']", "op": "lt", "value": "6"},
            {"source": "json", "path": "$.missing", "op": "exists", "value": ""},
            {"source": "header", "path": "Content-Type", "op": "regex", "value": "json$"},
            {"source": "body", "path": "", "op": "contains", "value": "tags"},
        ]
    )
    outcomes = evaluate_assertions(items, {"content-type": "application/json"}, body)
    assert [outcome.passed for outcome in outcomes] == [True, True, True, True, False, True, True]
    assert "не найдено" in outcomes[4].message
    assert parse_path("$.a[0]['b']") == ["a", 0, "b"]
    with pytest.raises(ValueError):
        parse_path("a.b")
    with pytest.raises(ValueError):
        ensure_safe_pattern("(a+)+$")
    with pytest.raises(ApiError):
        validate_assertions([{"source": "json", "path": "$..", "op": "equals", "value": "x"}])
    with pytest.raises(ApiError):
        validate_assertions([{"source": "json", "path": "$.a", "op": "regex", "value": "(\\w*)*"}])
    assert not evaluate_assertions(items[:1], {}, "not json")[0].passed


async def test_assertion_failure_marks_check_failed(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def request(*args: object, **kwargs: object) -> httpx.Response:
        return httpx.Response(200, json={"status": "degraded"})

    monkeypatch.setattr(probe, "safe_request", request)
    site_id = await create(
        client,
        auth_headers,
        assertions=[{"source": "json", "path": "$.status", "op": "equals", "value": "ok"}],
    )
    async with session_factory() as session:
        site = await session.get(Site, site_id)
        assert site
        result = await probe.probe_monitor(site)
    assert not result.ok and result.error and "degraded" in result.error
    from app.services.monitoring import record_probe

    await record_probe(site_id, result)
    preview = await client.post(
        f"/api/sites/{site_id}/assertions/preview",
        headers=auth_headers,
        json={
            "assertions": [
                {"source": "json", "path": "$.status", "op": "equals", "value": "degraded"}
            ]
        },
    )
    assert preview.json()["results"][0]["passed"] is True


async def test_assertions_require_plan(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await set_plan("free")
    response = await client.post(
        "/api/sites",
        headers=auth_headers,
        json={
            "name": "API",
            "url": PUBLIC_IP,
            "interval_seconds": 300,
            "assertions": [{"path": "$.ok", "op": "exists"}],
        },
    )
    assert response.status_code == 403 and response.json()["code"] == "plan_feature_assertions"


async def test_heartbeat_ping_fail_and_missed(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await set_plan("free")
    created = await client.post(
        "/api/sites",
        headers=auth_headers,
        json={
            "name": "Бэкап",
            "monitor_type": "heartbeat",
            "heartbeat_period_seconds": 3600,
            "heartbeat_grace_seconds": 300,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    token = body["heartbeat_token"]
    assert token.startswith("hb_") and body["heartbeat_prefix"] == token[:10]
    listed = (await client.get(f"/api/sites/{body['id']}", headers=auth_headers)).json()
    assert listed["heartbeat_token"] is None
    ping = await client.post(f"/api/heartbeat/{token}", params={"duration": 4200})
    assert ping.status_code == 200 and ping.json()["ok"]
    async with session_factory() as session:
        check = await session.scalar(select(Check).where(Check.site_id == body["id"]))
        assert check and check.ok and check.response_ms == 4200
    failed = await client.get(f"/api/heartbeat/{token}", params={"status": "fail"})
    assert failed.json()["ok"] is False
    assert (await client.get("/api/heartbeat/hb_unknown")).status_code == 404
    assert (await client.get("/api/heartbeat/plain")).status_code == 404
    rotated = await client.post(f"/api/sites/{body['id']}/heartbeat/rotate", headers=auth_headers)
    assert rotated.json()["heartbeat_token"] != token
    assert (await client.get(f"/api/heartbeat/{token}")).status_code == 404
    locked = await client.patch(
        f"/api/sites/{body['id']}", headers=auth_headers, json={"monitor_type": "http"}
    )
    assert locked.status_code == 422


async def test_scheduler_reports_missed_heartbeat(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    from app.scheduler import run_due_checks

    created = await client.post(
        "/api/sites",
        headers=auth_headers,
        json={"name": "cron", "monitor_type": "heartbeat", "heartbeat_period_seconds": 60},
    )
    site_id = created.json()["id"]
    async with session_factory() as session:
        site = await session.get(Site, site_id)
        assert site
        site.created_at = datetime.now(UTC) - timedelta(hours=1)
        site.next_check_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    await run_due_checks()
    async with session_factory() as session:
        site = await session.get(Site, site_id)
        incident = await session.scalar(select(Incident).where(Incident.site_id == site_id))
        assert site and site.status.value == "down"
        assert incident and incident.reason.startswith("Нет сигнала")
        assert site.next_check_at > datetime.now(UTC)


async def test_dns_monitor_records_and_change_alert(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import dns_probe
    from app.services.monitoring import record_probe

    answers = [["93.184.216.34"], ["93.184.216.34"], ["203.0.113.10"]]

    async def resolve(name: str, record_type: str, lifetime: float) -> list[str]:
        assert name == "example.org" and record_type == "A"
        return answers.pop(0)

    monkeypatch.setattr(dns_probe, "resolve_records", resolve)
    channel = await client.post(
        "/api/channels",
        headers=auth_headers,
        json={"type": "email", "name": "Mail", "config": {"email": "owner@example.com"}},
    )
    created = await client.post(
        "/api/sites",
        headers=auth_headers,
        json={
            "name": "DNS",
            "monitor_type": "dns",
            "url": "Example.org.",
            "dns_record_type": "A",
            "dns_expected": "93.184.216.34",
            "channel_ids": [channel.json()["id"]],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["url"] == "example.org"
    for _ in range(3):
        async with session_factory() as session:
            site = await session.get(Site, created.json()["id"])
            assert site
            result = await probe.probe_monitor(site)
        await record_probe(site.id, result)
    assert not result.ok and "203.0.113.10" in str(result.error)
    async with session_factory() as session:
        site = await session.get(Site, created.json()["id"])
        deliveries = list(await session.scalars(select(NotificationDelivery)))
    assert site and site.dns_values == ["203.0.113.10"]
    assert any(item.payload.get("event") == "dns_changed" for item in deliveries)
    for name in ("localhost", "printer.local", "single", "http://example.org", "db.internal"):
        rejected = await client.post(
            "/api/sites",
            headers=auth_headers,
            json={"name": "x", "monitor_type": "dns", "url": name, "dns_record_type": "A"},
        )
        assert rejected.status_code == 422, name


async def test_dns_failures_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    import dns.resolver

    from app.services import dns_probe

    async def missing(name: str, record_type: str, lifetime: float) -> list[str]:
        raise dns.resolver.NXDOMAIN

    monkeypatch.setattr(dns_probe, "resolve_records", missing)
    site = Site(url="example.org", monitor_type="dns", dns_record_type="MX", timeout_seconds=2)
    result = await probe.probe_monitor(site)
    assert not result.ok and result.error and result.error.startswith("NXDOMAIN")


async def test_tcp_banner_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "allow_private_targets", True)

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        writer.write(b"+PONG\r\n" if line == b"PING\r\n" else b"-ERR\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        site = Site(
            monitor_type="tcp",
            url=f"http://127.0.0.1:{port}",
            timeout_seconds=2,
            tcp_send="PING\\r\\n",
            tcp_expect="+PONG",
        )
        result = await probe.probe_monitor(site)
        assert result.ok and result.timings and result.timings.connect_ms is not None
        site.tcp_expect = "+OK"
        assert not (await probe.probe_monitor(site)).ok
    finally:
        server.close()
        await server.wait_closed()


async def test_http_probe_collects_phase_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = probe.PhaseClock()
    clock.restart(0.004, "8.8.8.8")
    marks = {
        "connection.connect_tcp.started": 1.0,
        "connection.connect_tcp.complete": 1.010,
        "connection.start_tls.started": 1.010,
        "connection.start_tls.complete": 1.040,
        "http11.send_request_headers.started": 1.040,
        "http11.receive_response_headers.complete": 1.140,
    }
    clock.marks.update(marks)
    clock.body_done = 1.150
    timings = clock.timings()
    assert timings.as_dict() == {
        "dns_ms": 4,
        "connect_ms": 10,
        "tls_ms": 30,
        "ttfb_ms": 100,
        "transfer_ms": 10,
    }
    await clock("custom.event", {})
    assert "custom.event" in clock.marks


async def test_anomaly_opens_and_closes_degradation(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    from app.services.monitoring import DEGRADATION_STREAK, RECOVERY_STREAK, record_probe

    site_id = await create(client, auth_headers)
    async with session_factory() as session:
        site = await session.get(Site, site_id)
        assert site
        site.baseline_ms, site.baseline_mad_ms = 100, 8
        await session.commit()
    for _ in range(DEGRADATION_STREAK):
        await record_probe(site_id, ProbeResult(True, 200, 340, None))
    view = (await client.get(f"/api/sites/{site_id}", headers=auth_headers)).json()
    assert view["degraded"] is True and view["status"] == "up"
    for _ in range(RECOVERY_STREAK):
        await record_probe(site_id, ProbeResult(True, 200, 105, None))
    async with session_factory() as session:
        incident = await session.scalar(select(Incident).where(Incident.kind == "degraded"))
    assert incident and incident.ended_at is not None


async def test_baseline_refresh_uses_median_and_mad(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    from app.scheduler import refresh_baselines

    site_id = await create(client, auth_headers)
    now = datetime.now(UTC)
    async with session_factory() as session:
        for index in range(60):
            session.add(
                Check(
                    site_id=site_id,
                    checked_at=now - timedelta(minutes=index),
                    ok=True,
                    response_ms=100 + (index % 5) * 2 + (900 if index == 3 else 0),
                    region=settings.worker_region,
                )
            )
        await session.commit()
    await refresh_baselines()
    async with session_factory() as session:
        site = await session.get(Site, site_id)
    assert site and site.baseline_ms == 104 and site.baseline_mad_ms in {2, 3, 4}


async def test_multi_region_requires_majority(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    from app.services.monitoring import record_probe

    await set_plan("business")
    async with session_factory() as session:
        for code in ("fra", "ams"):
            session.add(Region(code=code, name=code.upper(), token_hash=f"{code}-hash"))
        await session.commit()
    site_id = await create(client, auth_headers, regions=["fra", "ams"], failure_threshold=1)
    site = (await client.get(f"/api/sites/{site_id}", headers=auth_headers)).json()
    assert site["regions"] == [settings.worker_region, "fra", "ams"]
    await record_probe(site_id, ProbeResult(False, None, None, "timeout"), region="fra")
    view = (await client.get(f"/api/sites/{site_id}", headers=auth_headers)).json()
    assert view["status"] != "down"
    await record_probe(site_id, ProbeResult(False, None, None, "timeout"), region="ams")
    view = (await client.get(f"/api/sites/{site_id}", headers=auth_headers)).json()
    assert view["status"] == "down"
    states = {state["region"]: state["status"] for state in view["region_states"]}
    assert states["fra"] == "down" and states["ams"] == "down"
    async with session_factory() as session:
        incident = await session.scalar(select(Incident))
    assert incident and incident.reason.startswith("2 из 3 регионов")
    await record_probe(site_id, ProbeResult(True, 200, 80, None), region="fra")
    view = (await client.get(f"/api/sites/{site_id}", headers=auth_headers)).json()
    assert view["status"] == "up"
    unknown = await client.patch(
        f"/api/sites/{site_id}", headers=auth_headers, json={"regions": ["mars", "fra"]}
    )
    assert unknown.status_code == 422 and unknown.json()["code"] == "region_unknown"
    await set_plan("pro")
    limited = await client.patch(
        f"/api/sites/{site_id}", headers=auth_headers, json={"regions": ["fra", "ams"]}
    )
    assert limited.status_code in {402, 403}


async def test_agent_claim_and_results(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    from app.security import hash_refresh_token

    await set_plan("business")
    token = "agent-token-" + "x" * 32
    async with session_factory() as session:
        session.add(Region(code="fra", name="Frankfurt", token_hash=hash_refresh_token(token)))
        session.add(Region(code="ams", name="Amsterdam", token_hash="other"))
        await session.commit()
    site_id = await create(client, auth_headers, regions=["fra", "ams"])
    unauthorized = await client.post("/api/agent/claim", json={"limit": 5})
    assert unauthorized.status_code == 401
    wrong = await client.post(
        "/api/agent/claim", json={"limit": 5}, headers={"Authorization": "Agent " + "y" * 40}
    )
    assert wrong.status_code == 401
    agent = {"Authorization": f"Agent {token}"}
    jobs = (await client.post("/api/agent/claim", json={"limit": 5}, headers=agent)).json()
    assert [job["site_id"] for job in jobs] == [site_id]
    again = (await client.post("/api/agent/claim", json={"limit": 5}, headers=agent)).json()
    assert again == []
    stale = await client.post(
        "/api/agent/results",
        headers=agent,
        json={"results": [{"site_id": site_id, "lease": "forged", "ok": True}]},
    )
    assert stale.json()["accepted"] == 0
    accepted = await client.post(
        "/api/agent/results",
        headers=agent,
        json={
            "results": [
                {
                    "site_id": site_id,
                    "lease": jobs[0]["lease"],
                    "ok": True,
                    "status_code": 200,
                    "response_ms": 140,
                    "timings": {"dns_ms": 3, "ttfb_ms": 100},
                }
            ]
        },
    )
    assert accepted.json()["accepted"] == 1
    async with session_factory() as session:
        check = await session.scalar(select(Check).where(Check.region == "fra"))
        state = await session.get(SiteRegionState, (site_id, "fra"))
    assert check and check.ttfb_ms == 100 and state and state.lease_token is None
    regions = (await client.get("/api/regions", headers=auth_headers)).json()
    assert {region["code"] for region in regions} >= {settings.worker_region, "fra", "ams"}


async def test_new_channel_types_validate_format_and_ssrf(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.notifications import deliver_pending

    await set_plan("business")
    for kind, url in (
        ("slack", "https://127.0.0.1/hook"),
        ("discord", "https://10.0.0.5/api/webhooks/1"),
        ("teams", "https://169.254.169.254/webhook"),
        ("ntfy", "https://100.64.0.1/alerts"),
    ):
        blocked = await client.post(
            "/api/channels",
            headers=auth_headers,
            json={"type": kind, "name": kind, "config": {"url": url}},
        )
        assert blocked.status_code == 403, (kind, blocked.text)
    insecure = await client.post(
        "/api/channels",
        headers=auth_headers,
        json={"type": "slack", "name": "s", "config": {"url": "http://8.8.8.8/hook"}},
    )
    assert insecure.status_code == 422
    captured: list[tuple[str, bytes, object]] = []

    async def request(url: str, **kwargs: object) -> httpx.Response:
        body = kwargs["body"]
        assert isinstance(body, bytes)
        captured.append((url, body, kwargs["allow_private"]))
        return httpx.Response(200)

    monkeypatch.setattr("app.services.notifications.safe_request", request)
    ids = {}
    for kind, url in (
        ("slack", "https://8.8.8.8/services/T/B/x"),
        ("discord", "https://8.8.8.8/api/webhooks/1/abc"),
        ("teams", "https://8.8.8.8/workflows/1"),
        ("ntfy", "https://8.8.8.8/pulsewatch"),
    ):
        created = await client.post(
            "/api/channels",
            headers=auth_headers,
            json={"type": kind, "name": kind, "config": {"url": url}},
        )
        assert created.status_code == 201, created.text
        ids[kind] = created.json()["id"]
        assert created.json()["pending_verification"] is True
        verify = await client.post(f"/api/channels/{ids[kind]}/verify", headers=auth_headers)
        assert verify.status_code == 202
    async with session_factory() as session:
        await deliver_pending(session)
    bodies = {url: json.loads(body) for url, body, _ in captured}
    assert "blocks" in bodies["https://8.8.8.8/services/T/B/x"]
    assert bodies["https://8.8.8.8/api/webhooks/1/abc"]["allowed_mentions"] == {"parse": []}
    assert (
        bodies["https://8.8.8.8/workflows/1"]["attachments"][0]["content"]["type"] == "AdaptiveCard"
    )
    assert bodies["https://8.8.8.8/"]["topic"] == "pulsewatch"
    channels = (await client.get("/api/channels", headers=auth_headers)).json()
    assert all(channel["verified_at"] for channel in channels)
    assert all(private is False for _, _, private in captured)


async def test_channel_target_change_requires_verification(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.notifications import deliver_pending

    sent: list[str] = []

    async def send(address: str, subject: str, body: str) -> None:
        sent.append(f"{address}|{body}")

    monkeypatch.setattr("app.services.notifications.send_email", send)
    created = await client.post(
        "/api/channels",
        headers=auth_headers,
        json={"type": "email", "name": "Mail", "config": {"email": "owner@example.com"}},
    )
    channel_id = created.json()["id"]
    assert created.json()["verified_at"]
    changed = await client.patch(
        f"/api/channels/{channel_id}",
        headers=auth_headers,
        json={"config": {"email": "oncall@example.com"}},
    )
    assert changed.json()["pending_verification"] is True
    async with session_factory() as session:
        await deliver_pending(session)
        channel = await session.get(NotificationChannel, channel_id)
        assert channel and channel.pending_config
    assert sent[0].startswith("oncall@example.com|") and "/channels/verify?token=" in sent[0]
    token = sent[0].split("token=")[1].split()[0]
    assert (await client.post("/api/channels/verify", json={"token": "x" * 30})).status_code == 400
    confirmed = await client.post("/api/channels/verify", json={"token": token})
    assert confirmed.status_code == 200
    listed = (await client.get("/api/channels", headers=auth_headers)).json()[0]
    assert listed["target"].startswith("on") and listed["pending_verification"] is False
    other = await second_user(client)
    hijack = await client.patch(
        f"/api/channels/{channel_id}", headers=other, json={"config": {"email": "x@example.com"}}
    )
    assert hijack.status_code == 404


async def test_templates_render_and_require_business(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    denied = await client.put(
        "/api/templates/down", headers=auth_headers, json={"body": "{name} упал"}
    )
    assert denied.status_code == 403
    await set_plan("business")
    invalid = await client.put(
        "/api/templates/down", headers=auth_headers, json={"body": "{name} {__class__}"}
    )
    assert invalid.status_code == 422
    saved = await client.put(
        "/api/templates/down", headers=auth_headers, json={"body": "ALERT {name}: {error} ({code})"}
    )
    assert saved.json()["custom"] is True
    preview = await client.post(
        "/api/templates/preview", headers=auth_headers, json={"body": "{name}: {duration}"}
    )
    assert preview.json()["text"] == "Платёжный шлюз: 4 мин 20 с"
    listed = {
        row["event"]: row
        for row in (await client.get("/api/templates", headers=auth_headers)).json()
    }
    assert listed["down"]["custom"] and not listed["up"]["custom"]
    channel = await client.post(
        "/api/channels",
        headers=auth_headers,
        json={"type": "email", "name": "Mail", "config": {"email": "owner@example.com"}},
    )
    site_id = await create(
        client, auth_headers, channel_ids=[channel.json()["id"]], failure_threshold=1
    )
    from app.services.monitoring import record_probe

    await record_probe(site_id, ProbeResult(False, 503, 10, "HTTP 503"))
    async with session_factory() as session:
        delivery = await session.scalar(select(NotificationDelivery))
    assert delivery and delivery.payload["text"] == "ALERT API: HTTP 503 (503)"
    assert (await client.delete("/api/templates/down", headers=auth_headers)).status_code == 204


async def test_escalation_rules_notify_once(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    from app.scheduler import escalate_incidents

    channel = await client.post(
        "/api/channels",
        headers=auth_headers,
        json={"type": "email", "name": "Mail", "config": {"email": "owner@example.com"}},
    )
    channel_id = channel.json()["id"]
    site_id = await create(client, auth_headers)
    rule = await client.post(
        "/api/escalations",
        headers=auth_headers,
        json={"after_minutes": 10, "channel_id": channel_id},
    )
    assert rule.status_code == 201, rule.text
    other = await second_user(client)
    stolen = await client.post(
        "/api/escalations", headers=other, json={"after_minutes": 5, "channel_id": channel_id}
    )
    assert stolen.status_code in {403, 404}
    async with session_factory() as session:
        session.add(
            Incident(
                site_id=site_id,
                reason="HTTP 503",
                started_at=datetime.now(UTC) - timedelta(minutes=12),
            )
        )
        await session.commit()
    await escalate_incidents()
    await escalate_incidents()
    async with session_factory() as session:
        escalations = list(await session.scalars(select(IncidentEscalation)))
        deliveries = list(await session.scalars(select(NotificationDelivery)))
    assert len(escalations) == 1
    assert [item.payload["event"] for item in deliveries] == ["escalation"]
    listed = (await client.get("/api/escalations", headers=auth_headers)).json()
    assert listed[0]["after_minutes"] == 10
    assert (
        await client.delete(f"/api/escalations/{listed[0]['id']}", headers=auth_headers)
    ).status_code == 204
    async with session_factory() as session:
        assert list(await session.scalars(select(EscalationRule))) == []


async def test_expiry_alerts_follow_thresholds(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    from app.scheduler import expiry_alerts

    channel = await client.post(
        "/api/channels",
        headers=auth_headers,
        json={"type": "email", "name": "Mail", "config": {"email": "owner@example.com"}},
    )
    site_id = await create(client, auth_headers, channel_ids=[channel.json()["id"]])
    now = datetime.now(UTC)
    async with session_factory() as session:
        site = await session.get(Site, site_id)
        assert site
        site.ssl_expires_at = now + timedelta(days=12, hours=1)
        await expiry_alerts(session, site, now)
        await expiry_alerts(session, site, now)
        assert site.expiry_notified == {"ssl": 14}
        site.ssl_expires_at = now + timedelta(days=6)
        await expiry_alerts(session, site, now)
        assert site.expiry_notified == {"ssl": 7}
        site.ssl_expires_at = now + timedelta(days=90)
        await expiry_alerts(session, site, now)
        assert site.expiry_notified == {}
        await session.commit()
        deliveries = list(await session.scalars(select(NotificationDelivery)))
    assert len(deliveries) == 2 and all(item.payload["event"] == "expiring" for item in deliveries)


def test_certificate_report_and_domain_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    from app.services import certificates
    from app.services.domains import expiration_from, hostname_of, registrable_domain

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "shop.example.com")])
    issuer = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Demo Issuer")])
    expires = datetime.now(UTC).replace(microsecond=0) + timedelta(days=12)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(expires)
        .sign(key, hashes.SHA256())
    )
    der = certificate.public_bytes(serialization.Encoding.DER)
    calls: list[bool] = []

    def handshake(host: str, port: int, address: str, verify: bool) -> tuple[bytes, str, str]:
        calls.append(verify)
        if verify:
            error = certificates.ssl.SSLCertVerificationError("unable to get local issuer")
            error.verify_code = 20
            error.verify_message = "unable to get local issuer certificate"
            raise error
        return der, "TLSv1.3", "TLS_AES_128_GCM_SHA256"

    monkeypatch.setattr(certificates, "_handshake", handshake)
    report = certificates.read_certificate("shop.example.com", 443, "8.8.8.8")
    assert calls == [True, False]
    assert report.issuer == "Demo Issuer" and report.subject == "shop.example.com"
    assert report.chain_complete is False and report.trusted is False
    assert report.expires_at == expires and certificates.days_left(expires) in {11, 12}
    assert report.details()["tls_version"] == "TLSv1.3"
    assert registrable_domain("a.b.example.co.uk") == "example.co.uk"
    assert registrable_domain("api.example.com") == "example.com"
    assert hostname_of("https://shop.example.com:8443/x") == "shop.example.com"
    assert expiration_from(
        {"events": [{"eventAction": "expiration", "eventDate": "2027-01-31T10:00:00Z"}]}
    ) == datetime(2027, 1, 31, 10, tzinfo=UTC)
    assert expiration_from({"events": "bad"}) is None


async def test_rdap_lookup_uses_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import domains

    calls: list[str] = []

    async def request(url: str, **kwargs: object) -> httpx.Response:
        calls.append(url)
        if url == domains.BOOTSTRAP_URL:
            return httpx.Response(200, json={"services": [[["com"], ["https://rdap.test/"]]]})
        return httpx.Response(
            200,
            json={"events": [{"eventAction": "expiration", "eventDate": "2030-05-01T00:00:00Z"}]},
        )

    monkeypatch.setattr(domains, "safe_request", request)
    monkeypatch.setattr(domains, "_bootstrap", (0.0, {}))
    assert await domains.domain_expiry("www.example.com") == datetime(2030, 5, 1, tzinfo=UTC)
    assert calls[-1] == "https://rdap.test/domain/example.com"
    assert await domains.domain_expiry("example.ru") is None


def test_redaction_and_devices() -> None:
    from app.services.devices import describe_agent, mask_ip
    from app.services.redaction import redact_body, redact_headers

    assert redact_headers({"Authorization": "Bearer x", "X-Trace": "1"}) == {
        "authorization": "[redacted]",
        "x-trace": "1",
    }
    assert "s3cret" not in redact_body(b'{"user":{"password":"s3cret"},"n":1}')
    assert "s3cret" not in redact_body(b'oops "api_key": "s3cret" end')
    assert len(redact_body(b"x" * 10000)) == 2048
    assert describe_agent("curl/8.4.0").browser == "curl 8"
    edge = describe_agent(
        "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/131.0 Safari/537.36 Edg/131.0"
    )
    assert edge.browser == "Edge 131" and edge.os == "Windows 10/11"
    android = describe_agent("Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile Safari/537.36")
    assert android.os == "Android 14"
    assert mask_ip("203.0.113.77") == "203.0.113.x"
    assert mask_ip("2001:db8:85a3::8a2e:370:7334") == "2001:db8:85a3::x"
    assert mask_ip("::ffff:198.51.100.4") == "198.51.100.x"
    assert mask_ip("garbage") is None


async def test_demo_seed_is_idempotent() -> None:
    from sqlalchemy import func

    from app.demo import seed_demo
    from app.models import DailyUptime, StatusPage, User

    email, password, ping = await seed_demo("demo-password-2026")
    await seed_demo("demo-password-2026")
    async with session_factory() as session:
        demo = await session.scalar(select(User).where(User.email == email))
        assert demo and demo.is_admin
        sites = list(await session.scalars(select(Site).where(Site.user_id == demo.id)))
        assert len(sites) == 6
        assert {site.monitor_type for site in sites} == {
            "http",
            "keyword",
            "tcp",
            "ssl-only",
            "heartbeat",
        }
        incidents = list(
            await session.scalars(
                select(Incident).where(Incident.site_id.in_([site.id for site in sites]))
            )
        )
        kinds = sorted((item.kind, item.ended_at is None) for item in incidents)
        assert kinds == [("degraded", False), ("down", False), ("down", False), ("down", True)]
        assert await session.scalar(select(func.count(StatusPage.id))) == 1
        rollups = await session.scalar(select(func.count()).select_from(DailyUptime))
        assert rollups and rollups > 400
        users = await session.scalar(select(func.count(User.id)))
    assert users == 24 and ping.startswith(settings.api_base_url)
    assert password == "demo-password-2026"


async def test_demo_seed_refuses_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.demo import seed_demo

    monkeypatch.setattr(settings, "app_env", "test")
    with pytest.raises(SystemExit):
        await seed_demo(None)
