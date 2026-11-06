"""Catalog repository: parameterized reads only. Filtering and ranking live in
app/tools/matching.py; this module fetches rows with their ratings by key, prefix or trigram
similarity."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Actuator

_WITH_RATINGS = (selectinload(Actuator.service_ratings), selectinload(Actuator.electrical_ratings))


def _actuators():
    return select(Actuator).options(*_WITH_RATINGS)


async def get_by_key(session: AsyncSession, key: str) -> Actuator | None:
    return await session.scalar(_actuators().where(Actuator.part_key == key))


async def find_by_prefix(session: AsyncSession, key: str) -> Sequence[Actuator]:
    stmt = (
        _actuators()
        .where(Actuator.part_key.startswith(key, autoescape=True))
        .order_by(Actuator.part_number)
    )
    return (await session.scalars(stmt)).all()


async def suggest_similar(session: AsyncSession, key: str, limit: int = 5) -> Sequence[Actuator]:
    """Part numbers whose key is trigram-similar (pg_trgm `%`, default threshold 0.3)."""
    similarity = func.similarity(Actuator.part_key, key)
    stmt = (
        _actuators()
        .where(Actuator.part_key.op("%")(key))
        .order_by(similarity.desc(), Actuator.part_number)
        .limit(limit)
    )
    return (await session.scalars(stmt)).all()


async def list_actuators(session: AsyncSession, with_data_only: bool = False) -> Sequence[Actuator]:
    stmt = _actuators().order_by(Actuator.part_number)
    if with_data_only:
        stmt = stmt.where(Actuator.data_available)
    return (await session.scalars(stmt)).all()
