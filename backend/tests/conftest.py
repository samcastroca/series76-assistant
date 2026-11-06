"""Shared fixtures for tests that need the Postgres service from docker compose."""

import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import make_engine, make_sessionmaker
from ingest.load import run_load
from tests._db import alembic_config, temp_database


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
