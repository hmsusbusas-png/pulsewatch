from fastapi import APIRouter

from app.deps import CurrentUser, SessionDep
from app.schemas import TelegramLinkResponse, UserResponse
from app.security import generate_link_token
from app.services.telegram import fetch_bot_username, is_enabled

router = APIRouter(prefix="/me", tags=["me"])

DISABLED_INSTRUCTIONS = (
    "Telegram integration is disabled on this instance because TELEGRAM_BOT_TOKEN is not set. "
    "The token below becomes usable once the bot is configured."
)
LINK_INSTRUCTIONS = (
    "Open the bot in Telegram and send /start followed by this token. "
    "The token is single-use and replaced every time you open this screen."
)


@router.get("", response_model=UserResponse)
async def read_me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


@router.get("/telegram/link", response_model=TelegramLinkResponse)
async def create_telegram_link(user: CurrentUser, session: SessionDep) -> TelegramLinkResponse:
    user.telegram_link_token = generate_link_token()
    await session.commit()
    if not is_enabled():
        return TelegramLinkResponse(
            link_token=user.telegram_link_token,
            bot_username=None,
            instructions=DISABLED_INSTRUCTIONS,
        )
    return TelegramLinkResponse(
        link_token=user.telegram_link_token,
        bot_username=await fetch_bot_username(),
        instructions=LINK_INSTRUCTIONS,
    )
