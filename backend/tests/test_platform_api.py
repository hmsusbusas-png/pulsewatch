import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.database import session_factory
from app.models import AuditLog, Check, CheckDiagnostic, Incident, IncidentNote, Site
from app.services.events import CLOSE, Event, event_stream, frame, hub
from app.services.probe import ProbeResult
from tests.conftest import activate
from tests.test_billing import set_plan

PUBLIC_IP = "https://8.8.8.8"


async def second_user(client: httpx.AsyncClient) -> dict[str, str]:
    await client.post(
        "/api/auth/register", json={"email": "intruder@example.com", "password": "supersecret1"}
    )
    await activate(client, "intruder@example.com", "supersecret1")
    login = await client.post(
        "/api/auth/login", json={"email": "intruder@example.com", "password": "supersecret1"}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def create(client: httpx.AsyncClient, headers: dict[str, str], **extra: object) -> int:
    response = await client.post(
        "/api/sites", headers=headers, json={"name": "API", "url": PUBLIC_IP, **extra}
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def seed_checks(site_id: int, count: int, ok: bool = True, minutes: int = 1) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        for index in range(count):
            session.add(
                Check(
                    site_id=site_id,
                    checked_at=now - timedelta(minutes=index * minutes),
                    status_code=200 if ok else 503,
                    response_ms=100 + index % 50 if ok else None,
                    ok=ok,
                    error=None if ok else "HTTP 503",
                    dns_ms=2,
                    connect_ms=5,
                    tls_ms=20,
                    ttfb_ms=60,
                    transfer_ms=4,
                )
            )
        await session.commit()


async def test_stats_ranges_use_raw_checks_and_rollups(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    site_id = await create(client, auth_headers)
    await seed_checks(site_id, 120)
    await seed_checks(site_id, 3, ok=False, minutes=7)
    for range_key, source in (("24h", "checks"), ("7d", "checks"), ("30d", "daily_rollups")):
        response = await client.get(
            f"/api/sites/{site_id}/stats", params={"range": range_key}, headers=auth_headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source"] == source
        assert body["checks"] == 123 and body["failures"] == 3
        assert body["p50_ms"] <= body["p95_ms"] <= body["p99_ms"]
        assert body["points"] and len(body["points"]) <= 300
    assert body["timings"]["ttfb_ms"] == 60
    assert (
        await client.get(f"/api/sites/{site_id}/stats?range=1y", headers=auth_headers)
    ).status_code == 422


async def test_checks_pagination_link_and_rate_headers(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    site_id = await create(client, auth_headers)
    await seed_checks(site_id, 5)
    first = await client.get(
        f"/api/sites/{site_id}/checks", params={"limit": 2, "order": "desc"}, headers=auth_headers
    )
    assert first.status_code == 200
    assert 'rel="next"' in first.headers["link"]
    assert first.headers["x-api-version"]
    assert int(first.headers["x-ratelimit-limit"]) == settings.api_rate_limit_per_minute
    assert int(first.headers["x-ratelimit-remaining"]) >= 0
    cursor = first.headers["x-next-cursor"]
    second = await client.get(
        f"/api/sites/{site_id}/checks",
        params={"limit": 2, "order": "desc", "cursor": cursor},
        headers=auth_headers,
    )
    assert {item["id"] for item in first.json()}.isdisjoint(item["id"] for item in second.json())
    assert first.json()[0]["ttfb_ms"] == 60


async def test_rotating_invalid_api_keys_cannot_reset_ip_limit(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "api_rate_limit_per_minute", 1)
    first = await client.get("/api/sites", headers={"Authorization": "Bearer pw_invalid-one"})
    assert first.status_code == 401
    second = await client.get("/api/sites", headers={"Authorization": "Bearer pw_invalid-two"})
    assert second.status_code == 429
    assert second.json()["code"] == "rate_limited"
    assert int(second.headers["retry-after"]) >= 1


async def test_unified_error_format_and_request_id(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/api/sites/999999", headers={**auth_headers, "X-Request-ID": "trace-42"}
    )
    assert response.status_code == 404
    assert response.json() == {
        "code": "site_not_found",
        "message": "Site does not exist",
        "details": None,
        "request_id": "trace-42",
    }
    invalid = await client.post("/api/sites", headers=auth_headers, json={"name": ""})
    assert invalid.status_code == 422
    assert invalid.json()["details"][0]["field"] == "name"


async def test_check_now_limits_per_site_and_user(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    site_id = await create(client, auth_headers)
    accepted = await client.post(f"/api/sites/{site_id}/check-now", headers=auth_headers)
    assert accepted.status_code == 202
    repeated = await client.post(f"/api/sites/{site_id}/check-now", headers=auth_headers)
    assert repeated.status_code == 429 and repeated.json()["code"] == "check_rate_limited"
    assert int(repeated.headers["retry-after"]) >= 1
    monkeypatch.setattr(settings, "check_now_per_user_per_minute", 1)
    other = await create(client, auth_headers, name="Second")
    limited = await client.post(f"/api/sites/{other}/check-now", headers=auth_headers)
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
    await client.patch(f"/api/sites/{site_id}", headers=auth_headers, json={"paused": True})
    paused = await client.post(f"/api/sites/{site_id}/check-now", headers=auth_headers)
    assert paused.status_code == 409


async def test_idempotency_replays_and_rejects_changed_body(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    headers = {**auth_headers, "Idempotency-Key": "create-api-1"}
    body = {"name": "API", "url": PUBLIC_IP}
    first = await client.post("/api/sites", headers=headers, json=body)
    second = await client.post("/api/sites", headers=headers, json=body)
    assert first.status_code == second.status_code == 201
    assert second.headers["idempotent-replayed"] == "true"
    assert first.json()["id"] == second.json()["id"]
    async with session_factory() as session:
        assert len(list(await session.scalars(select(Site)))) == 1
    changed = await client.post("/api/sites", headers=headers, json={**body, "name": "Other"})
    assert changed.status_code == 422 and changed.json()["code"] == "idempotency_key_reused"
    bad = await client.post(
        "/api/sites", headers={**auth_headers, "Idempotency-Key": "bad key\x7f"}, json=body
    )
    assert bad.status_code == 422


async def test_owner_isolation_for_new_endpoints(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    site_id = await create(client, auth_headers)
    async with session_factory() as session:
        incident = Incident(site_id=site_id, reason="private", started_at=datetime.now(UTC))
        session.add(incident)
        await session.commit()
        incident_id = incident.id
    other = await second_user(client)
    for method, path, payload in [
        ("GET", f"/api/sites/{site_id}/stats", None),
        ("GET", f"/api/sites/{site_id}/slo", None),
        ("GET", f"/api/sites/{site_id}/daily", None),
        ("POST", f"/api/sites/{site_id}/check-now", None),
        ("POST", f"/api/sites/{site_id}/heartbeat/rotate", None),
        ("POST", f"/api/sites/{site_id}/assertions/preview", {"assertions": [{"path": "$.a"}]}),
        ("GET", f"/api/incidents/{incident_id}", None),
        ("GET", f"/api/incidents/{incident_id}/notes", None),
        ("POST", f"/api/incidents/{incident_id}/acknowledge", None),
        ("PUT", f"/api/incidents/{incident_id}/postmortem", {"text": "x"}),
    ]:
        response = await client.request(method, path, headers=other, json=payload)
        assert response.status_code == 404, (path, response.text)
    assert (await client.get("/api/sites/daily", headers=other)).json() == []
    assert (await client.get("/api/incidents", headers=other)).json() == []
    bulk = await client.post(
        "/api/sites/bulk", headers=other, json={"ids": [site_id], "action": "delete"}
    )
    assert bulk.json()["results"][0] == {"id": site_id, "ok": False, "error": "site_not_found"}
    async with session_factory() as session:
        assert await session.get(Site, site_id) is not None


async def test_incident_detail_timeline_notes_ack_and_postmortem(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    site_id = await create(client, auth_headers, failure_threshold=2)
    from app.services.monitoring import record_probe
    from app.services.probe import Evidence, Timings

    evidence = Evidence(
        ip="8.8.8.8",
        headers={"Content-Type": "application/json", "Set-Cookie": "session=secret"},
        body=b'{"error":"down","password":"hunter2"}',
    )
    failure = ProbeResult(False, 503, 40, "HTTP 503", Timings(dns_ms=1, ttfb_ms=30), evidence)
    await record_probe(site_id, failure)
    await record_probe(site_id, failure)
    incidents = (await client.get(f"/api/sites/{site_id}/incidents", headers=auth_headers)).json()
    incident_id = incidents[0]["id"]
    note = await client.post(
        f"/api/incidents/{incident_id}/notes",
        headers=auth_headers,
        json={"status": "identified", "text": "Нашли причину"},
    )
    assert note.status_code == 201
    acknowledged = await client.post(
        f"/api/incidents/{incident_id}/acknowledge", headers=auth_headers
    )
    assert acknowledged.json()["acknowledged_at"]
    saved = await client.put(
        f"/api/incidents/{incident_id}/postmortem",
        headers=auth_headers,
        json={"text": "## Причина\nПул соединений"},
    )
    assert saved.status_code == 200
    await record_probe(site_id, ProbeResult(True, 200, 30, None))
    detail = (await client.get(f"/api/incidents/{incident_id}", headers=auth_headers)).json()
    kinds = [entry["kind"] for entry in detail["timeline"]]
    assert kinds[0] == "started" and "note" in kinds and kinds[-1] in {"resolved", "note"}
    assert detail["postmortem"].startswith("## Причина")
    diagnostic = detail["diagnostics"][0]
    assert diagnostic["headers"]["set-cookie"] == "[redacted]"
    assert "hunter2" not in diagnostic["body"] and diagnostic["timings"]["ttfb_ms"] == 30
    notes = (await client.get(f"/api/incidents/{incident_id}/notes", headers=auth_headers)).json()
    assert notes[0]["status"] == "identified"
    async with session_factory() as session:
        actions = set(await session.scalars(select(AuditLog.action)))
    assert {"incident.note", "incident.acknowledge", "incident.postmortem"} <= actions


async def test_sessions_parse_agent_mask_ip_and_mark_current(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/auth/register", json={"email": "owner@example.com", "password": "supersecret1"}
    )
    await activate(client, "owner@example.com", "supersecret1")
    agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
    )
    login = await client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "supersecret1"},
        headers={"User-Agent": agent},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    sessions = (await client.get("/api/account/sessions", headers=headers)).json()
    assert sessions[0]["browser"] == "Safari 17" and sessions[0]["os"] == "macOS 14.5"
    assert sessions[0]["current"] is True and sessions[0]["ip"].endswith(".x")
    refreshed = await client.post("/api/auth/refresh")
    headers = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
    again = (await client.get("/api/account/sessions", headers=headers)).json()
    assert (
        len(again) == 1
        and again[0]["current"]
        and again[0]["created_at"] == sessions[0]["created_at"]
    )


async def test_audit_records_field_changes_with_cursor(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    site_id = await create(client, auth_headers)
    await client.patch(
        f"/api/sites/{site_id}",
        headers=auth_headers,
        json={"name": "Renamed", "interval_seconds": 600, "headers": {"X-Token": "secret"}},
    )
    page = await client.get("/api/account/audit", params={"limit": 1}, headers=auth_headers)
    entry = page.json()[0]
    assert entry["action"] == "site.update"
    changes = {change["field"]: change for change in entry["changes"]}
    assert changes["name"]["old"] == "API" and changes["name"]["new"] == "Renamed"
    assert changes["interval_seconds"]["new"] == 600
    assert "secret" not in str(changes["headers"])
    following = await client.get(
        "/api/account/audit",
        params={"limit": 1, "cursor": page.headers["x-next-cursor"]},
        headers=auth_headers,
    )
    assert following.json()[0]["id"] < entry["id"]


async def test_timezone_preference(client: httpx.AsyncClient, auth_headers: dict[str, str]) -> None:
    updated = await client.patch("/api/me", headers=auth_headers, json={"timezone": "Asia/Tokyo"})
    assert updated.json()["timezone"] == "Asia/Tokyo"
    invalid = await client.patch("/api/me", headers=auth_headers, json={"timezone": "Mars/Base"})
    assert invalid.status_code == 422


async def test_email_links_point_to_frontend(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.crypto import decrypt_text
    from app.models import MailJob

    await client.post("/api/auth/password/reset", json={"email": "owner@example.com"})
    async with session_factory() as session:
        bodies = [
            decrypt_text(job.body) for job in await session.scalars(select(MailJob)) if job.body
        ]
    assert any(f"{settings.frontend_base_url}/account/reset?token=" in body for body in bodies)
    assert all("/api/" not in body for body in bodies)


async def test_dev_mail_is_logged_in_full(monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    import logging

    from app.logging_conf import JsonFormatter
    from app.services import notifications

    logged: list[dict[str, object]] = []

    class Recorder:
        def info(self, message: str, extra: dict[str, dict[str, object]]) -> None:
            logged.append(extra["dev_mail"])

    monkeypatch.setattr(notifications, "logger", Recorder())
    await notifications.send_email("reader@example.com", "Тема", "Ссылка: /account/reset?token=abc")
    assert logged[0]["body"] == "Ссылка: /account/reset?token=abc"
    record = logging.makeLogRecord({"msg": "dev_mail", "dev_mail": logged[0]})
    payload = json.loads(JsonFormatter().format(record))
    assert payload["mail"]["body"].endswith("token=abc")
    monkeypatch.setattr(settings, "app_env", "production")
    assert "mail" not in json.loads(JsonFormatter().format(record))


async def test_billing_downgrade_switches_at_period_end(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await set_plan("business")
    scheduled = await client.post(
        "/api/billing/downgrade", headers=auth_headers, json={"plan_code": "free"}
    )
    assert scheduled.status_code == 200
    assert scheduled.json()["scheduled_plan"] == "free"
    assert scheduled.json()["cancel_at_period_end"] is True
    from app.models import Subscription

    async with session_factory() as session:
        subscription = await session.scalar(select(Subscription))
        assert subscription
        subscription.current_period_end = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
    after = (await client.get("/api/billing/subscription", headers=auth_headers)).json()
    assert after["plan"]["code"] == "free" and after["status"] == "active"
    again = await client.post(
        "/api/billing/downgrade", headers=auth_headers, json={"plan_code": "free"}
    )
    assert again.status_code == 409


async def test_stream_token_is_single_use(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    issued = (await client.post("/api/stream/token", headers=auth_headers)).json()
    assert issued["expires_in"] == 60
    subscribed: list[int] = []

    async def fake_subscribe(user_id: int) -> asyncio.Queue[Event]:
        subscribed.append(user_id)
        queue: asyncio.Queue[Event] = asyncio.Queue()
        queue.put_nowait(CLOSE)
        return queue

    monkeypatch.setattr(hub, "subscribe", fake_subscribe)
    first = await client.get("/api/stream", params={"token": issued["token"]})
    assert first.status_code == 200 and first.headers["content-type"].startswith(
        "text/event-stream"
    )
    assert "event: ready" in first.text and "event: close" in first.text
    second = await client.get("/api/stream", params={"token": issued["token"]})
    assert second.status_code == 401
    assert len(subscribed) == 1


async def test_event_hub_dispatch_limit_and_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    async def listening() -> None:
        return None

    monkeypatch.setattr(hub, "_listen", listening)
    monkeypatch.setattr(settings, "stream_max_per_user", 1)
    queue = await hub.subscribe(77)
    from app.errors import ApiError

    with pytest.raises(ApiError):
        await hub.subscribe(77)
    hub.dispatch(77, "check_completed", {"site_id": 1})
    hub.dispatch(78, "check_completed", {"site_id": 2})
    event = queue.get_nowait()
    assert event.kind == "check_completed" and queue.empty()
    assert frame(event).startswith(f"id: {event.id}\nevent: check_completed\ndata: ")
    for _ in range(250):
        hub.dispatch(77, "check_completed", {"site_id": 1})
    kinds = []
    while not queue.empty():
        kinds.append(queue.get_nowait().kind)
    assert "resync" in kinds
    queue.put_nowait(CLOSE)
    chunks = [chunk async for chunk in event_stream(77, queue)]
    assert chunks[0].startswith("retry: 5000") and chunks[-1].startswith("event: close")
    assert hub.connections(77) == 0


async def test_publish_reaches_listener_through_postgres(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    from app.services.events import EventHub

    local = EventHub()
    queue = await local.subscribe(1)
    try:
        site_id = await create(client, auth_headers)
        from app.services.monitoring import record_probe

        await record_probe(site_id, ProbeResult(True, 200, 50, None))
        received = []
        for _ in range(3):
            received.append(await asyncio.wait_for(queue.get(), 5))
        kinds = {event.kind for event in received}
        assert "check_completed" in kinds
        completed = next(event for event in received if event.kind == "check_completed")
        assert completed.data["site_id"] == site_id and completed.data["response_ms"] == 50
    finally:
        await local.close()


async def test_bulk_import_export_and_plan_limits(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    first = await create(client, auth_headers)
    second = await create(client, auth_headers, name="Second")
    paused = await client.post(
        "/api/sites/bulk", headers=auth_headers, json={"ids": [first, second], "action": "pause"}
    )
    assert paused.json()["updated"] == 2
    tagged = await client.post(
        "/api/sites/bulk",
        headers=auth_headers,
        json={"ids": [first], "action": "set_tags", "tags": ["prod"]},
    )
    assert tagged.json()["results"][0]["ok"]
    exported = await client.get("/api/sites/export", headers=auth_headers)
    document = exported.json()
    assert len(document["monitors"]) == 2 and "headers" not in document["monitors"][0]
    csv_export = await client.get("/api/sites/export?format=csv", headers=auth_headers)
    assert csv_export.text.splitlines()[0].startswith("name,url,monitor_type")
    await set_plan("free")
    csv = "name,url,interval_seconds,tags\nShop,https://8.8.4.4,300,web;prod\nBad,ftp://x,300,\nThird,https://1.1.1.1,300,\nFourth,https://1.0.0.1,300,\n"
    report = (
        await client.post(
            "/api/sites/import", headers=auth_headers, json={"format": "csv", "content": csv}
        )
    ).json()
    statuses = [row["status"] for row in report["rows"]]
    assert statuses[0] == "created" and statuses[1] == "error"
    assert "plan_limit_sites" in report["rows"][3]["error"]
    dry = await client.post(
        "/api/sites/import",
        headers=auth_headers,
        json={
            "format": "json",
            "content": '[{"name": "X", "url": "http://127.0.0.1"}]',
            "dry_run": True,
        },
    )
    assert dry.json()["rows"][0]["status"] == "error"
    broken = await client.post(
        "/api/sites/import", headers=auth_headers, json={"format": "json", "content": "{"}
    )
    assert broken.status_code == 422 and broken.json()["code"] == "invalid_import"


async def test_import_dry_run_does_not_create(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    content = json.dumps(
        {
            "monitors": [
                {"name": "Ping", "monitor_type": "heartbeat", "heartbeat_period_seconds": 3600}
            ]
        }
    )
    report = (
        await client.post(
            "/api/sites/import",
            headers=auth_headers,
            json={"format": "json", "content": content, "dry_run": True},
        )
    ).json()
    assert report["valid"] == 1 and report["created"] == 0
    async with session_factory() as session:
        assert list(await session.scalars(select(Site))) == []


async def test_public_page_bars_overall_feed_and_badge(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    site_id = await create(client, auth_headers)
    await seed_checks(site_id, 30)
    page = await client.post(
        "/api/status-pages",
        headers=auth_headers,
        json={
            "title": "Сервисы",
            "slug": "services",
            "site_ids": [site_id],
            "show_urls": True,
            "accent": "violet",
            "logo_url": "https://cdn.example.com/logo.svg",
            "custom_domain": "status.example.com",
        },
    )
    assert page.status_code == 201, page.text
    page_id = page.json()["id"]
    announcement = await client.post(
        f"/api/status-pages/{page_id}/announcements",
        headers=auth_headers,
        json={"title": "Работы", "body": "<b>ночью</b>"},
    )
    assert announcement.status_code == 201
    async with session_factory() as session:
        incident = Incident(
            site_id=site_id,
            reason="secret reason",
            started_at=datetime.now(UTC) - timedelta(hours=2),
            ended_at=datetime.now(UTC) - timedelta(hours=1),
        )
        session.add(incident)
        await session.flush()
        session.add(IncidentNote(incident_id=incident.id, status="resolved", text="Готово & <ok>"))
        await session.commit()
    public = (await client.get("/api/public/pages/services")).json()
    assert public["overall"]["status"] == "operational"
    assert public["sites"][0]["url"] == PUBLIC_IP and len(public["sites"][0]["days"]) == 90
    assert public["announcements"][0]["body"] == "&lt;b&gt;ночью&lt;/b&gt;"
    assert public["accent"] == "violet" and "secret reason" not in str(public)
    listed = (await client.get("/api/status-pages", headers=auth_headers)).json()[0]
    assert listed["custom_domain"] == "status.example.com" and listed["cname_target"]
    feed = await client.get("/api/public/pages/services/feed.xml")
    assert feed.headers["content-type"].startswith("application/rss+xml")
    assert "Готово &amp; &lt;ok&gt;" in feed.text and '<rss version="2.0">' in feed.text
    badge = await client.get("/api/badge/services.svg")
    assert badge.headers["content-type"].startswith("image/svg+xml")
    assert badge.headers["cache-control"] == "public, max-age=300"
    assert "100%" in badge.text and "<script" not in badge.text
    bad_logo = await client.put(
        f"/api/status-pages/{page_id}",
        headers=auth_headers,
        json={"title": "T", "site_ids": [site_id], "logo_url": "javascript:alert(1)"},
    )
    assert bad_logo.status_code == 422


async def test_admin_stats_and_users(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    from app.models import User

    assert (await client.get("/api/admin/users", headers=auth_headers)).status_code == 403
    async with session_factory() as session:
        user = await session.scalar(select(User))
        assert user
        user.is_admin = True
        await session.commit()
    stats = (await client.get("/api/admin/stats", headers=auth_headers)).json()
    assert stats["users"] == 1 and len(stats["signups"]) == 90 and "queue_lag_seconds" in stats
    users = (await client.get("/api/admin/users?search=owner", headers=auth_headers)).json()
    assert users[0]["email"] == "owner@example.com" and users[0]["plan"] == "pro"


async def test_diagnostics_link_to_incident(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    from app.services.monitoring import record_probe

    site_id = await create(client, auth_headers, failure_threshold=3)
    for _ in range(3):
        await record_probe(site_id, ProbeResult(False, None, None, "connection refused"))
    async with session_factory() as session:
        rows = list(await session.scalars(select(CheckDiagnostic)))
        incident = await session.scalar(select(Incident))
    assert incident is not None and len(rows) == 2
    assert all(row.incident_id == incident.id for row in rows)
    assert incident.confirmed_at is not None and incident.started_at < incident.confirmed_at
