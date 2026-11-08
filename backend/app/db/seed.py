"""Create the three demo users if they do not exist: `python -m app.db.seed`.

Idempotent: an existing user (same email) is left as it is, password included. Passwords come
from SEED_ENGINEER1_PASSWORD, SEED_ENGINEER2_PASSWORD and SEED_DEVELOPER_PASSWORD; docker compose
supplies development defaults, the code has none."""

import asyncio
import logging
import sys

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import users
from app.db.engine import make_engine, make_sessionmaker
from app.security import hash_password

logger = logging.getLogger(__name__)

# (email, role, settings field holding the password)
DEMO_USERS = (
    ("engineer1@example.com", "engineer", "seed_engineer1_password"),
    ("engineer2@example.com", "engineer", "seed_engineer2_password"),
    ("developer@example.com", "developer", "seed_developer_password"),
)


class SeedError(Exception):
    pass


def demo_passwords(settings: Settings) -> list[tuple[str, str, str]]:
    """(email, role, password) for each demo user; fails if any password is not set."""
    out = []
    missing = []
    for email, role, field in DEMO_USERS:
        secret: SecretStr | None = getattr(settings, field)
        if secret is None:
            missing.append(field.upper())
        else:
            out.append((email, role, secret.get_secret_value()))
    if missing:
        raise SeedError(f"Set {', '.join(missing)} to seed the demo users")
    return out


async def seed_users(db: AsyncSession, settings: Settings) -> list[str]:
    """Insert the missing demo users and commit. Returns the emails that were created."""
    created = []
    for email, role, password in demo_passwords(settings):
        if await users.get_by_email(db, email) is not None:
            continue  # skip the Argon2 hash for users that already exist
        if await users.insert_if_missing(db, email, hash_password(password), role):
            created.append(email)
    await db.commit()
    return created


async def run_seed(settings: Settings) -> list[str]:
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as db:
            return await seed_users(db, settings)
    finally:
        await engine.dispose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        created = asyncio.run(run_seed(get_settings()))
    except SeedError as exc:
        logger.error("seed: %s", exc)
        return 1
    logger.info("seed: created %s", ", ".join(created) if created else "nothing (users exist)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
