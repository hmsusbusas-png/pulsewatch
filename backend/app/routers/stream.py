import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete

from app.deps import CurrentUser, SessionDep
from app.errors import ApiError, ErrorResponse
from app.models import StreamToken
from app.security import hash_refresh_token
from app.services.events import EVENT_KINDS, event_stream, hub

router = APIRouter(prefix="/stream", tags=["stream"])
TOKEN_SECONDS = 60


class StreamTokenResponse(BaseModel):
    token: str
    expires_in: int


@router.post(
    "/token",
    response_model=StreamTokenResponse,
    summary="Токен для потока событий",
    description="Одноразовый, живёт 60 секунд. EventSource не умеет передавать заголовки, "
    "В URL передаётся только одноразовый токен.",
)
async def issue_token(user: CurrentUser, session: SessionDep) -> StreamTokenResponse:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    await session.execute(delete(StreamToken).where(StreamToken.expires_at < now))
    session.add(
        StreamToken(
            token_hash=hash_refresh_token(token),
            user_id=user.id,
            expires_at=now + timedelta(seconds=TOKEN_SECONDS),
        )
    )
    await session.commit()
    return StreamTokenResponse(token=token, expires_in=TOKEN_SECONDS)


@router.get(
    "",
    summary="Server-Sent Events",
    description=(
        "События: "
        + ", ".join(EVENT_KINDS)
        + ", а также resync (при переполнении очереди нужно перезапросить данные) и close. "
        "Комментарий-heartbeat каждые 20 секунд. Не более STREAM_MAX_PER_USER соединений."
    ),
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}},
        401: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)
async def stream(
    session: SessionDep, token: str = Query(min_length=20, max_length=100)
) -> StreamingResponse:
    user_id = await session.scalar(
        delete(StreamToken)
        .where(
            StreamToken.token_hash == hash_refresh_token(token),
            StreamToken.expires_at > datetime.now(UTC),
        )
        .returning(StreamToken.user_id)
    )
    await session.commit()
    if user_id is None:
        raise ApiError(401, "invalid_token", "Токен потока недействителен или уже использован")
    queue = await hub.subscribe(user_id)
    return StreamingResponse(
        event_stream(user_id, queue),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
