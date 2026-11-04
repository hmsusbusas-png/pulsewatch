import json
import uuid

import httpx
import pytest

from app.config import settings


async def test_yookassa_confirms_status_via_api(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "payment_provider", "yookassa")
    monkeypatch.setattr(settings, "yookassa_shop_id", "test-shop")
    monkeypatch.setattr(settings, "yookassa_secret_key", "test-key")
    payment_id = str(uuid.uuid4())
    state = {"status": "pending", "test": True}
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["authorization"].startswith("Basic ")
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["amount"] == {"value": "990.00", "currency": "RUB"}
            assert request.headers["idempotence-key"]
        return httpx.Response(
            200,
            json={
                "id": payment_id,
                **state,
                "amount": {"value": "990.00", "currency": "RUB"},
                "confirmation": {"confirmation_url": "https://yookassa.ru/test-payment"},
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.services.payments.httpx.AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    checkout = await client.post(
        "/api/billing/checkout", headers=auth_headers, json={"plan_code": "pro"}
    )
    assert checkout.status_code == 200, checkout.text
    forged = {"object": {"id": payment_id, "status": "succeeded", "amount": {"value": "0.01"}}}
    pending = await client.post("/api/billing/webhook/yookassa", json=forged)
    assert pending.status_code == 200 and not pending.json()["activated"]
    assert (await client.get("/api/billing/subscription", headers=auth_headers)).json()[
        "status"
    ] == "trialing"
    state["status"] = "succeeded"
    paid = await client.post("/api/billing/webhook/yookassa", json=forged)
    assert paid.status_code == 200 and paid.json()["activated"]
    assert len(calls) == 3 and calls[-1].method == "GET"
    replay = await client.post("/api/billing/webhook/yookassa", json=forged)
    assert not replay.json()["activated"]
    state["test"] = False
    assert (await client.post("/api/billing/webhook/yookassa", json=forged)).status_code == 400


async def test_payment_bad_amount_and_provider(
    client: httpx.AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.dev_payments import DevProvider

    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "payment_provider", "dev")
    checkout = (
        await client.post("/api/billing/checkout", headers=auth_headers, json={"plan_code": "pro"})
    ).json()
    payment_id = checkout["payment_id"]
    response = await client.post(
        "/api/billing/webhook/dev",
        json={"id": payment_id, "amount_kop": 1},
        headers={"x-signature": DevProvider.signature(payment_id)},
    )
    assert response.status_code == 400 and response.json()["code"] == "payment_amount_mismatch"
    assert (await client.post("/api/billing/webhook/unknown", json={})).status_code == 404
