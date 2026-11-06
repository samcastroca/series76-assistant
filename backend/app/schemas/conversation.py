"""API request/response models for conversations. `products` is built by the backend from tool
results (app/services/products.py); nothing here is parsed from the LLM's text."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.catalog import CurrentType, Enclosure, FilterName, Frequency, ServiceType
from app.schemas.trace import ToolCallRecord, UsageRecord

ResponseType = Literal[
    "detail",
    "recommendation",
    "comparison",
    "clarification",
    "no_match",
    "not_found",
    "no_data",
    "error",
]
HighlightField = Literal[
    "torque",
    "speed_s",
    "duty_cycle_pct",
    "power_supply",
    "certifications",
    "enclosure",
    "series_size",
    "service",
]
TurnWarning = Literal[
    "tolerance_applied",
    "service_type_unspecified",
    "not_csa_certified",
    "no_published_data",
    "multiple_filters_blocking",
    "numbers_removed",
    "tool_error",
]


# --- Products (rendered as cards by the app) --------------------------------------------------


class PowerSupply(BaseModel):
    voltage_v: int
    phase: int | None
    current_type: CurrentType


class Torque(BaseModel):
    in_lb: int | None
    nm: int | None


class ProductService(BaseModel):
    type: ServiceType
    torque: Torque
    duty_cycle_pct: int | None
    cycles_per_hour: int | None
    starts_per_hour: int | None


class ProductElectrical(BaseModel):
    frequency_hz: Frequency | None
    speed_s: int | None
    fla_a: float | None
    lra_a: float | None


class Source(BaseModel):
    table: str
    page: int


class Product(BaseModel):
    part_number: str
    series_size: int
    variant: str
    enclosure: Enclosure
    power_supply: PowerSupply
    certifications: list[str]
    csa_certified: bool
    hazardous_locations: list[str] | None
    data_available: bool
    service: list[ProductService]
    electrical: list[ProductElectrical]
    motor_power_w: int | None  # None: not published
    notes: list[str] = []
    source: Source
    matched_service: ServiceType | None = None  # the rating a search matched on
    meets_requirements: bool = True  # False for closest alternatives
    unmet: list[FilterName] = []


class Clarification(BaseModel):
    question: str
    options: list[str]


# --- Endpoints --------------------------------------------------------------------------------


class ConversationRequest(BaseModel):
    session_id: uuid.UUID | None = None
    # Generous parsing cap; the route enforces MAX_QUERY_CHARS (query_too_long) after strip.
    query: str = Field(max_length=20_000)


class ConversationResponse(BaseModel):
    session_id: uuid.UUID
    message_id: uuid.UUID
    answer: str
    response_type: ResponseType
    products: list[Product] = []
    highlight_fields: list[HighlightField] = []
    clarification: Clarification | None = None
    suggestions: list[str] = []
    warnings: list[TurnWarning] = []


class SessionSummary(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class MessageOut(BaseModel):
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    status: Literal["ok", "error"]
    created_at: datetime
    response_type: ResponseType | None = None
    products: list[Product] = []
    highlight_fields: list[HighlightField] = []
    clarification: Clarification | None = None
    suggestions: list[str] = []
    warnings: list[TurnWarning] = []


class TraceOut(BaseModel):
    message_id: uuid.UUID
    session_id: uuid.UUID
    tool_calls: list[ToolCallRecord]
    usage: list[UsageRecord]
    payload: dict[str, Any]
