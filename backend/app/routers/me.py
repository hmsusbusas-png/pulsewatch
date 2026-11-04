from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter

from app.deps import CurrentUser, SessionDep
from app.errors import ApiError
from app.schemas import MeUpdate, TelegramLinkResponse, UserResponse
from app.services import audit

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=UserResponse)
async def read_me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


@router.patch(
    "",
    response_model=UserResponse,
    summary="Настройки профиля",
    description="Часовой пояс IANA, в нём фронтенд показывает время.",
)
async def update_me(payload: MeUpdate, user: CurrentUser, session: SessionDep) -> UserResponse:
    try:
        ZoneInfo(payload.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ApiError(422, "invalid_timezone", "Неизвестный часовой пояс") from exc
    if payload.timezone != user.timezone:
        audit.record(
            session,
            user.id,
            "account.timezone",
            "account",
            None,
            [{"field": "timezone", "old": user.timezone, "new": payload.timezone}],
        )
        user.timezone = payload.timezone
        await session.commit()
    return UserResponse.model_validate(user)


@router.get("/telegram/link", response_model=TelegramLinkResponse)
async def create_telegram_link(user: CurrentUser, session: SessionDep) -> TelegramLinkResponse:
    from app.routers.telegram import new_link

    link = await new_link(user, session)
    return TelegramLinkResponse(
        link_token=link.link_token, bot_username=link.bot_username, instructions=link.instructions
    )
