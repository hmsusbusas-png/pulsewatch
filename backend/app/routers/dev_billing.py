from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.deps import SessionDep
from app.errors import ApiError
from app.models import Payment
from app.services.billing import activate_payment
from app.services.dev_payments import DevProvider

router = APIRouter(tags=["dev billing"])


@router.get("/billing/dev/{payment_id}", response_class=HTMLResponse)
async def dev_page(payment_id: str, signature: str) -> str:
    provider = DevProvider()
    if not await provider.verify_signature({"id": payment_id}, signature):
        raise ApiError(403, "invalid_signature", "Ссылка недействительна")
    return (
        '<html lang="ru"><meta charset="utf-8"><title>Тестовый платёж</title>'
        f'<form method="post" action="?signature={signature}">'
        "<button>Подтвердить тестовую оплату</button></form></html>"
    )


@router.post("/billing/dev/{payment_id}", response_class=HTMLResponse)
async def dev_confirm(payment_id: str, signature: str, session: SessionDep) -> str:
    provider = DevProvider()
    if not await provider.verify_signature({"id": payment_id}, signature):
        raise ApiError(403, "invalid_signature", "Ссылка недействительна")
    payment = await session.scalar(
        select(Payment).where(Payment.provider_payment_id == payment_id, Payment.provider == "dev")
    )
    if payment is None:
        raise ApiError(404, "payment_not_found", "Платёж не найден")
    await activate_payment(session, payment_id, "dev", payment.amount_kop, {"id": payment_id})
    await session.commit()
    return (
        '<html lang="ru"><meta charset="utf-8"><title>Оплата</title>Тестовый платёж принят.</html>'
    )
