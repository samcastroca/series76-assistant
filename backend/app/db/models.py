"""Catalog tables: one row per part number, plus torque per service type and electrical data
per frequency. NULL always means "not published"; it is never replaced with 0.

Conversation tables: sessions, their messages, the tool calls behind each assistant message and
the LLM usage of each turn. Deleting a session cascades to everything under it."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ENCLOSURES = ("weatherproof", "explosionproof")
CURRENT_TYPES = ("AC", "DC", "AC/DC")
SERVICE_TYPES = ("on_off", "modulating", "unspecified")
VARIANTS = ("A", "B", "C", "X", "Y")
VOLTAGES = (24, 110, 220)
PHASES = (1, 3)
FREQUENCIES = (50, 60)
ROLES = ("user", "assistant")
MESSAGE_STATUSES = ("ok", "error")


def _in(column: str, values: tuple) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    pass


class Actuator(Base):
    __tablename__ = "actuators"

    part_number: Mapped[str] = mapped_column(Text, primary_key=True)
    part_key: Mapped[str] = mapped_column(Text, unique=True)
    series_size: Mapped[int] = mapped_column(SmallInteger)
    variant: Mapped[str] = mapped_column(Text)
    enclosure: Mapped[str] = mapped_column(Text)
    certifications: Mapped[list[str]] = mapped_column(ARRAY(Text))
    csa_certified: Mapped[bool]
    hazardous_locations: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    voltage_v: Mapped[int] = mapped_column(SmallInteger)
    phase: Mapped[int | None] = mapped_column(SmallInteger)
    current_type: Mapped[str] = mapped_column(Text)
    motor_power_w: Mapped[int | None] = mapped_column(Integer)
    data_available: Mapped[bool]
    source_table: Mapped[str] = mapped_column(Text)
    source_page: Mapped[int] = mapped_column(SmallInteger)

    service_ratings: Mapped[list["ServiceRating"]] = relationship(
        back_populates="actuator", cascade="all, delete-orphan", passive_deletes=True
    )
    electrical_ratings: Mapped[list["ElectricalRating"]] = relationship(
        back_populates="actuator", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("series_size BETWEEN 1 AND 7", name="ck_actuators_series_size"),
        CheckConstraint(_in("variant", VARIANTS), name="ck_actuators_variant"),
        CheckConstraint(_in("enclosure", ENCLOSURES), name="ck_actuators_enclosure"),
        CheckConstraint(_in("voltage_v", VOLTAGES), name="ck_actuators_voltage_v"),
        CheckConstraint(_in("phase", PHASES), name="ck_actuators_phase"),
        CheckConstraint(_in("current_type", CURRENT_TYPES), name="ck_actuators_current_type"),
        CheckConstraint("(voltage_v = 24) = (phase IS NULL)", name="ck_actuators_phase_24v"),
        Index(
            "ix_actuators_part_key_trgm",
            "part_key",
            postgresql_using="gin",
            postgresql_ops={"part_key": "gin_trgm_ops"},
        ),
    )


class ServiceRating(Base):
    __tablename__ = "service_ratings"

    part_number: Mapped[str] = mapped_column(
        Text, ForeignKey("actuators.part_number", ondelete="CASCADE"), primary_key=True
    )
    service_type: Mapped[str] = mapped_column(Text, primary_key=True)
    torque_inlb: Mapped[int | None] = mapped_column(Integer)
    torque_nm: Mapped[int | None] = mapped_column(Integer)
    duty_cycle_pct: Mapped[int | None] = mapped_column(SmallInteger)
    cycles_per_hour: Mapped[int | None] = mapped_column(Integer)
    starts_per_hour: Mapped[int | None] = mapped_column(Integer)

    actuator: Mapped[Actuator] = relationship(back_populates="service_ratings")

    __table_args__ = (
        CheckConstraint(_in("service_type", SERVICE_TYPES), name="ck_service_ratings_type"),
    )


class ElectricalRating(Base):
    __tablename__ = "electrical_ratings"

    id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    part_number: Mapped[str] = mapped_column(
        Text, ForeignKey("actuators.part_number", ondelete="CASCADE")
    )
    frequency_hz: Mapped[int | None] = mapped_column(SmallInteger)  # NULL on 24 V tables
    speed_s: Mapped[int | None] = mapped_column(Integer)
    fla_a: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    lra_a: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    actuator: Mapped[Actuator] = relationship(back_populates="electrical_ratings")

    __table_args__ = (
        CheckConstraint(_in("frequency_hz", FREQUENCIES), name="ck_electrical_ratings_frequency"),
        UniqueConstraint(
            "part_number",
            "frequency_hz",
            name="uq_electrical_ratings_part_frequency",
            postgresql_nulls_not_distinct=True,
        ),
    )


# --- Conversations --------------------------------------------------------------------------


def _now() -> Any:
    return mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


class ChatSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    title: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("sessions.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(Text, server_default="ok")
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        CheckConstraint(_in("role", ROLES), name="ck_messages_role"),
        CheckConstraint(_in("status", MESSAGE_STATUSES), name="ck_messages_status"),
        Index("ix_messages_session_created", "session_id", "created_at"),
    )


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(SmallInteger)
    tool_name: Mapped[str] = mapped_column(Text)
    args: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    latency_ms: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class LlmUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))  # NULL: not reported
