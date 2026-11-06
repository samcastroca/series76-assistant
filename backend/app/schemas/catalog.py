"""Typed inputs and outputs of the catalog tools. Closed sets are `Literal`s so the LLM can only
pass values the code understands. Every spec value comes from the database; None means "not
published", never 0."""

from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Enclosure = Literal["weatherproof", "explosionproof"]
CurrentType = Literal["AC", "DC", "AC/DC"]
ServiceType = Literal["on_off", "modulating", "unspecified"]
TorqueUnit = Literal["in_lb", "nm"]
Frequency = Literal[50, 60]
Warning = Literal["no_published_data", "service_type_unspecified", "not_csa_certified"]
FilterName = Literal[
    "min_torque",
    "max_speed_s",
    "min_duty_cycle_pct",
    "series_size",
    "service_type",
    "current_type",
    "phase",
    "voltage_v",
    "csa_required",
    "enclosure",
]


# --- Actuator specs -------------------------------------------------------------------------


class ServiceSpec(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service_type: ServiceType
    torque_inlb: int | None
    torque_nm: int | None
    duty_cycle_pct: int | None
    cycles_per_hour: int | None
    starts_per_hour: int | None


class ElectricalSpec(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    frequency_hz: Frequency | None
    speed_s: int | None
    fla_a: Decimal | None
    lra_a: Decimal | None


class ActuatorSpec(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    part_number: str
    series_size: int
    variant: str
    enclosure: Enclosure
    certifications: list[str]
    csa_certified: bool
    hazardous_locations: list[str] | None
    voltage_v: int
    phase: int | None
    current_type: CurrentType
    motor_power_w: int | None
    data_available: bool
    source_table: str
    source_page: int
    service_ratings: list[ServiceSpec]
    electrical_ratings: list[ElectricalSpec]
    warnings: list[Warning] = []


class ActuatorSummary(BaseModel):
    """Enough to tell apart products that share a short name across tables."""

    model_config = ConfigDict(from_attributes=True)

    part_number: str
    enclosure: Enclosure
    voltage_v: int
    phase: int | None
    current_type: CurrentType
    source_table: str


# --- get_actuator ---------------------------------------------------------------------------


class GetActuatorArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_number: str = Field(min_length=1, max_length=64)


class GetActuatorResult(BaseModel):
    status: Literal["found", "partial_match", "not_found", "no_data"]
    query: str
    normalized: str
    matched_by: Literal["exact", "prefix"] | None = None  # prefix: the input was incomplete
    actuator: ActuatorSpec | None = None
    candidates: list[ActuatorSummary] = []
    candidate_count: int = 0
    suggestions: list[ActuatorSummary] = []


# --- search_actuators -----------------------------------------------------------------------


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enclosure: Enclosure | None = None
    voltage_v: int | None = Field(default=None, gt=0, le=1000)
    phase: Literal[1, 3] | None = None
    current_type: Literal["AC", "DC"] | None = None
    service_type: ServiceType | None = None
    min_torque: Decimal | None = Field(default=None, gt=0, max_digits=8, decimal_places=2)
    torque_unit: TorqueUnit | None = None
    max_speed_s: int | None = Field(default=None, gt=0)
    min_duty_cycle_pct: int | None = Field(default=None, ge=1, le=100)
    csa_required: bool = False
    series_size: int | None = Field(default=None, ge=1, le=7)
    frequency_hz: Frequency | None = None
    limit: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def _torque_needs_unit(self) -> Self:
        if (self.min_torque is None) != (self.torque_unit is None):
            raise ValueError("min_torque and torque_unit must be given together")
        return self


class Match(BaseModel):
    """One (actuator, service type) pair. Torque depends on the service, so it is the unit."""

    part_number: str
    enclosure: Enclosure
    voltage_v: int
    phase: int | None
    current_type: CurrentType
    series_size: int
    csa_certified: bool
    service_type: ServiceType
    torque_inlb: int | None
    torque_nm: int | None
    torque_margin: Decimal | None  # torque - min_torque, in the unit the user asked for
    duty_cycle_pct: int | None
    speed_s: int | None  # at frequency_hz, or the slowest published speed when not given
    speed_frequency_hz: Frequency | None  # frequency of speed_s; None on 24 V (single value)
    warnings: list[Warning] = []
    meets_requirements: bool = True
    unmet: list[FilterName] = []


class BlockingFilter(BaseModel):
    filter: FilterName
    matches_without: int


class SearchResult(BaseModel):
    matches: list[Match]
    matched_voltage_v: int | None
    tolerance_applied: bool
    blocking_filters: list[BlockingFilter] = []
    closest_alternatives: list[Match] = []
    multiple_filters_blocking: bool = False
    applied_filters: SearchFilters


# --- list_catalog_options -------------------------------------------------------------------

OptionField = Literal["enclosure", "power_supply", "service_type", "series_size", "torque"]


class CatalogOptionsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: OptionField | None = None


class PowerSupplyOption(BaseModel):
    enclosure: Enclosure
    voltage_v: int
    phase: int | None
    current_type: CurrentType
    service_types: list[ServiceType]
    frequencies_hz: list[Frequency]


class TorqueRange(BaseModel):
    enclosure: Enclosure
    service_type: ServiceType
    min_inlb: int
    max_inlb: int
    min_nm: int
    max_nm: int


class CatalogOptions(BaseModel):
    enclosures: list[Enclosure] | None = None
    power_supplies: list[PowerSupplyOption] | None = None
    service_types: list[ServiceType] | None = None
    series_sizes: list[int] | None = None
    torque_ranges: list[TorqueRange] | None = None
