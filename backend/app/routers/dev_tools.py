from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.deps import CurrentUser, SessionDep
from app.errors import ApiError, ErrorResponse
from app.models import Site

router = APIRouter(prefix="/dev", tags=["dev"])


class DemoFilled(BaseModel):
    monitors: int
    heartbeat_url: str


@router.post(
    "/demo-data",
    response_model=DemoFilled,
    status_code=201,
    summary="Заполнить пустой аккаунт демо-историей",
    description="Только APP_ENV=dev. 90 дней проверок, инциденты, каналы и статус-страница.",
    responses={409: {"model": ErrorResponse}},
)
async def demo_data(user: CurrentUser, session: SessionDep) -> DemoFilled:
    existing = await session.scalar(select(func.count(Site.id)).where(Site.user_id == user.id))
    if existing:
        raise ApiError(409, "account_not_empty", "Демо-данные добавляются только в пустой аккаунт")
    await session.commit()
    from app.config import settings
    from app.demo import fill_current_account

    monitors, token = await fill_current_account(user.id)
    return DemoFilled(
        monitors=monitors,
        heartbeat_url=f"{settings.api_base_url.rstrip('/')}/api/heartbeat/{token}",
    )
