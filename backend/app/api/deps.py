"""FastAPI dependencies. Long-lived resources live on app.state, created in the lifespan."""

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.api.ratelimit import LoginRateLimiter
from app.config import Settings
from app.db import users
from app.db.models import User
from app.security import TokenExpired, TokenInvalid, decode_token
from app.services.conversation import ConversationService

_bearer = HTTPBearer(auto_error=False)  # missing header -> our 401 shape, not FastAPI's 403
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_service(request: Request) -> ConversationService:
    return request.app.state.service


def get_login_limiter(request: Request) -> LoginRateLimiter:
    return request.app.state.login_limiter


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """The user behind the Bearer token. The role is read from the database, so a role change
    or a deleted user takes effect before the token expires."""
    if credentials is None:
        raise ApiError(401, "not_authenticated", "Sign in to continue.", _CHALLENGE)
    secret = request.app.state.settings.jwt_secret.get_secret_value()
    try:
        claims = decode_token(credentials.credentials, secret)
    except TokenExpired:
        raise ApiError(
            401, "token_expired", "Your session expired. Sign in again.", _CHALLENGE
        ) from None
    except TokenInvalid:
        raise ApiError(401, "invalid_token", "Sign in again.", _CHALLENGE) from None
    user = await users.get_by_id(db, claims.user_id)
    if user is None:
        raise ApiError(401, "invalid_token", "Sign in again.", _CHALLENGE)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(role: str) -> Callable[..., Coroutine[Any, Any, User]]:
    async def dependency(user: CurrentUser) -> User:
        if user.role != role:
            raise ApiError(403, "forbidden", "You do not have access to this resource.")
        return user

    return dependency
