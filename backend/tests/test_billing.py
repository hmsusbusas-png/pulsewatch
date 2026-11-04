import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.database import session_factory
from app.models import Check, DailyUptime, Plan, User
from app.services.billing import (
    apply_limits,
    billing_maintenance,
    current_plan,
    grant_trial,
    plan_by_code,
)


async def set_plan(code: str) -> None:
    async with session_factory() as session:
        user = await session.scalar(select(User).order_by(User.id))
        assert user is not None
        sub, _ = await current_plan(session, user)
        plan = await plan_by_code(session, code)
        sub.plan_id = plan.id
        sub.status = "active"
        sub.current_period_end = datetime.now(UTC) + timedelta(days=30)
        await apply_limits(session, user.id, plan)
        await session.commit()


async def test_trial_only_once(client: httpx.AsyncClient, auth_headers: dict[str, str]) -> None:
    first = (await client.get("/api/billing/subscription", headers=auth_headers)).json()
    assert first["plan"]["code"] == "pro"
    assert first["status"] == "trialing"
    async with session_factory() as session:
        user = await session.scalar(select(User))
        assert user and user.trial_used
        sub = await grant_trial(session, user)
        assert sub.current_period_end.isoformat() == first["current_period_end"].replace(
            "Z", "+00:00"
        )
        sub.current_period_end = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    expired = (await client.get("/api/billing/subscription", headers=auth_headers)).json()
    assert expired["plan"]["code"] == "free"
    assert expired["status"] == "expired"
    async with session_factory() as session:
        user = await session.scalar(select(User))
        assert user
        sub = await grant_trial(session, user)
        assert (await session.get(Plan, sub.plan_id)).code == "free"


async def test_limits_come_from_database(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    async with session_factory() as session:
        plan = await plan_by_code(session, "pro")
        plan.max_sites = 1
        await session.commit()
    try:
        first = await client.post(
            "/api/sites", headers=auth_headers, json={"name": "one", "url": "https://8.8.8.8"}
        )
        assert first.status_code == 201
        second = await client.post(
            "/api/sites", headers=auth_headers, json={"name": "two", "url": "https://8.8.8.8"}
        )
        assert second.status_code == 402
        assert second.json()["code"] == "plan_limit_sites"
    finally:
        async with session_factory() as session:
            plan = await plan_by_code(session, "pro")
            plan.max_sites = 20
            await session.commit()


async def test_downgrade_pauses_newest(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    ids = []
    for index in range(5):
        response = await client.post(
            "/api/sites",
            headers=auth_headers,
            json={"name": str(index), "url": "https://8.8.8.8", "interval_seconds": 60},
        )
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    await set_plan("free")
    rows = (await client.get("/api/sites", headers=auth_headers)).json()
    assert [row["id"] for row in rows if row["paused"]] == ids[3:]
    assert all(row["interval_seconds"] == 300 for row in rows)
    assert all(row["pause_reason"] == "plan_limit_sites" for row in rows[3:])
    resume = await client.patch(
        f"/api/sites/{ids[-1]}", headers=auth_headers, json={"paused": False}
    )
    assert resume.status_code == 402
    await set_plan("pro")
    assert all(
        not row["paused"] for row in (await client.get("/api/sites", headers=auth_headers)).json()
    )


async def test_free_feature_limits(client: httpx.AsyncClient, auth_headers: dict[str, str]) -> None:
    await set_plan("free")
    for extra, code in [
        ({"interval_seconds": 60}, "plan_limit_interval"),
        ({"method": "POST"}, "plan_feature_post"),
        ({"headers": {"Authorization": "secret"}}, "plan_feature_headers"),
        ({"monitor_type": "keyword", "keyword": "ok"}, "plan_feature_keyword"),
    ]:
        response = await client.post(
            "/api/sites",
            headers=auth_headers,
            json={"name": "restricted", "url": "https://8.8.8.8", **extra},
        )
        assert response.status_code in (402, 403), response.text
        assert response.json()["code"] == code


async def test_payment_idempotent_and_cancel(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "payment_provider", "dev")
    checkout = await client.post(
        "/api/billing/checkout", headers=auth_headers, json={"plan_code": "pro"}
    )
    assert checkout.status_code == 200, checkout.text
    payment = checkout.json()
    assert (await client.get(payment["url"])).status_code == 200
    first = await client.post(payment["url"])
    assert first.status_code == 200
    current = (await client.get("/api/billing/subscription", headers=auth_headers)).json()
    assert current["status"] == "active"
    from app.services.dev_payments import DevProvider

    payload = {"id": payment["payment_id"], "amount_kop": current["plan"]["price_kop"]}
    results = await asyncio.gather(
        *(
            client.post(
                "/api/billing/webhook/dev",
                json=payload,
                headers={"x-signature": DevProvider.signature(payment["payment_id"])},
            )
            for _ in range(3)
        )
    )
    assert all(r.status_code == 200 and not r.json()["activated"] for r in results)
    after = (await client.get("/api/billing/subscription", headers=auth_headers)).json()
    assert after["current_period_end"] == current["current_period_end"]
    invalid = await client.post("/api/billing/webhook/dev", json=payload)
    assert invalid.status_code == 403
    assert (await client.post("/api/billing/cancel", headers=auth_headers)).status_code == 204
    assert (await client.get("/api/billing/subscription", headers=auth_headers)).json()[
        "cancel_at_period_end"
    ]
    assert (await client.post("/api/billing/resume", headers=auth_headers)).status_code == 204
    assert not (await client.get("/api/billing/subscription", headers=auth_headers)).json()[
        "cancel_at_period_end"
    ]
    payments = (await client.get("/api/billing/payments", headers=auth_headers)).json()
    assert len(payments) == 1 and payments[0]["status"] == "succeeded"


async def test_dev_provider_disabled_outside_dev(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "payment_provider", "dev")
    monkeypatch.setattr(settings, "app_env", "production")
    response = await client.post(
        "/api/billing/checkout", headers=auth_headers, json={"plan_code": "pro"}
    )
    assert response.status_code == 404


async def test_retention_per_plan(client: httpx.AsyncClient, auth_headers: dict[str, str]) -> None:
    created = await client.post(
        "/api/sites", headers=auth_headers, json={"name": "history", "url": "https://8.8.8.8"}
    )
    site_id = created.json()["id"]
    async with session_factory() as session:
        session.add_all(
            [
                Check(site_id=site_id, checked_at=datetime.now(UTC) - timedelta(days=days), ok=True)
                for days in (1, 10, 100)
            ]
        )
        await session.commit()
        await billing_maintenance(session)
        assert await session.scalar(select(func.count(Check.id))) == 2
    await set_plan("free")
    async with session_factory() as session:
        await billing_maintenance(session)
        assert await session.scalar(select(func.count(Check.id))) == 1
        assert await session.scalar(select(func.count(DailyUptime.site_id))) == 1


async def test_non_admin_forbidden(client: httpx.AsyncClient, auth_headers: dict[str, str]) -> None:
    assert (await client.get("/api/admin/stats", headers=auth_headers)).status_code == 403
    assert (
        await client.post(
            "/api/admin/users/1/plan", headers=auth_headers, json={"plan_code": "business"}
        )
    ).status_code == 403


async def test_admin_plan_change(client: httpx.AsyncClient, auth_headers: dict[str, str]) -> None:
    async with session_factory() as session:
        user = await session.scalar(select(User))
        assert user
        user.is_admin = True
        await session.commit()
    response = await client.post(
        "/api/admin/users/1/plan", headers=auth_headers, json={"plan_code": "business"}
    )
    assert response.status_code == 204
    assert (await client.get("/api/billing/subscription", headers=auth_headers)).json()["plan"][
        "code"
    ] == "business"
    assert (await client.get("/api/admin/stats", headers=auth_headers)).json()["users"] == 1


async def test_feature_downgrade_preserves_configuration(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/sites",
        headers=auth_headers,
        json={
            "name": "Keyword",
            "url": "https://8.8.8.8",
            "monitor_type": "keyword",
            "keyword": "ready",
        },
    )
    site_id = created.json()["id"]
    await set_plan("free")
    paused = (await client.get(f"/api/sites/{site_id}", headers=auth_headers)).json()
    assert paused["paused"] and paused["pause_reason"] == "plan_feature_keyword"
    assert paused["keyword"] == "ready"
    await set_plan("pro")
    restored = (await client.get(f"/api/sites/{site_id}", headers=auth_headers)).json()
    assert not restored["paused"] and restored["keyword"] == "ready"
