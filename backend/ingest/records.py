"""Validated catalog records: the content of data/processed/catalog.json and what `load` upserts.
Types and closed sets mirror app/db/models.py. NULL (None) means "not published", never 0."""

import re
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

PositiveInt = Annotated[int, Field(ge=1)]
Amps = Annotated[Decimal, Field(gt=0, max_digits=5, decimal_places=2)]

PART_PREFIX = re.compile(r"76(?P<size>[1-7])(?P<variant>[ABCXY])\d{2}")
VARIANTS_BY_ENCLOSURE = {"weatherproof": {"A", "B", "C"}, "explosionproof": {"X", "Y"}}


class ServiceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_type: Literal["on_off", "modulating", "unspecified"]
    torque_inlb: PositiveInt | None
    torque_nm: PositiveInt | None
    duty_cycle_pct: Annotated[int, Field(ge=1, le=100)] | None
    cycles_per_hour: PositiveInt | None = None
    starts_per_hour: PositiveInt | None = None


class ElectricalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frequency_hz: Literal[50, 60] | None
    speed_s: PositiveInt | None
    fla_a: Amps | None
    lra_a: Amps | None


class ActuatorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_number: str
    part_key: str
    series_size: Annotated[int, Field(ge=1, le=7)]
    variant: Literal["A", "B", "C", "X", "Y"]
    enclosure: Literal["weatherproof", "explosionproof"]
    certifications: list[str]
    csa_certified: bool
    hazardous_locations: list[str] | None
    voltage_v: Literal[24, 110, 220]
    phase: Literal[1, 3] | None
    current_type: Literal["AC", "DC", "AC/DC"]
    motor_power_w: PositiveInt | None
    data_available: bool
    source_table: str
    source_page: PositiveInt
    service_ratings: list[ServiceRecord]
    electrical_ratings: list[ElectricalRecord]

    @model_validator(mode="after")
    def _check(self) -> Self:
        prefix = PART_PREFIX.match(self.part_number)
        if not prefix:
            raise ValueError(f"{self.part_number}: does not start with 76<size><variant><nn>")
        if int(prefix["size"]) != self.series_size or prefix["variant"] != self.variant:
            raise ValueError(f"{self.part_number}: size/variant do not match the part number")
        if self.variant not in VARIANTS_BY_ENCLOSURE[self.enclosure]:
            raise ValueError(f"{self.part_number}: variant {self.variant} is not {self.enclosure}")
        if self.csa_certified != ("CSA" in self.certifications):
            raise ValueError(f"{self.part_number}: csa_certified disagrees with certifications")
        if (self.voltage_v == 24) != (self.phase is None):
            raise ValueError(f"{self.part_number}: phase must be null exactly on 24 V")
        if self.data_available:
            if (
                self.motor_power_w is None
                or not self.service_ratings
                or not self.electrical_ratings
            ):
                raise ValueError(f"{self.part_number}: data row without motor power or ratings")
        elif self.motor_power_w is not None or self.service_ratings or self.electrical_ratings:
            raise ValueError(f"{self.part_number}: N/A row must have no specs")
        return self
