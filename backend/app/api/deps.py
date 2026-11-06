"""FastAPI dependencies. Long-lived resources live on app.state, created in the lifespan."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services.conversation import ConversationService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_service(request: Request) -> ConversationService:
    return request.app.state.service
