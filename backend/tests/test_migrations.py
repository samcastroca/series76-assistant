"""The Alembic migrations build the catalog schema on a throwaway database and tear it down."""

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.config import get_settings

pytestmark = pytest.mark.db

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _dsn(url) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


async def _admin(sql: str) -> None:
    conn = await asyncpg.connect(_dsn(make_url(get_settings().database_url)))
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def _fetch(url, sql: str) -> list:
    conn = await asyncpg.connect(_dsn(url))
    try:
        return await conn.fetch(sql)
    finally:
        await conn.close()


async def _execute(url, sql: str) -> None:
    conn = await asyncpg.connect(_dsn(url))
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture
def db_url() -> Iterator:
    name = f"series76_test_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(get_settings().database_url).set(database=name)
    finally:
        asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


def _alembic(url) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
    return cfg


TABLES_SQL = "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"


def test_upgrade_creates_catalog_and_downgrade_removes_it(db_url) -> None:
    cfg = _alembic(db_url)
    command.upgrade(cfg, "head")

    tables = [r["tablename"] for r in asyncio.run(_fetch(db_url, TABLES_SQL))]
    assert tables == ["actuators", "alembic_version", "electrical_ratings", "service_ratings"]
    ext = asyncio.run(_fetch(db_url, "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"))
    assert ext
    idx = asyncio.run(
        _fetch(db_url, "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_actuators_part_key_trgm'")
    )
    assert idx

    command.downgrade(cfg, "base")
    tables = [r["tablename"] for r in asyncio.run(_fetch(db_url, TABLES_SQL))]
    assert tables == ["alembic_version"]


def test_models_match_migrations(db_url) -> None:
    cfg = _alembic(db_url)
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if autogenerate would emit any operation


# Synthetic rows: they exercise constraints, not catalog content.
ACTUATOR = (
    "INSERT INTO actuators (part_number, part_key, series_size, variant, enclosure, "
    "certifications, csa_certified, voltage_v, phase, current_type, data_available, "
    "source_table, source_page) VALUES ('T', 'T', 1, 'X', 'explosionproof', '{{}}', true, "
    "{voltage}, {phase}, 'DC', true, 'test', 1)"
)


def test_one_null_frequency_row_per_part(db_url) -> None:
    command.upgrade(_alembic(db_url), "head")
    asyncio.run(_execute(db_url, ACTUATOR.format(voltage=24, phase="NULL")))
    insert = "INSERT INTO electrical_ratings (part_number, frequency_hz) VALUES ('T', NULL)"
    asyncio.run(_execute(db_url, insert))
    with pytest.raises(asyncpg.UniqueViolationError):
        asyncio.run(_execute(db_url, insert))


def test_phase_is_null_exactly_on_24v(db_url) -> None:
    command.upgrade(_alembic(db_url), "head")
    with pytest.raises(asyncpg.CheckViolationError):
        asyncio.run(_execute(db_url, ACTUATOR.format(voltage=24, phase=1)))
    with pytest.raises(asyncpg.CheckViolationError):
        asyncio.run(_execute(db_url, ACTUATOR.format(voltage=110, phase="NULL")))
