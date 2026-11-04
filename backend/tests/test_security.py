import asyncio
import base64
import ipaddress
import json
import logging
import os
import secrets
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select

from app.config import settings, validate_runtime
from app.crypto import decrypt_text, encrypt_config, encrypt_text
from app.database import session_factory
from app.errors import ApiError
from app.logging_conf import JsonFormatter
from app.models import (
    AccountToken,
    ApiKey,
    AuditLog,
    Check,
    DailyUptime,
    Incident,
    IncidentNote,
    MailJob,
    NotificationChannel,
    NotificationDelivery,
    Payment,
    RefreshToken,
    Site,
    SiteChannel,
    StatusPage,
    StatusPageSite,
    StatusSubscriber,
    Subscription,
    User,
)
from app.security import access_claims, create_access_token, hash_password, link_claims, signed_link
from app.services.billing import activate_payment, current_plan, payment_transition
from app.ssrf import ensure_target_allowed, is_blocked_ip, normalize_url
from tests.conftest import activate
from tests.test_billing import set_plan

BACKEND_DIR = Path(__file__).resolve().parents[1]


def without_request_id(body: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in body.items() if key != "request_id"}


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "169.254.169.254",
        "224.0.0.1",
        "240.0.0.1",
        "100.64.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
    ],
)
def test_blocked_address_categories(address: str) -> None:
    assert is_blocked_ip(ipaddress.ip_address(address))


@pytest.mark.parametrize("host", ["2130706433", "0177.0.0.1", "0x7f000001"])
async def test_alternative_loopback_notations(host: str) -> None:
    with pytest.raises(ApiError):
        await ensure_target_allowed(f"http://{host}/", False)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "http://user@example.com",
        "http://example.com:22",
        "http://example.com:25",
        "http://example.com:5432",
        "http://example.com:6379",
        "http://example.com:2375",
        "http://[",
        "http://example.com/\r\nX-Test:x",
    ],
)
def test_forbidden_urls(url: str) -> None:
    with pytest.raises(ApiError):
        normalize_url(url)


@pytest.mark.parametrize("field", ["exp", "iat", "iss", "aud", "jti", "sub", "ver"])
def test_jwt_requires_claims(field: str) -> None:
    data = jwt.decode(create_access_token(1), options={"verify_signature": False})
    data.pop(field)
    assert access_claims(jwt.encode(data, settings.jwt_secret, algorithm="HS256")) is None


@pytest.mark.parametrize(
    "change",
    [{"iss": "other"}, {"aud": "other"}, {"jti": "short"}, {"iat": 9999999999}, {"exp": 1}],
)
def test_jwt_rejects_invalid_claims(change: dict[str, object]) -> None:
    data = jwt.decode(create_access_token(1), options={"verify_signature": False})
    data.update(change)
    assert access_claims(jwt.encode(data, settings.jwt_secret, algorithm="HS256")) is None


def test_jwt_fixed_algorithm_and_password_cost() -> None:
    data = jwt.decode(create_access_token(1), options={"verify_signature": False})
    assert access_claims(jwt.encode(data, "", algorithm="none")) is None
    assert access_claims(jwt.encode(data, secrets.token_urlsafe(64), algorithm="HS512")) is None
    assert hash_password(secrets.token_urlsafe(24)).split("$")[2] == "12"
    with pytest.raises(ApiError):
        hash_password("x" * 73)


def good_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.engine import make_url

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "jwt_secret", secrets.token_urlsafe(48))
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(
        settings,
        "database_url",
        make_url(settings.database_url)
        .set(password=secrets.token_urlsafe(48))
        .render_as_string(hide_password=False),
    )
    monkeypatch.setattr(settings, "cookie_secure", True)
    monkeypatch.setattr(settings, "cors_origins", ["https://example.com"])
    monkeypatch.setattr(settings, "api_base_url", "https://example.com")
    monkeypatch.setattr(settings, "frontend_base_url", "https://example.com")
    monkeypatch.setattr(settings, "payment_provider", "yookassa")


@pytest.mark.parametrize("value", ["", "CHANGE_ME", "a" * 32, "CHANGE_ME" * 8])
def test_production_rejects_weak_secrets(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    good_production(monkeypatch)
    monkeypatch.setattr(settings, "jwt_secret", value)
    with pytest.raises(RuntimeError):
        validate_runtime()


def test_production_configuration_and_cors(monkeypatch: pytest.MonkeyPatch) -> None:
    good_production(monkeypatch)
    validate_runtime()
    monkeypatch.setattr(settings, "cors_origins", ["*"])
    with pytest.raises(RuntimeError):
        validate_runtime()


@pytest.mark.parametrize("password", ["", "CHANGE_ME", "x" * 32])
def test_weak_database_password_rejected(password: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.engine import make_url

    good_production(monkeypatch)
    monkeypatch.setattr(
        settings,
        "database_url",
        make_url(settings.database_url)
        .set(password=password)
        .render_as_string(hide_password=False),
    )
    with pytest.raises(RuntimeError):
        validate_runtime()
    monkeypatch.setattr(settings, "cors_origins", ["https://example.com"])
    monkeypatch.setattr(settings, "encryption_key", base64.urlsafe_b64encode(b"0" * 32).decode())
    with pytest.raises(RuntimeError):
        validate_runtime()


async def test_no_dev_routes_or_docs_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import create_app

    good_production(monkeypatch)
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://example.com"
    ) as client:
        for path in ("/docs", "/redoc", "/openapi.json", "/api/billing/dev/unknown"):
            assert (await client.get(path)).status_code == 404
        assert (await client.post("/api/billing/webhook/dev", json={})).status_code == 404
        response = await client.get("/healthz")
        assert "Strict-Transport-Security" in response.headers
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-c",
            (
                "import sys; from app.main import app; "
                "assert 'app.services.dev_payments' not in sys.modules"
            ),
        ],
        cwd=BACKEND_DIR,
        env={**os.environ, "APP_ENV": "production"},
        capture_output=True,
    )
    assert result.returncode == 0


async def test_login_uses_equal_password_work(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.routers import auth

    calls = []
    verify = auth.verify_password

    def observed(password: str, stored: str) -> bool:
        calls.append(stored)
        return verify(password, stored)

    monkeypatch.setattr(auth, "verify_password", observed)
    replies = [
        await client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
        for email in ("missing@example.com", "owner@example.com")
    ]
    assert replies[0].status_code == replies[1].status_code == 401
    assert without_request_id(replies[0].json()) == without_request_id(replies[1].json())
    assert (
        len(calls) == 2 and calls[0] == auth.DUMMY_HASH and all("$12$" in value for value in calls)
    )


async def test_request_caps_and_security_headers(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/auth/login",
        content=b"x" * (1048576 + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    response = await client.post(
        "/api/auth/login",
        content="[" * 17 + "0" + "]" * 17,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    response = await client.get("/api/me", headers=auth_headers)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_log_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    private = secrets.token_urlsafe(40)
    monkeypatch.setattr(settings, "bot_token", private)
    record = logging.LogRecord("test", logging.ERROR, "test", 1, f"Bearer {private}", (), None)
    record.extra_fields = {
        "authorization": private,
        "nested": {"password": private},
        "raw_payload": {"key": private},
    }
    rendered = JsonFormatter().format(record)
    assert private not in rendered
    assert "[redacted]" in rendered


async def test_tariff_race_and_resume_cannot_rewrite_pause_reason(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    for index in range(4):
        assert (
            await client.post(
                "/api/sites",
                headers=auth_headers,
                json={"name": str(index), "url": "https://example.com"},
            )
        ).status_code == 201
    await set_plan("free")
    assert (
        await client.patch("/api/sites/4", headers=auth_headers, json={"paused": True})
    ).status_code == 200
    assert (
        await client.patch("/api/sites/4", headers=auth_headers, json={"paused": False})
    ).status_code == 402
    assert (
        await client.patch("/api/sites/1", headers=auth_headers, json={"interval_seconds": 60})
    ).status_code == 402
    await client.delete("/api/sites/4", headers=auth_headers)
    await client.delete("/api/sites/3", headers=auth_headers)
    results = await asyncio.gather(
        *(
            client.post(
                "/api/sites",
                headers=auth_headers,
                json={"name": f"race-{index}", "url": "https://example.com"},
            )
            for index in range(2)
        )
    )
    assert sorted(result.status_code for result in results) == [201, 402]


async def test_payment_fsm_and_first_webhook_race(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    from app.services.dev_payments import DevProvider

    checkout = (
        await client.post("/api/billing/checkout", headers=auth_headers, json={"plan_code": "pro"})
    ).json()
    payment_id = checkout["payment_id"]

    async def paid() -> bool:
        async with session_factory() as session:
            changed = await activate_payment(
                session, payment_id, "dev", 99000, {"secret": "must not persist"}
            )
            await session.commit()
            return changed

    assert sum(await asyncio.gather(paid(), paid())) == 1
    async with session_factory() as session:
        payment = await session.scalar(select(Payment))
        assert payment and "secret" not in payment.raw_payload
        payment_transition(payment, "refunded", {})
        with pytest.raises(ApiError):
            payment_transition(payment, "succeeded", {})
        payment.status = "canceled"
        payment.paid_at = None
        await session.commit()
    response = await client.post(
        "/api/billing/webhook/dev",
        json={"id": payment_id, "amount_kop": 99000},
        headers={"x-signature": DevProvider.signature(payment_id)},
    )
    assert response.status_code == 409


@pytest.mark.parametrize("value", ["=1+1", "+1", "-1", "@SUM(A1)", " =1", "\t=1", "\r=1"])
def test_csv_formulas(value: str) -> None:
    from app.routers.export import csv_value

    assert csv_value(value).startswith("'")


async def test_xss_and_mass_assignment(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    denied = await client.post(
        "/api/sites",
        headers=auth_headers,
        json={"name": "site", "url": "https://example.com", "user_id": 999, "is_admin": True},
    )
    assert denied.status_code == 422
    created = await client.post(
        "/api/sites",
        headers=auth_headers,
        json={"name": "<img src=x onerror=alert(1)>", "url": "https://example.com"},
    )
    page = await client.post(
        "/api/status-pages",
        headers=auth_headers,
        json={
            "title": "<script>alert(1)</script>",
            "description": "<iframe>",
            "site_ids": [created.json()["id"]],
        },
    )
    public = await client.get(f"/api/public/pages/{page.json()['slug']}")
    assert "<script>" not in public.text and "<img" not in public.text and "&lt;" in public.text


def test_signed_links_expire_and_have_purpose() -> None:
    token = signed_link("reset", {"nonce": secrets.token_urlsafe(32)}, 60)
    assert link_claims(token, "reset")
    assert link_claims(token, "email") is None
    assert link_claims(signed_link("reset", {}, -1), "reset") is None


@pytest.fixture
async def foreign_resources(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> dict[str, str]:
    await set_plan("business")
    assert (
        await client.post(
            "/api/sites",
            headers=auth_headers,
            json={"name": "private", "url": "https://example.com"},
        )
    ).status_code == 201
    assert (
        await client.post(
            "/api/channels",
            headers=auth_headers,
            json={"type": "email", "name": "mail", "config": {"email": "owner@example.com"}},
        )
    ).status_code == 201
    assert (
        await client.post("/api/keys", headers=auth_headers, json={"name": "private"})
    ).status_code == 201
    assert (
        await client.post(
            "/api/status-pages", headers=auth_headers, json={"title": "private", "site_ids": [1]}
        )
    ).status_code == 201
    assert (
        await client.post("/api/billing/checkout", headers=auth_headers, json={"plan_code": "pro"})
    ).status_code == 200
    async with session_factory() as session:
        session.add(Incident(site_id=1, reason="private"))
        await session.commit()
    payload = {"email": "second@example.com", "password": secrets.token_urlsafe(24)}
    assert (await client.post("/api/auth/register", json=payload)).status_code == 202
    await activate(client, payload["email"], payload["password"])
    login = await client.post("/api/auth/login", json=payload)
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.email == payload["email"]))
        assert user
        sub, _ = await current_plan(session, user)
        from app.services.billing import plan_by_code

        sub.plan_id = (await plan_by_code(session, "business")).id
        await session.commit()
    return headers


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("GET", "/api/sites/1", None),
        ("PATCH", "/api/sites/1", {"name": "other"}),
        ("DELETE", "/api/sites/1", None),
        ("GET", "/api/sites/1/checks", None),
        ("GET", "/api/sites/1/incidents", None),
        ("PATCH", "/api/channels/1", {"enabled": False}),
        ("DELETE", "/api/channels/1", None),
        ("POST", "/api/channels/1/test", None),
        ("GET", "/api/channels/1/deliveries", None),
        ("DELETE", "/api/keys/1", None),
        ("PUT", "/api/status-pages/1", {"title": "other", "site_ids": [1]}),
        ("DELETE", "/api/status-pages/1", None),
        ("POST", "/api/status-pages", {"title": "other", "site_ids": [1]}),
        ("POST", "/api/incidents/1/notes", {"status": "resolved", "text": "other"}),
        ("GET", "/api/export/checks.csv?site_id=1", None),
        ("DELETE", "/api/account/sessions/1", None),
        ("GET", "/api/admin/stats", None),
        ("POST", "/api/admin/users/1/plan", {"plan_code": "free"}),
    ],
)
async def test_resource_owner_matrix(
    client: httpx.AsyncClient,
    foreign_resources: dict[str, str],
    method: str,
    path: str,
    payload: object,
) -> None:
    response = await client.request(method, path, json=payload, headers=foreign_resources)
    assert response.status_code in (403, 404), response.text
    for collection in ("sites", "channels", "keys", "billing/payments", "status-pages"):
        assert (await client.get(f"/api/{collection}", headers=foreign_resources)).json() == []
    assert (await client.get("/api/billing/subscription", headers=foreign_resources)).json()[
        "usage"
    ] == {"sites": 0, "channels": 0}


async def test_account_deletion_cascades_every_table(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await set_plan("business")
    await client.post(
        "/api/sites", headers=auth_headers, json={"name": "private", "url": "https://example.com"}
    )
    await client.post(
        "/api/channels",
        headers=auth_headers,
        json={"type": "email", "name": "mail", "config": {"email": "owner@example.com"}},
    )
    await client.post("/api/keys", headers=auth_headers, json={"name": "key"})
    await client.post("/api/billing/checkout", headers=auth_headers, json={"plan_code": "pro"})
    await client.post(
        "/api/status-pages", headers=auth_headers, json={"title": "page", "site_ids": [1]}
    )
    async with session_factory() as session:
        session.add_all(
            [
                Check(site_id=1, ok=True),
                Incident(site_id=1, reason="test"),
                NotificationDelivery(channel_id=1, payload={"text": "test"}),
                SiteChannel(site_id=1, channel_id=1),
                StatusSubscriber(page_id=1, email="user@example.com"),
                MailJob(
                    user_id=1,
                    recipient="user@example.com",
                    subject="test",
                    body=encrypt_text("test"),
                ),
                AccountToken(
                    user_id=1,
                    token_hash=secrets.token_hex(32),
                    purpose="reset",
                    expires_at=datetime.now(UTC) + timedelta(minutes=30),
                ),
            ]
        )
        await session.flush()
        session.add(IncidentNote(incident_id=1, status="investigating", text="test"))
        await session.commit()
    response = await client.request(
        "DELETE",
        "/api/account",
        headers=auth_headers,
        json={"email": "owner@example.com", "password": "supersecret1"},
    )
    assert response.status_code == 204
    async with session_factory() as session:
        for model in (
            User,
            Site,
            Check,
            DailyUptime,
            Incident,
            IncidentNote,
            RefreshToken,
            AccountToken,
            Subscription,
            Payment,
            NotificationChannel,
            NotificationDelivery,
            SiteChannel,
            ApiKey,
            StatusPage,
            StatusPageSite,
            StatusSubscriber,
            MailJob,
            AuditLog,
        ):
            assert await session.scalar(select(func.count()).select_from(model)) == 0, (
                model.__tablename__
            )


async def test_multifernet_rotation(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.rotate_keys import rotate

    old, new = Fernet.generate_key(), Fernet.generate_key()
    monkeypatch.setattr(settings, "encryption_key", old.decode())
    async with session_factory() as session:
        session.add(
            Site(
                user_id=1,
                name="private",
                url="https://example.com",
                headers_encrypted=encrypt_config({"Authorization": "private"}),
            )
        )
        session.add(
            NotificationChannel(
                user_id=1,
                name="mail",
                type="email",
                config=encrypt_config({"email": "owner@example.com"}),
            )
        )
        session.add(
            MailJob(
                user_id=1,
                recipient="user@example.com",
                subject="test",
                body=encrypt_text("private"),
            )
        )
        await session.commit()
    monkeypatch.setattr(settings, "encryption_key", new.decode())
    monkeypatch.setattr(settings, "fernet_previous_keys", [old.decode()])
    await rotate()
    monkeypatch.setattr(settings, "fernet_previous_keys", [])
    async with session_factory() as session:
        body = await session.scalar(select(MailJob.body).where(MailJob.body != ""))
        assert body and decrypt_text(body) == "private"
        with pytest.raises(InvalidToken):
            Fernet(old).decrypt(body.encode())
        encrypted_headers = await session.scalar(select(Site.headers_encrypted))
        assert encrypted_headers and "private" in decrypt_text(encrypted_headers)


@pytest.mark.parametrize(
    "path",
    [
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/password/reset",
        "/api/public/pages/test",
        "/api/status/test",
        "/api/billing/webhook/dev",
        "/api/telegram/webhook",
        "/api/telegram/link",
        "/api/channels/1/test",
    ],
)
async def test_rate_gate_covers_sensitive_routes(
    path: str, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def denied(key: str, limit: int) -> bool:
        return False

    monkeypatch.setattr("app.http_guard.allow_persistent", denied)
    assert (await client.post(path, json={})).status_code == 429


async def test_webhook_ssrf_uses_probe_policy(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1"):
        response = await client.post(
            "/api/channels",
            headers=auth_headers,
            json={
                "type": "webhook",
                "name": "test",
                "config": {"url": f"http://{address}", "secret": secrets.token_urlsafe(24)},
            },
        )
        assert response.status_code == 403


async def test_total_timeout_and_redirect_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import probe

    async def delayed(url: str, allow_private: bool) -> str:
        await asyncio.Event().wait()
        return "8.8.8.8"

    monkeypatch.setattr(probe, "target_ip", delayed)
    with pytest.raises(TimeoutError):
        await probe.safe_request("https://example.com", request_timeout=1)

    async def public(url: str, allow_private: bool) -> str:
        return "8.8.8.8"

    monkeypatch.setattr(probe, "target_ip", public)
    count = 0

    def redirect(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(302, headers={"location": "/loop"}, stream=httpx.ByteStream(b""))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        probe.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(redirect), **kw),
    )
    with pytest.raises(probe.RedirectRejected):
        await probe.safe_request("https://example.com")
    assert count == probe.MAX_REDIRECTS + 1


async def test_refund_verified_via_api_and_downgrades(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import uuid

    from app.services.billing import refund_subscription
    from app.services.payments import YooKassaProvider

    checkout = (
        await client.post("/api/billing/checkout", headers=auth_headers, json={"plan_code": "pro"})
    ).json()
    async with session_factory() as session:
        await activate_payment(session, checkout["payment_id"], "dev", 99000, {})
        payment = await session.scalar(select(Payment))
        assert payment
        payment_transition(payment, "refunded", {})
        await refund_subscription(session, payment)
        await session.commit()
    assert (await client.get("/api/billing/subscription", headers=auth_headers)).json()["plan"][
        "code"
    ] == "free"
    monkeypatch.setattr(settings, "yookassa_shop_id", "CHANGE_ME")
    monkeypatch.setattr(settings, "yookassa_secret_key", "CHANGE_ME")
    refund_id, payment_id = str(uuid.uuid4()), str(uuid.uuid4())
    calls = []

    def verified(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "/refunds/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "id": refund_id,
                    "payment_id": payment_id,
                    "status": "succeeded",
                    "amount": {"value": "990.00", "currency": "RUB"},
                },
            )
        return httpx.Response(200, json={"id": payment_id, "test": True, "status": "succeeded"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.services.payments.httpx.AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(verified), **kw),
    )
    result = await YooKassaProvider().parse_webhook(
        {"event": "refund.succeeded", "object": {"id": refund_id}}
    )
    assert result.id == payment_id and result.status == "refunded" and result.amount_kop == 99000
    assert len(calls) == 2


async def test_api_key_cannot_bypass_site_limit(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    from app.services.billing import plan_by_code

    await set_plan("business")
    issued = await client.post(
        "/api/keys", headers=auth_headers, json={"name": "test", "scope": "write"}
    )
    headers = {"Authorization": f"Bearer {issued.json()['token']}"}
    async with session_factory() as session:
        plan = await plan_by_code(session, "business")
        plan.max_sites = 1
        await session.commit()
    try:
        responses = await asyncio.gather(
            *(
                client.post(
                    "/api/sites",
                    headers=headers,
                    json={"name": "test", "url": "https://example.com"},
                )
                for _ in range(2)
            )
        )
        assert sorted(response.status_code for response in responses) == [201, 402]
    finally:
        async with session_factory() as session:
            plan = await plan_by_code(session, "business")
            plan.max_sites = 100
            await session.commit()


async def test_500_hides_internal_details() -> None:
    from app.main import create_app

    app = create_app()
    secret = secrets.token_urlsafe(32)

    async def crash() -> None:
        raise RuntimeError(secret)

    app.add_api_route("/crash", crash)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://example.com",
    ) as client:
        response = await client.get("/crash")
        assert response.status_code == 500
        body = response.json()
        assert set(body) == {"code", "message", "details", "request_id"}
        assert body["code"] == "internal_error" and body["details"] is None
        assert body["request_id"] == response.headers["x-request-id"]
        assert secret not in response.text


def test_configured_logger_never_formats_exception_values(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    from app.logging_conf import configure_logging, redact, request_id_var

    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", list(root.handlers))
    monkeypatch.setattr(root, "level", root.level)
    configure_logging("INFO")
    stream = io.StringIO()
    root.handlers[0].setStream(stream)
    secret = secrets.token_urlsafe(32)
    context = request_id_var.set("correlation")
    try:
        try:
            raise RuntimeError(secret)
        except RuntimeError:
            root.exception("operation_failed", extra={"extra_fields": {"authorization": secret}})
    finally:
        request_id_var.reset(context)
    result = json.loads(stream.getvalue())
    assert result["exception"] == "RuntimeError"
    assert result["request_id"] == "correlation"
    assert secret not in stream.getvalue()
    assert redact([True, {"password": secret}]) == [True, {"password": "[redacted]"}]


async def test_seed_generates_password_once_and_migrates_legacy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.models import UserTariff
    from app.security import verify_password
    from app.seed import seed

    async with session_factory() as session:
        legacy = User(
            email="legacy@example.com",
            password_hash=hash_password(secrets.token_urlsafe(24)),
            tariff=UserTariff.pro,
            telegram_chat_id=123,
        )
        session.add(legacy)
        await session.flush()
        session.add(Site(user_id=legacy.id, name="legacy", url="https://example.com"))
        await session.commit()
    await seed()
    output = capsys.readouterr().out
    password = output.split("Demo password: ")[1].strip()
    async with session_factory() as session:
        demo = await session.scalar(select(User).where(User.email == settings.demo_email))
        assert demo and verify_password(password, demo.password_hash)
        legacy = await session.scalar(select(User).where(User.email == "legacy@example.com"))
        assert legacy and legacy.trial_used
        sub = await session.scalar(select(Subscription).where(Subscription.user_id == legacy.id))
        assert sub and sub.status == "active"
        assert await session.scalar(select(func.count(NotificationChannel.id))) == 1
        assert await session.scalar(select(func.count(SiteChannel.site_id))) == 1
    await seed()
    assert capsys.readouterr().out == ""


async def test_registration_requires_email_ownership(client: httpx.AsyncClient) -> None:
    payload = {"email": "user@example.com", "password": secrets.token_urlsafe(24)}
    await client.post("/api/auth/register", json=payload)
    first = await client.post("/api/auth/login", json=payload)
    missing = await client.post("/api/auth/login", json={**payload, "email": "missing@example.com"})
    assert first.status_code == missing.status_code == 401
    assert without_request_id(first.json()) == without_request_id(missing.json())
    await activate(client, payload["email"], payload["password"])
    assert (await client.post("/api/auth/login", json=payload)).status_code == 200
    assert (await client.post("/api/auth/register", json=payload)).json() == {"accepted": True}


@pytest.mark.parametrize(
    "path",
    [
        "/api/sites?limit=101",
        "/api/sites/1/checks?limit=1001",
        "/api/channels?limit=101",
        "/api/keys?limit=101",
        "/api/status-pages?limit=101",
        "/api/account/sessions?limit=101",
    ],
)
async def test_pagination_limits(
    path: str, client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    assert (await client.get(path, headers=auth_headers)).status_code == 422
