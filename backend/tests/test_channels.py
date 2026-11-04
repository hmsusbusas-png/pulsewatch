import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.crypto import decrypt_config
from app.database import session_factory
from app.models import NotificationChannel, NotificationDelivery, Site, SiteStatus
from app.services.monitoring import record_probe
from app.services.notifications import deliver_pending, unsubscribe_token
from app.services.probe import ProbeResult
from tests.conftest import activate
from tests.test_billing import set_plan


async def webhook_channel(client: httpx.AsyncClient, headers: dict[str, str]) -> int:
    response = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "type": "webhook",
            "name": "Operations",
            "config": {"url": "https://8.8.8.8/notify", "secret": "long-webhook-secret"},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["config"]["secret"] == "********"
    return int(response.json()["id"])


async def test_channels_encrypted_isolated_and_limited(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    channel_id = await webhook_channel(client, auth_headers)
    async with session_factory() as session:
        channel = await session.get(NotificationChannel, channel_id)
        assert channel and "long-webhook-secret" not in channel.config
        assert decrypt_config(channel.config)["secret"] == "long-webhook-secret"
    other = await client.post(
        "/api/auth/register", json={"email": "second@example.com", "password": "supersecret1"}
    )
    await activate(client, "second@example.com", "supersecret1")
    other = await client.post(
        "/api/auth/login", json={"email": "second@example.com", "password": "supersecret1"}
    )
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    assert (await client.get("/api/channels", headers=other_headers)).json() == []
    for method, suffix, payload in [
        ("POST", "/test", None),
        ("PATCH", "", {"name": "stolen"}),
        ("DELETE", "", None),
    ]:
        response = await client.request(
            method, f"/api/channels/{channel_id}{suffix}", headers=other_headers, json=payload
        )
        assert response.status_code == 404
    await set_plan("free")
    blocked = await client.post(
        "/api/channels",
        headers=auth_headers,
        json={"type": "email", "name": "Email", "config": {"email": "owner@example.com"}},
    )
    assert blocked.status_code == 402 and blocked.json()["code"] == "plan_limit_channels"
    assert (
        await client.delete(f"/api/channels/{channel_id}", headers=auth_headers)
    ).status_code == 204


async def test_webhook_retry_signature_and_delivery(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    channel_id = await webhook_channel(client, auth_headers)
    attempts = []

    async def request(url: str, **kwargs: object) -> httpx.Response:
        attempts.append(kwargs)
        code = 503 if len(attempts) == 1 else 204
        return httpx.Response(code)

    monkeypatch.setattr("app.services.notifications.safe_request", request)
    queued = await client.post(f"/api/channels/{channel_id}/test", headers=auth_headers)
    assert queued.status_code == 202
    async with session_factory() as session:
        await deliver_pending(session)
        delivery = await session.get(NotificationDelivery, queued.json()["delivery_id"])
        assert delivery and delivery.status == "pending" and delivery.attempts == 1
        delivery.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        await deliver_pending(session)
        await session.refresh(delivery)
        assert delivery.status == "sent" and delivery.attempts == 2
    body = attempts[0]["body"]
    assert (
        attempts[0]["headers"]["X-Pulsewatch-Signature"]
        == "sha256=" + hmac.new(b"long-webhook-secret", body, hashlib.sha256).hexdigest()
    )
    logs = (await client.get(f"/api/channels/{channel_id}/deliveries", headers=auth_headers)).json()
    assert logs[0]["response_code"] == 204


async def test_failure_confirmation_cooldown_recovery_and_maintenance(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await set_plan("business")
    channel_id = await webhook_channel(client, auth_headers)
    async with session_factory() as session:
        channel = await session.get(NotificationChannel, channel_id)
        assert channel
        channel.verified_at = datetime.now(UTC)
        await session.commit()
    created = await client.post(
        "/api/sites",
        headers=auth_headers,
        json={
            "name": "API",
            "url": "https://8.8.8.8",
            "failure_threshold": 3,
            "channel_ids": [channel_id],
        },
    )
    site_id = created.json()["id"]
    fail = ProbeResult(False, 503, 12, "HTTP 503")
    for _ in range(2):
        await record_probe(site_id, fail)
    assert (await client.get(f"/api/sites/{site_id}", headers=auth_headers)).json()[
        "status"
    ] == "unknown"
    await record_probe(site_id, fail)
    await record_probe(site_id, fail)
    async with session_factory() as session:
        rows = list(await session.scalars(select(NotificationDelivery)))
        assert len(rows) == 1
    await record_probe(site_id, ProbeResult(True, 200, 5, None))
    async with session_factory() as session:
        rows = list(await session.scalars(select(NotificationDelivery)))
        assert len(rows) == 2
        assert "Простой" in str(rows[1].payload["text"])
    now = datetime.now(UTC)
    response = await client.patch(
        f"/api/sites/{site_id}",
        headers=auth_headers,
        json={
            "maintenance": [
                {
                    "start": (now - timedelta(minutes=5)).isoformat(),
                    "end": (now + timedelta(minutes=5)).isoformat(),
                }
            ]
        },
    )
    assert response.status_code == 200
    for _ in range(4):
        await record_probe(site_id, fail)
    async with session_factory() as session:
        site = await session.get(Site, site_id)
        assert site and site.status == SiteStatus.up and site.consecutive_failures == 0


async def test_telegram_blocked_and_rate_limited(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.crypto import encrypt_config

    async with session_factory() as session:
        channel = NotificationChannel(
            user_id=1,
            name="Telegram",
            type="telegram",
            config=encrypt_config({"chat_id": "123"}),
            verified_at=datetime.now(UTC),
        )
        session.add(channel)
        await session.flush()
        channel_id = channel.id
        await session.commit()
    responses = [httpx.Response(429, json={"parameters": {"retry_after": 60}}), httpx.Response(403)]

    async def send(method: str, payload: dict[str, object]) -> httpx.Response:
        return responses.pop(0)

    monkeypatch.setattr("app.services.telegram.telegram_request", send)
    queued = await client.post(f"/api/channels/{channel_id}/test", headers=auth_headers)
    async with session_factory() as session:
        await deliver_pending(session)
        delivery = await session.get(NotificationDelivery, queued.json()["delivery_id"])
        assert delivery and delivery.next_attempt_at > datetime.now(UTC) + timedelta(seconds=50)
        delivery.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        await deliver_pending(session)
        channel = await session.get(NotificationChannel, channel_id)
        assert channel and not channel.enabled


async def test_email_unsubscribe_signed(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    response = await client.post(
        "/api/channels",
        headers=auth_headers,
        json={"type": "email", "name": "Mail", "config": {"email": "owner@example.com"}},
    )
    channel_id = response.json()["id"]
    sent = []

    async def send(address: str, subject: str, body: str) -> None:
        sent.append(body)

    monkeypatch.setattr("app.services.notifications.send_email", send)
    await client.post(f"/api/channels/{channel_id}/test", headers=auth_headers)
    async with session_factory() as session:
        await deliver_pending(session)
    assert len(sent) == 1 and "unsubscribe" in sent[0]
    assert (
        await client.post(
            "/api/channels/unsubscribe", json={"token": "bad-token-of-sufficient-length"}
        )
    ).status_code == 400
    result = await client.post(
        "/api/channels/unsubscribe", json={"token": unsubscribe_token(channel_id)}
    )
    assert result.status_code == 200
