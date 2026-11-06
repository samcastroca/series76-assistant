"""FastAPI application. The engine, the agent and the conversation service are created once in
the lifespan and shared by every request. Run with `uvicorn app.main:create_app --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langchain_core.language_models import BaseChatModel

from app.agent.factory import build_agent
from app.api import errors
from app.api.routes import debug_router, router
from app.config import Settings, get_settings
from app.db.engine import make_engine, make_sessionmaker
from app.services.conversation import ConversationService


def create_app(settings: Settings | None = None, model: BaseChatModel | None = None) -> FastAPI:
    """`model` overrides the configured chat model (tests pass a ScriptedFakeModel)."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        sessionmaker = make_sessionmaker(engine)
        agent = build_agent(settings, model)
        app.state.settings = settings
        app.state.sessionmaker = sessionmaker
        app.state.service = ConversationService(agent, sessionmaker, settings)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Series 76 Technical Assistant", version="0.1.0", lifespan=lifespan)
    errors.install(app)
    app.include_router(router)
    if settings.debug_api:
        app.include_router(debug_router)
    return app
