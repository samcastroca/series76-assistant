"""Shared fixtures for tests that need the Postgres service from docker compose."""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest
from alembic import command
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.fake import ScriptedFakeModel
from app.config import Settings
from app.db.engine import make_engine, make_sessionmaker
from app.main import create_app
from ingest.load import run_load
from tests._api import JWT_SECRET, PASSWORD, USERS, bearer
from tests._db import alembic_config, create_user, temp_database


@pytest.fixture
def db_url() -> Iterator[URL]:
    """An empty throwaway database, one per test."""
    with temp_database() as url:
        yield url


@pytest.fixture(scope="module")
def catalog_db_url() -> Iterator[str]:
    """A throwaway database migrated to head and loaded with the real catalog.json."""
    with temp_database() as url:
        command.upgrade(alembic_config(url), "head")
        rendered = url.render_as_string(hide_password=False)
        asyncio.run(run_load(rendered))
        yield rendered


@pytest.fixture
async def catalog_session(catalog_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = make_engine(catalog_db_url)
    try:
        async with make_sessionmaker(engine)() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(catalog_db_url: str) -> AsyncIterator[AsyncSession]:
    """A session on the catalog database with the conversation tables emptied first."""
    engine = make_engine(catalog_db_url)
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql("TRUNCATE users, sessions CASCADE")
        async with make_sessionmaker(engine)() as session:
            yield session
    finally:
        await engine.dispose()


Client = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
async def client(catalog_db_url: str) -> AsyncIterator[Client]:
    """Starts app instances on the catalog database, emptied of users and conversations and
    with the three test users of tests._api.USERS."""
    engine = make_engine(catalog_db_url)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("TRUNCATE users, sessions CASCADE")
    async with make_sessionmaker(engine)() as db:
        for email, role in USERS.values():
            await create_user(db, email, role, PASSWORD)
    await engine.dispose()

    @asynccontextmanager
    async def start(
        steps: list, as_user: str | None = "engineer1", **overrides
    ) -> AsyncIterator[httpx.AsyncClient]:
        """A fresh app instance (new engine, agent and service), like a process start, with a
        client logged in as `as_user` (None: no Authorization header)."""
        settings = Settings(
            _env_file=None,
            database_url=catalog_db_url,
            llm_provider="fake",  # independent of the developer's shell
            agent_model=None,
            fallback_model=None,
            model_call_limit=4,
            tool_call_limit=4,
            jwt_secret=JWT_SECRET,
            **overrides,
        )
        app = create_app(settings, ScriptedFakeModel(steps=steps))
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                if as_user is not None:
                    c.headers.update(await bearer(c, as_user))
                yield c

    yield start
