"""Catalog tables (actuators, service_ratings, electrical_ratings) and pg_trgm.

Revision ID: 0001
Revises:
Create Date: 2026-11-04 23:36:07.379079

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # pg_trgm backs the part-number suggestions (similarity search on part_key).
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "actuators",
        sa.Column("part_number", sa.Text(), nullable=False),
        sa.Column("part_key", sa.Text(), nullable=False),
        sa.Column("series_size", sa.SmallInteger(), nullable=False),
        sa.Column("variant", sa.Text(), nullable=False),
        sa.Column("enclosure", sa.Text(), nullable=False),
        sa.Column("certifications", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("csa_certified", sa.Boolean(), nullable=False),
        sa.Column("hazardous_locations", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("voltage_v", sa.SmallInteger(), nullable=False),
        sa.Column("phase", sa.SmallInteger(), nullable=True),
        sa.Column("current_type", sa.Text(), nullable=False),
        sa.Column("motor_power_w", sa.Integer(), nullable=True),
        sa.Column("data_available", sa.Boolean(), nullable=False),
        sa.Column("source_table", sa.Text(), nullable=False),
        sa.Column("source_page", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "current_type IN ('AC', 'DC', 'AC/DC')", name="ck_actuators_current_type"
        ),
        sa.CheckConstraint(
            "enclosure IN ('weatherproof', 'explosionproof')", name="ck_actuators_enclosure"
        ),
        sa.CheckConstraint("variant IN ('A', 'B', 'C', 'X', 'Y')", name="ck_actuators_variant"),
        sa.CheckConstraint("(voltage_v = 24) = (phase IS NULL)", name="ck_actuators_phase_24v"),
        sa.CheckConstraint("phase IN (1, 3)", name="ck_actuators_phase"),
        sa.CheckConstraint("series_size BETWEEN 1 AND 7", name="ck_actuators_series_size"),
        sa.CheckConstraint("voltage_v IN (24, 110, 220)", name="ck_actuators_voltage_v"),
        sa.PrimaryKeyConstraint("part_number"),
        sa.UniqueConstraint("part_key"),
    )
    op.create_index(
        "ix_actuators_part_key_trgm",
        "actuators",
        ["part_key"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"part_key": "gin_trgm_ops"},
    )
    op.create_table(
        "electrical_ratings",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("part_number", sa.Text(), nullable=False),
        sa.Column("frequency_hz", sa.SmallInteger(), nullable=True),
        sa.Column("speed_s", sa.Integer(), nullable=True),
        sa.Column("fla_a", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("lra_a", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.CheckConstraint("frequency_hz IN (50, 60)", name="ck_electrical_ratings_frequency"),
        sa.ForeignKeyConstraint(["part_number"], ["actuators.part_number"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "part_number",
            "frequency_hz",
            name="uq_electrical_ratings_part_frequency",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        "service_ratings",
        sa.Column("part_number", sa.Text(), nullable=False),
        sa.Column("service_type", sa.Text(), nullable=False),
        sa.Column("torque_inlb", sa.Integer(), nullable=True),
        sa.Column("torque_nm", sa.Integer(), nullable=True),
        sa.Column("duty_cycle_pct", sa.SmallInteger(), nullable=True),
        sa.Column("cycles_per_hour", sa.Integer(), nullable=True),
        sa.Column("starts_per_hour", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "service_type IN ('on_off', 'modulating', 'unspecified')",
            name="ck_service_ratings_type",
        ),
        sa.ForeignKeyConstraint(["part_number"], ["actuators.part_number"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("part_number", "service_type"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("service_ratings")
    op.drop_table("electrical_ratings")
    op.drop_index(
        "ix_actuators_part_key_trgm",
        table_name="actuators",
        postgresql_using="gin",
        postgresql_ops={"part_key": "gin_trgm_ops"},
    )
    op.drop_table("actuators")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
