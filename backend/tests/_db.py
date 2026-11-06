"""Helpers for tests that need the Postgres service from docker compose: throwaway databases,
raw asyncpg access and the Alembic config pointed at a given URL."""

import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import asyncpg
from alembic.config import Config
from sqlalchemy.engine import URL, make_url

from app.config import get_settings

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def dsn(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


async def admin(sql: str) -> None:
    conn = await asyncpg.connect(dsn(make_url(get_settings().database_url)))
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def fetch(url: URL, sql: str) -> list:
    conn = await asyncpg.connect(dsn(url))
    try:
        return await conn.fetch(sql)
    finally:
        await conn.close()


async def execute(url: URL, sql: str) -> None:
    conn = await asyncpg.connect(dsn(url))
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@contextmanager
def temp_database() -> Iterator[URL]:
    """An empty database that is dropped afterwards, even if the test fails."""
    name = f"series76_test_{uuid.uuid4().hex[:8]}"
    asyncio.run(admin(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(get_settings().database_url).set(database=name)
    finally:
        asyncio.run(admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


def alembic_config(url: URL) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    return cfg
