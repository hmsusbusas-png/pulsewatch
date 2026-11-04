import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import httpx

from app.config import settings
from app.errors import ApiError


@dataclass(frozen=True)
class CreatedPayment:
    id: str
    url: str


@dataclass(frozen=True)
class PaidPayment:
    id: str
    amount_kop: int
    status: str
    raw: dict[str, object]
    currency: str = "RUB"


class PaymentProvider(Protocol):
    async def create_payment(self, amount_kop: int, order_id: str) -> CreatedPayment: ...

    async def parse_webhook(self, payload: dict[str, object]) -> PaidPayment: ...

    async def verify_signature(self, payload: dict[str, object], signature: str) -> bool: ...


class YooKassaProvider:
    def __init__(self) -> None:
        if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
            raise ApiError(503, "payments_unavailable", "Платёжный провайдер не настроен")

    async def create_payment(self, amount_kop: int, order_id: str) -> CreatedPayment:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.post(
                "https://api.yookassa.ru/v3/payments",
                auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
                headers={"Idempotence-Key": order_id},
                json={
                    "amount": {
                        "value": f"{amount_kop // 100}.{amount_kop % 100:02d}",
                        "currency": "RUB",
                    },
                    "capture": True,
                    "confirmation": {
                        "type": "redirect",
                        "return_url": f"{settings.frontend_base_url}/billing",
                    },
                    "description": "Подписка Pulsewatch",
                },
            )
            response.raise_for_status()
            data = response.json()
            if data.get("test") is not True:
                raise ApiError(
                    403, "live_payments_disabled", "Используйте тестовый магазин YooKassa"
                )
            return CreatedPayment(data["id"], data["confirmation"]["confirmation_url"])

    async def verify_signature(self, payload: dict[str, object], signature: str) -> bool:
        obj = payload.get("object")
        return isinstance(obj, dict) and isinstance(obj.get("id"), str)

    async def parse_webhook(self, payload: dict[str, object]) -> PaidPayment:
        obj = payload.get("object")
        if not isinstance(obj, dict):
            raise ApiError(400, "invalid_payment", "Некорректное уведомление платежа")
        try:
            payment_id = str(uuid.UUID(str(obj.get("id"))))
        except ValueError as exc:
            raise ApiError(400, "invalid_payment", "Некорректный идентификатор платежа") from exc
        refund = payload.get("event") == "refund.succeeded"
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            endpoint = "refunds" if refund else "payments"
            response = await client.get(
                f"https://api.yookassa.ru/v3/{endpoint}/{payment_id}",
                auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
            )
            response.raise_for_status()
            data = response.json()
            if refund:
                if data.get("id") != payment_id or data.get("status") != "succeeded":
                    raise ApiError(400, "invalid_payment", "Возврат не подтверждён")
                payment_id = str(uuid.UUID(str(data["payment_id"])))
                original = await client.get(
                    f"https://api.yookassa.ru/v3/payments/{payment_id}",
                    auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
                )
                original.raise_for_status()
                if original.json().get("test") is not True:
                    raise ApiError(400, "invalid_payment", "Возврат не тестового платежа")
            elif data.get("id") != payment_id or data.get("test") is not True:
                raise ApiError(400, "invalid_payment", "Платёж не прошёл проверку")
        if data["amount"]["currency"] != "RUB":
            raise ApiError(400, "invalid_payment", "Некорректная валюта")
        amount = Decimal(data["amount"]["value"]) * 100
        if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
            raise ApiError(400, "invalid_payment", "Некорректная сумма")
        return PaidPayment(payment_id, int(amount), "refunded" if refund else data["status"], data)


def payment_provider(name: str) -> PaymentProvider:
    if name == "dev" and settings.app_env == "dev":
        from app.services.dev_payments import DevProvider

        return DevProvider()
    if name == "yookassa":
        return YooKassaProvider()
    raise ApiError(404, "provider_not_found", "Платёжный провайдер не найден")
