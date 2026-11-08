"""The demo-user seed: idempotent, the right roles, passwords from settings, and a clear error
when one is missing."""

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import users
from app.db.models import User
from app.db.seed import SeedError, seed_users
from app.security import verify_password

pytestmark = pytest.mark.db

PASSWORDS = {
    "seed_engineer1_password": "pw-engineer-1",
    "seed_engineer2_password": "pw-engineer-2",
    "seed_developer_password": "pw-developer",
}


@pytest.fixture
async def empty_users(db_session):
    await db_session.execute(User.__table__.delete())
    await db_session.commit()
    return db_session


async def test_seed_is_idempotent_and_assigns_roles(empty_users) -> None:
    db = empty_users
    settings = Settings(_env_file=None, **PASSWORDS)
    assert len(await seed_users(db, settings)) == 3
    assert await seed_users(db, settings) == []

    rows = {u.email: u for u in (await db.scalars(select(User))).all()}
    assert {e: u.role for e, u in rows.items()} == {
        "engineer1@example.com": "engineer",
        "engineer2@example.com": "engineer",
        "developer@example.com": "developer",
    }
    assert verify_password("pw-developer", rows["developer@example.com"].password_hash)


async def test_seed_does_not_overwrite_an_existing_password(empty_users) -> None:
    db = empty_users
    await seed_users(db, Settings(_env_file=None, **PASSWORDS))
    changed = {**PASSWORDS, "seed_engineer1_password": "something-else"}
    await seed_users(db, Settings(_env_file=None, **changed))
    user = await users.get_by_email(db, "ENGINEER1@example.com")
    assert verify_password("pw-engineer-1", user.password_hash)


async def test_missing_password_fails_before_writing(empty_users) -> None:
    db = empty_users
    partial = {k: v for k, v in PASSWORDS.items() if k != "seed_developer_password"}
    with pytest.raises(SeedError, match="SEED_DEVELOPER_PASSWORD"):
        await seed_users(db, Settings(_env_file=None, **partial))
    assert (await db.scalars(select(User))).all() == []
