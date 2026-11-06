"""The Alembic migrations build the catalog schema on a throwaway database and tear it down."""

import asyncio

import asyncpg
import pytest
from alembic import command

from tests._db import alembic_config, execute, fetch

pytestmark = pytest.mark.db

TABLES_SQL = "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"


def test_upgrade_creates_catalog_and_downgrade_removes_it(db_url) -> None:
    cfg = alembic_config(db_url)
    command.upgrade(cfg, "head")

    tables = [r["tablename"] for r in asyncio.run(fetch(db_url, TABLES_SQL))]
    assert tables == ["actuators", "alembic_version", "electrical_ratings", "service_ratings"]
    ext = asyncio.run(fetch(db_url, "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"))
    assert ext
    idx = asyncio.run(
        fetch(db_url, "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_actuators_part_key_trgm'")
    )
    assert idx

    command.downgrade(cfg, "base")
    tables = [r["tablename"] for r in asyncio.run(fetch(db_url, TABLES_SQL))]
    assert tables == ["alembic_version"]


def test_models_match_migrations(db_url) -> None:
    cfg = alembic_config(db_url)
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
    command.upgrade(alembic_config(db_url), "head")
    asyncio.run(execute(db_url, ACTUATOR.format(voltage=24, phase="NULL")))
    insert = "INSERT INTO electrical_ratings (part_number, frequency_hz) VALUES ('T', NULL)"
    asyncio.run(execute(db_url, insert))
    with pytest.raises(asyncpg.UniqueViolationError):
        asyncio.run(execute(db_url, insert))


def test_phase_is_null_exactly_on_24v(db_url) -> None:
    command.upgrade(alembic_config(db_url), "head")
    with pytest.raises(asyncpg.CheckViolationError):
        asyncio.run(execute(db_url, ACTUATOR.format(voltage=24, phase=1)))
    with pytest.raises(asyncpg.CheckViolationError):
        asyncio.run(execute(db_url, ACTUATOR.format(voltage=110, phase="NULL")))
