import hashlib
import hmac

from app.config import settings
from app.errors import ApiError
from app.services.payments import CreatedPayment, PaidPayment


class DevProvider:
    def __init__(self) -> None:
        if settings.app_env != "dev":
            raise ApiError(404, "not_found", "Ресурс не найден")

    @staticmethod
    def signature(payment_id: str) -> str:
        return hmac.new(
            settings.jwt_secret.encode(), payment_id.encode(), hashlib.sha256
        ).hexdigest()

    async def create_payment(self, amount_kop: int, order_id: str) -> CreatedPayment:
        payment_id = f"dev_{order_id}"
        return CreatedPayment(
            payment_id,
            f"{settings.api_base_url}/api/billing/dev/{payment_id}?signature={self.signature(payment_id)}",
        )

    async def verify_signature(self, payload: dict[str, object], signature: str) -> bool:
        return hmac.compare_digest(self.signature(str(payload.get("id", ""))), signature)

    async def parse_webhook(self, payload: dict[str, object]) -> PaidPayment:
        amount = payload.get("amount_kop")
        if type(amount) is not int:
            raise ApiError(400, "invalid_payment", "Некорректная сумма платежа")
        return PaidPayment(str(payload["id"]), amount, "succeeded", payload)
