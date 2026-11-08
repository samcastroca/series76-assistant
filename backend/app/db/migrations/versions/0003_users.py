"""Users table; every session belongs to a user.

Sessions without an owner (created before authentication existed) are deleted, with their
messages and traces, because user_id becomes NOT NULL (ADR 0006).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('engineer', 'developer')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.execute("DELETE FROM sessions WHERE user_id IS NULL")
    op.alter_column("sessions", "user_id", existing_type=sa.UUID(), nullable=False)
    op.create_foreign_key(
        "sessions_user_id_fkey", "sessions", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_sessions_user_updated", "sessions", ["user_id", "updated_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_sessions_user_updated", table_name="sessions")
    op.drop_constraint("sessions_user_id_fkey", "sessions", type_="foreignkey")
    op.alter_column("sessions", "user_id", existing_type=sa.UUID(), nullable=True)
    op.drop_table("users")
