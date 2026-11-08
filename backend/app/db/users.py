"""User repository: parameterized reads and the idempotent insert used by the seed. Emails are
stored and looked up in lowercase. Functions never commit."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == normalize_email(email)))


async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def insert_if_missing(db: AsyncSession, email: str, password_hash: str, role: str) -> bool:
    """Create the user unless the email exists; an existing user is left untouched. True if
    it was created."""
    stmt = (
        insert(User)
        .values(
            id=uuid.uuid4(), email=normalize_email(email), password_hash=password_hash, role=role
        )
        .on_conflict_do_nothing(index_elements=[User.email])
    )
    result = await db.execute(stmt)
    return result.rowcount > 0
