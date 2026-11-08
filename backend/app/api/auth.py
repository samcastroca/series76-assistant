"""Login and current user. Login is the only public /api route; attempts are limited per client
IP and email (ADR 0006). Argon2 runs in a worker thread so a login never blocks the event loop."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, get_current_user, get_db, get_login_limiter, get_settings
from app.api.errors import ApiError
from app.api.ratelimit import LoginRateLimiter
from app.config import Settings
from app.db import users
from app.schemas.auth import LoginRequest, LoginResponse, UserOut
from app.security import create_token, verify_password

public_router = APIRouter()
router = APIRouter(dependencies=[Depends(get_current_user)])


@public_router.post("/api/auth/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    limiter: Annotated[LoginRateLimiter, Depends(get_login_limiter)],
) -> LoginResponse:
    key = (request.client.host if request.client else "unknown", users.normalize_email(body.email))
    retry_after = limiter.acquire(key)  # counted before the password check: no race
    if retry_after is not None:
        raise ApiError(
            429,
            "too_many_attempts",
            "Too many failed sign-in attempts. Try again later.",
            {"Retry-After": str(retry_after)},
        )
    user = await users.get_by_email(db, body.email)
    stored_hash = user.password_hash if user else None
    if not await run_in_threadpool(verify_password, body.password, stored_hash) or user is None:
        raise ApiError(
            401, "invalid_credentials", "Wrong email or password.", {"WWW-Authenticate": "Bearer"}
        )
    limiter.reset(key)
    token = create_token(
        user.id, user.role, settings.jwt_secret.get_secret_value(), settings.jwt_expire_minutes
    )
    return LoginResponse(access_token=token, user=UserOut(email=user.email, role=user.role))


@router.get("/api/auth/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut(email=user.email, role=user.role)
