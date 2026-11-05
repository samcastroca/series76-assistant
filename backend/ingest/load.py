"""`load` stage: data/processed/catalog.json -> Postgres. Idempotent upsert in one transaction;
running it twice leaves the database unchanged."""

import json
from pathlib import Path

from pydantic import TypeAdapter
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import get_settings
from app.db.engine import make_engine
from app.db.models import Actuator, ElectricalRating, ServiceRating
from ingest.build import DATA_DIR
from ingest.records import ActuatorRecord

CATALOG_PATH = DATA_DIR / "processed" / "catalog.json"
_RECORDS = TypeAdapter(list[ActuatorRecord])


def read_catalog(path: Path = CATALOG_PATH) -> list[ActuatorRecord]:
    return _RECORDS.validate_python(json.loads(path.read_text("utf-8"))["actuators"])


def _upsert(model, rows: list[dict], conflict: dict):
    stmt = insert(model).values(rows)
    keys = set(conflict.get("index_elements", ()))
    updates = {c: stmt.excluded[c] for c in rows[0] if c not in keys}
    return stmt.on_conflict_do_update(**conflict, set_=updates)


async def upsert_catalog(conn: AsyncConnection, records: list[ActuatorRecord]) -> None:
    children = {"service_ratings", "electrical_ratings"}
    actuators = [r.model_dump(exclude=children) for r in records]
    services = [
        {"part_number": r.part_number, **s.model_dump()} for r in records for s in r.service_ratings
    ]
    electrical = [
        {"part_number": r.part_number, **e.model_dump()}
        for r in records
        for e in r.electrical_ratings
    ]
    await conn.execute(_upsert(Actuator, actuators, {"index_elements": ["part_number"]}))
    if services:
        conflict = {"index_elements": ["part_number", "service_type"]}
        await conn.execute(_upsert(ServiceRating, services, conflict))
    if electrical:
        conflict = {"constraint": "uq_electrical_ratings_part_frequency"}
        await conn.execute(_upsert(ElectricalRating, electrical, conflict))


async def run_load(database_url: str | None = None, catalog: Path = CATALOG_PATH) -> int:
    records = read_catalog(catalog)
    engine = make_engine(database_url or get_settings().database_url)
    try:
        async with engine.begin() as conn:
            await upsert_catalog(conn, records)
    finally:
        await engine.dispose()
    return len(records)
