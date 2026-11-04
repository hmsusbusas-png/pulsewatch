import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.crypto import decrypt_text
from app.database import session_factory
from app.models import Check, Incident, MailJob, Site
from app.scheduler import heartbeat, run_due_checks
from app.services.probe import ProbeResult
from tests.test_billing import set_plan


async def test_keys_scopes_revocation_and_csv(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    denied = await client.post("/api/keys", headers=auth_headers, json={"name": "CLI"})
    assert denied.status_code == 403
    await set_plan("business")
    created = await client.post(
        "/api/sites", headers=auth_headers, json={"name": "=SUM(A1)", "url": "https://8.8.8.8"}
    )
    assert created.status_code == 201
    issued = await client.post(
        "/api/keys", headers=auth_headers, json={"name": "CLI", "scope": "read"}
    )
    assert issued.status_code == 201
    key = issued.json()
    headers = {"Authorization": f"Bearer {key['token']}"}
    assert (await client.get("/api/sites", headers=headers)).status_code == 200
    assert (
        await client.post(
            "/api/sites", headers=headers, json={"name": "forbidden", "url": "https://8.8.8.8"}
        )
    ).status_code == 403
    assert (await client.get("/api/admin/stats", headers=headers)).status_code == 403
    for kind in ("sites", "checks", "incidents"):
        result = await client.get(f"/api/export/{kind}.csv", headers=headers)
        assert result.status_code == 200, result.text
        if kind == "sites":
            assert "'=SUM(A1)" in result.text
    assert (await client.get("/api/keys", headers=auth_headers)).json()[0]["last_used_at"]
    assert (await client.delete(f"/api/keys/{key['id']}", headers=auth_headers)).status_code == 204
    assert (await client.get("/api/sites", headers=headers)).status_code == 401
    issued = await client.post(
        "/api/keys", headers=auth_headers, json={"name": "writer", "scope": "write"}
    )
    headers = {"Authorization": f"Bearer {issued.json()['token']}"}
    assert (
        await client.post(
            "/api/sites", headers=headers, json={"name": "second", "url": "https://8.8.8.8"}
        )
    ).status_code == 201
    await set_plan("free")
    assert (await client.get("/api/sites", headers=headers)).status_code == 403


async def test_account_reset_email_sessions_and_delete(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    assert len((await client.get("/api/account/sessions", headers=auth_headers)).json()) == 1
    wrong = await client.post(
        "/api/account/password",
        headers=auth_headers,
        json={"current_password": "wrong", "new_password": "newpassword1"},
    )
    assert wrong.status_code == 403
    changed = await client.post(
        "/api/account/password",
        headers=auth_headers,
        json={"current_password": "supersecret1", "new_password": "newpassword1"},
    )
    assert changed.status_code == 204
    assert (await client.get("/api/account/sessions", headers=auth_headers)).status_code == 401
    login = await client.post(
        "/api/auth/login", json={"email": "owner@example.com", "password": "newpassword1"}
    )
    auth_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    requested = await client.post("/api/auth/password/reset", json={"email": "owner@example.com"})
    assert requested.status_code == 202
    async with session_factory() as session:
        job = await session.scalar(select(MailJob).order_by(MailJob.id.desc()))
        assert job
        token = decrypt_text(job.body).split("token=")[1].split()[0]
    confirmed = await client.post(
        "/api/auth/password/confirm", json={"token": token, "password": "resetpassword1"}
    )
    assert confirmed.status_code == 204
    assert (
        await client.post(
            "/api/auth/password/confirm", json={"token": token, "password": "resetpassword1"}
        )
    ).status_code == 400
    login = await client.post(
        "/api/auth/login", json={"email": "owner@example.com", "password": "resetpassword1"}
    )
    assert login.status_code == 200
    auth_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    changed = await client.post(
        "/api/account/email",
        headers=auth_headers,
        json={"password": "resetpassword1", "email": "changed@example.com"},
    )
    assert changed.status_code == 202
    async with session_factory() as session:
        job = await session.scalar(select(MailJob).order_by(MailJob.id.desc()))
        assert job
        token = decrypt_text(job.body).split("token=")[1].split()[0]
    assert (
        await client.post("/api/account/email/confirm", json={"token": token})
    ).status_code == 204
    assert (await client.get("/api/me", headers=auth_headers)).status_code == 401
    login = await client.post(
        "/api/auth/login", json={"email": "changed@example.com", "password": "resetpassword1"}
    )
    auth_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/api/me", headers=auth_headers)).json()[
        "email"
    ] == "changed@example.com"
    assert (await client.get("/api/account/audit", headers=auth_headers)).json()
    assert (await client.delete("/api/account/sessions", headers=auth_headers)).status_code == 204
    login = await client.post(
        "/api/auth/login", json={"email": "changed@example.com", "password": "resetpassword1"}
    )
    auth_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    deleted = await client.request(
        "DELETE",
        "/api/account",
        headers=auth_headers,
        json={"email": "changed@example.com", "password": "resetpassword1"},
    )
    assert deleted.status_code == 204
    assert (await client.get("/api/me", headers=auth_headers)).status_code == 401
    async with session_factory() as session:
        assert await session.scalar(select(func.count(MailJob.id))) == 0


async def test_pages_notes_subscribers_and_mail_queue(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await client.post(
        "/api/sites", headers=auth_headers, json={"name": "API", "url": "https://8.8.8.8"}
    )
    site_id = created.json()["id"]
    page = await client.post(
        "/api/status-pages",
        headers=auth_headers,
        json={"title": "Services", "slug": "services", "site_ids": [site_id]},
    )
    assert page.status_code == 201
    page_id = page.json()["id"]
    async with session_factory() as session:
        incident = Incident(site_id=site_id, reason="private reason")
        session.add(incident)
        await session.commit()
        incident_id = incident.id
    note = await client.post(
        f"/api/incidents/{incident_id}/notes",
        headers=auth_headers,
        json={"status": "investigating", "text": "Проверяем сеть"},
    )
    assert note.status_code == 201
    public = (await client.get("/api/public/pages/services")).json()
    assert public["sites"][0]["url"] is None
    assert public["incidents"][0]["notes"][0]["status"] == "investigating"
    assert "private reason" not in str(public)
    subscribed = await client.post(
        "/api/public/pages/services/subscribe", json={"email": "reader@example.com"}
    )
    assert subscribed.status_code == 202
    async with session_factory() as session:
        job = await session.scalar(select(MailJob).order_by(MailJob.id.desc()))
        assert job
        url = decrypt_text(job.body).split()[-1]
    assert url.startswith(f"{settings.frontend_base_url}/public/subscribers/confirm?token=")
    confirm = await client.post(
        "/api/public/subscribers/confirm", json={"token": url.split("token=")[1]}
    )
    assert confirm.status_code == 200
    from app.services.notifications import deliver_mail, enqueue

    async with session_factory() as session:
        site = await session.get(Site, site_id)
        assert site
        await enqueue(session, site, "Восстановление")
        await session.commit()
    sent = []

    async def send(address: str, subject: str, body: str) -> None:
        sent.append(body)

    monkeypatch.setattr("app.services.notifications.send_email", send)
    async with session_factory() as session:
        await deliver_mail(session)
    assert len(sent) == 2
    link = sent[1].split()[-1]
    assert "/public/subscribers/unsubscribe?token=" in link
    unsubscribed = await client.post(
        "/api/public/subscribers/unsubscribe", json={"token": link.split("token=")[1]}
    )
    assert unsubscribed.status_code == 200
    updated = await client.put(
        f"/api/status-pages/{page_id}",
        headers=auth_headers,
        json={"title": "Renamed", "site_ids": [site_id]},
    )
    assert updated.status_code == 204
    assert (await client.get("/api/status-pages", headers=auth_headers)).json()[0][
        "title"
    ] == "Renamed"
    assert (
        await client.delete(f"/api/status-pages/{page_id}", headers=auth_headers)
    ).status_code == 204
    assert (await client.get("/api/public/pages/services")).status_code == 404


async def test_worker_claims_and_fences_results(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await client.post(
        "/api/sites", headers=auth_headers, json={"name": "API", "url": "http://8.8.8.8"}
    )
    site_id = created.json()["id"]
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def probe(site: Site) -> ProbeResult:
        calls.append(site.id)
        started.set()
        await release.wait()
        return ProbeResult(True, 200, 5, None)

    monkeypatch.setattr("app.scheduler.probe_monitor", probe)
    first = asyncio.create_task(run_due_checks())
    await started.wait()
    await run_due_checks()
    assert calls == [site_id]
    release.set()
    await first
    async with session_factory() as session:
        site = await session.get(Site, site_id)
        assert site and site.lease_token is None and site.next_check_at > datetime.now(UTC)
        assert await session.scalar(select(func.count(Check.id))) == 1
    from app.services.monitoring import record_probe

    await record_probe(site_id, ProbeResult(False, 500, 5, "stale"), "stale-token")
    async with session_factory() as session:
        assert await session.scalar(select(func.count(Check.id))) == 1


async def test_health_metrics_and_rate_limit(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/readyz")).status_code == 503
    await heartbeat()
    assert (await client.get("/readyz")).status_code == 200
    assert (await client.get("/metrics")).status_code == 403
    monkeypatch.setattr(settings, "metrics_token", "test-metrics")
    metrics = await client.get("/metrics", headers={"Authorization": "Bearer test-metrics"})
    assert metrics.status_code == 200 and "pulsewatch_sites" in metrics.text
    async with session_factory() as session:
        from sqlalchemy import delete

        from app.models import RateBucket

        await session.execute(delete(RateBucket))
        await session.commit()
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1)
    for index, expected in enumerate((200, 429)):
        response = await client.post(
            "/api/auth/login",
            json={"email": "owner@example.com", "password": "supersecret1"},
            headers={"x-forwarded-for": f"8.8.8.{index}"},
        )
        assert response.status_code == expected
    csrf = await client.post(
        "/api/auth/refresh", headers={"Origin": "https://attacker.example.org"}
    )
    assert csrf.status_code in (403, 429)
