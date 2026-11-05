"""Typed loader for ingest/tables.yaml: the declarative description of each PDF table."""

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import ElectricalRating, ServiceRating

SPEC_PATH = Path(__file__).with_name("tables.yaml")

Enclosure = Literal["weatherproof", "explosionproof"]
ServiceType = Literal["on_off", "modulating", "unspecified"]
CurrentType = Literal["AC", "DC", "AC/DC"]

FREQUENCY_PREFIXES = {"hz60": 60, "hz50": 50, "single": None}
ACTUATOR_FIELDS = {"part_number", "motor_power_w"}
SERVICE_FIELDS = set(ServiceRating.__table__.columns.keys()) - {"part_number", "service_type"}
ELECTRICAL_FIELDS = set(ElectricalRating.__table__.columns.keys()) - {
    "id",
    "part_number",
    "frequency_hz",
}


class Target(BaseModel):
    """One column destination, e.g. `on_off.torque_inlb` or `hz60.fla_a`."""

    model_config = ConfigDict(frozen=True)

    group: str
    field: str

    @classmethod
    def parse(cls, raw: str) -> Self:
        group, sep, field = raw.partition(".")
        if not sep:
            raise ValueError(f"target {raw!r} must look like '<group>.<field>'")
        allowed = (
            ACTUATOR_FIELDS
            if group == "actuator"
            else SERVICE_FIELDS
            if group in ServiceType.__args__
            else ELECTRICAL_FIELDS
            if group in FREQUENCY_PREFIXES
            else None
        )
        if allowed is None:
            raise ValueError(f"unknown target group in {raw!r}")
        if field not in allowed:
            raise ValueError(f"unknown field in {raw!r}; allowed: {sorted(allowed)}")
        return cls(group=group, field=field)


class EnclosureDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    certifications: list[str]
    hazardous_locations: list[str] | None = None


class Marker(BaseModel):
    """Effect of a footnote marker (e.g. `*`) printed next to a part number."""

    model_config = ConfigDict(extra="forbid")

    csa_certified: bool | None = None
    remove_certifications: list[str] = Field(default_factory=list)


class TableSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    page: int = Field(ge=1)
    index: int = Field(ge=0)
    title: str
    enclosure: Enclosure
    voltage_v: Literal[24, 110, 220]
    phase: Literal[1, 3] | None
    current_type: CurrentType
    services: list[ServiceType] = Field(min_length=1)
    layout: str
    part_suffix: str
    expected_rows: int = Field(ge=1)
    expected_na_rows: int = Field(default=0, ge=0)
    markers: dict[str, Marker] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.voltage_v == 24) != (self.phase is None):
            raise ValueError(f"{self.id}: phase must be null exactly for 24 V tables")
        if self.expected_na_rows > self.expected_rows:
            raise ValueError(f"{self.id}: more N/A rows than rows")
        if len(set(self.services)) != len(self.services):
            raise ValueError(f"{self.id}: duplicated service")
        return self


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layouts: dict[str, list[Target]]
    defaults: dict[Enclosure, EnclosureDefaults]
    tables: list[TableSpec]

    @model_validator(mode="before")
    @classmethod
    def _parse_targets(cls, data: dict) -> dict:
        layouts = data.get("layouts", {})
        data["layouts"] = {
            name: [Target.parse(raw) for raw in columns] for name, columns in layouts.items()
        }
        return data

    @model_validator(mode="after")
    def _check(self) -> Self:
        for name, columns in self.layouts.items():
            if columns[0] != Target(group="actuator", field="part_number"):
                raise ValueError(f"layout {name}: first column must be actuator.part_number")
            if len(set(columns)) != len(columns):
                raise ValueError(f"layout {name}: duplicated column target")

        ids = [t.id for t in self.tables]
        if len(set(ids)) != len(ids):
            raise ValueError("table ids must be unique")
        positions = [(t.page, t.index) for t in self.tables]
        if len(set(positions)) != len(positions):
            raise ValueError("(page, index) must be unique")

        for table in self.tables:
            if table.layout not in self.layouts:
                raise ValueError(f"{table.id}: unknown layout {table.layout!r}")
            groups = {c.group for c in self.layouts[table.layout]}
            services = groups & set(ServiceType.__args__)
            if services != set(table.services):
                raise ValueError(
                    f"{table.id}: services {table.services} do not match layout {sorted(services)}"
                )
            frequencies = groups & FREQUENCY_PREFIXES.keys()
            expected = {"single"} if table.voltage_v == 24 else {"hz60", "hz50"}
            if frequencies != expected:
                raise ValueError(f"{table.id}: layout frequencies {sorted(frequencies)}")
            certs = set(self.defaults[table.enclosure].certifications)
            for symbol, marker in table.markers.items():
                if not set(marker.remove_certifications) <= certs:
                    raise ValueError(f"{table.id}: marker {symbol!r} removes unknown certification")
        return self


def load_spec(path: Path = SPEC_PATH) -> Spec:
    with path.open(encoding="utf-8") as fh:
        return Spec.model_validate(yaml.safe_load(fh))
