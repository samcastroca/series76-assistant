"""Shared fixtures. `catalog_db_url` is a throwaway database migrated to head and loaded with the
real data/processed/catalog.json; requires the Postgres service from docker compose."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.engine import make_engine, make_sessionmaker
from ingest.load import run_load
from tests.test_migrations import _admin, _alembic


@pytest.fixture(scope="module")
def catalog_db_url() -> Iterator[str]:
    name = f"series76_test_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    try:
        url = make_url(get_settings().database_url).set(database=name)
        command.upgrade(_alembic(url), "head")
        rendered = url.render_as_string(hide_password=False)
        asyncio.run(run_load(rendered))
        yield rendered
    finally:
        asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture
async def catalog_session(catalog_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = make_engine(catalog_db_url)
    try:
        async with make_sessionmaker(engine)() as session:
            yield session
    finally:
        await engine.dispose()
