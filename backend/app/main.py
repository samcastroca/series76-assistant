"""FastAPI application. The engine, the agent, the conversation service and the login limiter are
created once in the lifespan and shared by every request. Run with
`uvicorn app.main:create_app --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langchain_core.language_models import BaseChatModel

from app.agent.factory import build_agent
from app.api import auth, errors, routes
from app.api.ratelimit import LoginRateLimiter
from app.config import Settings, get_settings
from app.db.engine import make_engine, make_sessionmaker
from app.security import MIN_SECRET_LENGTH
from app.services.conversation import ConversationService


def create_app(settings: Settings | None = None, model: BaseChatModel | None = None) -> FastAPI:
    """`model` overrides the configured chat model (tests pass a ScriptedFakeModel)."""
    settings = settings or get_settings()
    secret = settings.jwt_secret.get_secret_value() if settings.jwt_secret else ""
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(
            f"JWT_SECRET is required and must be at least {MIN_SECRET_LENGTH} characters"
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        sessionmaker = make_sessionmaker(engine)
        agent = build_agent(settings, model)
        app.state.settings = settings
        app.state.sessionmaker = sessionmaker
        app.state.service = ConversationService(agent, sessionmaker, settings)
        app.state.login_limiter = LoginRateLimiter(
            settings.login_max_attempts, settings.login_window_minutes * 60
        )
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Series 76 Technical Assistant", version="0.1.0", lifespan=lifespan)
    errors.install(app)
    app.include_router(auth.public_router)
    app.include_router(routes.public_router)
    app.include_router(auth.router)
    app.include_router(routes.router)
    return app
