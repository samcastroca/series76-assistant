"""`load` upserts catalog.json idempotently. Uses a synthetic catalog (made-up values) on a
throwaway database; requires the Postgres service from docker compose."""

import asyncio
from pathlib import Path

import pytest
from alembic import command

from ingest.build import build_table, catalog_json
from ingest.extracted import ExtractionRun, MotorPowerGroup, column_key, row_columns
from ingest.load import run_load
from ingest.spec import load_spec
from tests.test_migrations import _alembic, _fetch, db_url  # noqa: F401  (fixture)

pytestmark = pytest.mark.db

SPEC = load_spec()
COUNTS_SQL = """SELECT (SELECT count(*) FROM actuators) AS a,
                       (SELECT count(*) FROM service_ratings) AS s,
                       (SELECT count(*) FROM electrical_ratings) AS e,
                       (SELECT count(*) FROM actuators WHERE NOT data_available) AS na"""


def _catalog(tmp_path: Path) -> Path:
    """Two 3-phase rows (one N/A) and two 24 V rows, with made-up values."""
    records = []
    for table_id, cell, na_first in (("wp_220v_3ph", "10", True), ("xp_24v_on_off", "10", False)):
        table = next(t for t in SPEC.tables if t.id == table_id)
        keys = [column_key(t) for t in row_columns(SPEC, table)][1:]
        variant = "A" if table.enclosure == "weatherproof" else "X"
        pns = [f"76{n}{variant}00{table.part_suffix}" for n in (1, 2)]
        data = {k: cell for k in keys} | {k: "885" for k in keys if k.endswith("torque_inlb")}
        data |= {k: "100" for k in keys if k.endswith("torque_nm")}
        rows = [
            {"part_number": pns[0]} | ({k: "N/A" for k in keys} if na_first else data),
            {"part_number": pns[1]} | data,
        ]
        first = pns[1] if na_first else pns[0]
        run = ExtractionRun(
            caption=table.title,
            rows=rows,
            motor_power_groups=[
                MotorPowerGroup(watts="25", first_part_number=first, last_part_number=pns[1])
            ],
        )
        records += build_table(SPEC, table, run)
    path = tmp_path / "catalog.json"
    path.write_text(catalog_json(records), encoding="utf-8")
    return path


def test_load_is_idempotent(db_url, tmp_path: Path) -> None:  # noqa: F811
    command.upgrade(_alembic(db_url), "head")
    url = db_url.render_as_string(hide_password=False)
    catalog = _catalog(tmp_path)

    assert asyncio.run(run_load(url, catalog)) == 4
    first = dict(asyncio.run(_fetch(db_url, COUNTS_SQL))[0])
    asyncio.run(run_load(url, catalog))
    second = dict(asyncio.run(_fetch(db_url, COUNTS_SQL))[0])

    # 3 data rows: 3 service rows; 3-phase has 60/50 Hz rows, 24 V one NULL-frequency row.
    assert first == second == {"a": 4, "s": 3, "e": 2 + 1 + 1, "na": 1}
