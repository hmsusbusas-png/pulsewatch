from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.errors import ApiError
from app.models import User
from app.security import decode_access_token

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(request: Request, session: SessionDep) -> User:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "unauthorized", "Access token is missing")
    user_id = decode_access_token(token)
    if user_id is None:
        raise ApiError(401, "unauthorized", "Access token is invalid or expired")
    user = await session.get(User, user_id)
    if user is None:
        raise ApiError(401, "unauthorized", "User no longer exists")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
