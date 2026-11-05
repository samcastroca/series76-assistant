"""`build` stage: data/extracted/ + data/corrections.yaml -> data/processed/catalog.json and
actuators.csv. Deterministic and offline: same inputs, byte-identical outputs."""

import csv
import io
import json
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import Numeric

from app.db.models import SERVICE_TYPES, ElectricalRating
from ingest.clean import (
    CellError,
    clean_part_number,
    is_blank,
    is_na,
    parse_decimal,
    parse_int,
    part_key,
)
from ingest.extracted import (
    MOTOR_POWER_GROUPS,
    PART_NUMBER,
    ExtractedTable,
    ExtractionRun,
    MotorPowerGroup,
    column_key,
    diff_runs,
    row_columns,
)
from ingest.records import ActuatorRecord, ElectricalRecord, ServiceRecord
from ingest.spec import FREQUENCY_PREFIXES, SERVICE_FIELDS, Spec, TableSpec, Target, load_spec
from ingest.validate import check_catalog

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SOURCE_PDF = "data/raw/series_76_electric_data.pdf"
DECIMAL_FIELDS = {
    name
    for name, col in ElectricalRating.__table__.columns.items()
    if isinstance(col.type, Numeric)
}
ELECTRICAL_FIELDS = ("speed_s", "fla_a", "lra_a")


class BuildError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("build failed:\n" + "\n".join(f"  - {e}" for e in errors))


class Correction(BaseModel):
    """A human-reviewed fix for one extracted cell (or for a table's Motor Power groups)."""

    model_config = ConfigDict(extra="forbid")

    table: str
    row: int | None = Field(default=None, ge=1)  # 1-based position in the table
    field: str
    value: str | list[MotorPowerGroup]
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        groups = self.field == MOTOR_POWER_GROUPS
        if groups != (self.row is None) or groups != isinstance(self.value, list):
            raise ValueError(
                f"{self.table}: '{MOTOR_POWER_GROUPS}' takes no row and a list value; "
                "any other field takes a row and a string value"
            )
        return self


# --- reading -------------------------------------------------------------------------------


def read_corrections(path: Path) -> list[Correction]:
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or []
    return [Correction.model_validate(item) for item in raw]


def read_extracted(directory: Path, spec: Spec) -> dict[str, ExtractedTable]:
    missing = [t.id for t in spec.tables if not (directory / f"{t.id}.json").exists()]
    if missing:
        raise BuildError(
            [f"no extraction for {m} in {directory}; run `extract` first" for m in missing]
        )
    return {
        t.id: ExtractedTable.model_validate_json((directory / f"{t.id}.json").read_text("utf-8"))
        for t in spec.tables
    }


# --- corrections ---------------------------------------------------------------------------


def uncovered_discrepancies(
    extracted: dict[str, ExtractedTable], corrections: list[Correction]
) -> list[str]:
    covered = {(c.table, c.row, c.field) for c in corrections}
    errors = []
    for table_id, table in extracted.items():
        for d in diff_runs(table_id, *table.runs):
            if d.field == "row_count":
                errors.append(f"{table_id}: runs disagree on row count; re-run `extract`")
            elif (d.table_id, d.row, d.field) not in covered:
                errors.append(
                    f"{table_id} row {d.row} {d.field}: runs disagree ({d.first!r} vs "
                    f"{d.second!r}) and there is no correction"
                )
    return errors


def apply_corrections(
    table_id: str, run: ExtractionRun, corrections: list[Correction]
) -> ExtractionRun:
    run = run.model_copy(deep=True)
    for c in corrections:
        if c.table != table_id:
            continue
        if c.field == MOTOR_POWER_GROUPS:
            run.motor_power_groups = list(c.value)
            continue
        if c.row > len(run.rows):
            raise BuildError([f"{table_id}: correction for row {c.row}, table has {len(run.rows)}"])
        row = run.rows[c.row - 1]
        if c.field not in row:
            raise BuildError([f"{table_id} row {c.row}: correction for unknown field {c.field!r}"])
        row[c.field] = c.value
    return run


# --- one table -----------------------------------------------------------------------------


def _parse(target: Target, raw: str) -> object:
    return parse_decimal(raw) if target.field in DECIMAL_FIELDS else parse_int(raw)


def expand_motor_power(
    groups: list[MotorPowerGroup], part_numbers: list[str], na_rows: set[int], markers: set[str]
) -> dict[int, int]:
    """Fill-down of the merged Motor Power cells: row index -> watts. Every data row must be
    covered exactly once and no N/A row may be covered."""
    index = {pn: i for i, pn in enumerate(part_numbers)}
    watts_by_row: dict[int, int] = {}
    for g in groups:
        first, _ = clean_part_number(g.first_part_number, markers)
        last, _ = clean_part_number(g.last_part_number, markers)
        if first not in index or last not in index:
            raise CellError(f"motor power group {first}..{last}: part number not in the table")
        if index[first] > index[last]:
            raise CellError(f"motor power group {first}..{last}: first row is after last row")
        watts = parse_int(g.watts)
        for i in range(index[first], index[last] + 1):
            if i in watts_by_row:
                raise CellError(f"{part_numbers[i]}: covered by two motor power groups")
            if i in na_rows:
                raise CellError(f"{part_numbers[i]}: N/A row inside a motor power group")
            watts_by_row[i] = watts
    uncovered = [
        part_numbers[i]
        for i in range(len(part_numbers))
        if i not in na_rows and i not in watts_by_row
    ]
    if uncovered:
        raise CellError(f"rows without motor power: {', '.join(uncovered)}")
    return watts_by_row


def build_table(spec: Spec, table: TableSpec, run: ExtractionRun) -> list[ActuatorRecord]:
    errors: list[str] = []
    if " ".join(run.caption.split()).casefold() != table.title.casefold():
        errors.append(f"{table.id}: caption {run.caption!r} is not {table.title!r}")

    columns = [t for t in row_columns(spec, table) if t != PART_NUMBER]
    markers = set(table.markers)
    defaults = spec.defaults[table.enclosure]

    part_numbers: list[str] = []
    found_markers: list[set[str]] = []
    na_rows: set[int] = set()
    for i, row in enumerate(run.rows):
        pn, found = clean_part_number(row.get("part_number") or "", markers)
        if not pn.endswith(table.part_suffix):
            errors.append(f"{table.id} row {i + 1}: {pn} does not end with {table.part_suffix}")
        part_numbers.append(pn)
        found_markers.append(found)
        cells = [row.get(column_key(t)) for t in columns]
        if any(is_na(c) for c in cells):
            if not all(is_na(c) for c in cells):
                errors.append(f"{table.id} {pn}: row mixes N/A and values")
            na_rows.add(i)

    try:
        watts_by_row = expand_motor_power(run.motor_power_groups, part_numbers, na_rows, markers)
    except CellError as exc:
        errors.append(f"{table.id}: {exc}")
        watts_by_row = {}

    records: list[ActuatorRecord] = []
    for i, row in enumerate(run.rows):
        pn = part_numbers[i]
        certifications = list(defaults.certifications)
        csa_certified = None
        for symbol in sorted(found_markers[i]):
            marker = table.markers[symbol]
            certifications = [c for c in certifications if c not in marker.remove_certifications]
            if marker.csa_certified is not None:
                csa_certified = marker.csa_certified
        if csa_certified is None:
            csa_certified = "CSA" in certifications

        services: list[ServiceRecord] = []
        electrical: list[ElectricalRecord] = []
        if i not in na_rows:
            try:
                values: dict[str, dict[str, object]] = {}
                for t in columns:
                    raw = row.get(column_key(t))
                    if is_blank(raw):
                        raise CellError(f"{column_key(t)} is empty")
                    try:
                        values.setdefault(t.group, {})[t.field] = _parse(t, raw)
                    except CellError as exc:
                        raise CellError(f"{column_key(t)}: {exc}") from exc
                services = [ServiceRecord(service_type=s, **values[s]) for s in table.services]
                electrical = [
                    ElectricalRecord(frequency_hz=FREQUENCY_PREFIXES[g], **values[g])
                    for g in FREQUENCY_PREFIXES
                    if g in values
                ]
            except (CellError, ValidationError) as exc:
                errors.append(f"{table.id} {pn}: {exc}")
                continue

        try:
            records.append(
                ActuatorRecord(
                    part_number=pn,
                    part_key=part_key(pn),
                    series_size=int(pn[2]) if pn[2:3].isdigit() else 0,
                    variant=pn[3:4],
                    enclosure=table.enclosure,
                    certifications=certifications,
                    csa_certified=csa_certified,
                    hazardous_locations=defaults.hazardous_locations,
                    voltage_v=table.voltage_v,
                    phase=table.phase,
                    current_type=table.current_type,
                    motor_power_w=watts_by_row.get(i),
                    data_available=i not in na_rows,
                    source_table=table.id,
                    source_page=table.page,
                    service_ratings=services,
                    electrical_ratings=electrical,
                )
            )
        except ValidationError as exc:
            errors.append(f"{table.id} {pn}: {exc}")

    if errors:
        raise BuildError(errors)
    return records


# --- whole catalog -------------------------------------------------------------------------


def build_catalog(
    spec: Spec, extracted: dict[str, ExtractedTable], corrections: list[Correction]
) -> list[ActuatorRecord]:
    known = {t.id for t in spec.tables}
    errors = [
        f"correction for unknown table {c.table!r}" for c in corrections if c.table not in known
    ]
    errors += uncovered_discrepancies(extracted, corrections)
    records: list[ActuatorRecord] = []
    for table in spec.tables:
        try:
            run = apply_corrections(table.id, extracted[table.id].runs[0], corrections)
            records += build_table(spec, table, run)
        except BuildError as exc:
            errors += exc.errors
    if not errors:
        errors = check_catalog(spec, records)
    if errors:
        raise BuildError(errors)
    return records


# --- outputs -------------------------------------------------------------------------------

ACTUATOR_COLUMNS = [
    name
    for name in ActuatorRecord.model_fields
    if name not in ("service_ratings", "electrical_ratings")
]
SERVICE_COLUMNS = [f"{s}_{f}" for s in SERVICE_TYPES for f in sorted(SERVICE_FIELDS)]
ELECTRICAL_COLUMNS = [f"{g}_{f}" for g in FREQUENCY_PREFIXES for f in ELECTRICAL_FIELDS]
LIST_SEPARATOR = " | "  # hazardous-location strings contain ';' and ','


def _csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return LIST_SEPARATOR.join(value)
    return str(value)


def catalog_json(records: list[ActuatorRecord]) -> str:
    payload = {
        "source_pdf": SOURCE_PDF,
        "actuators": [r.model_dump(mode="json") for r in records],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def actuators_csv(records: list[ActuatorRecord]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(ACTUATOR_COLUMNS + SERVICE_COLUMNS + ELECTRICAL_COLUMNS)
    freq_prefix = {v: k for k, v in FREQUENCY_PREFIXES.items()}
    for r in records:
        flat = {c: getattr(r, c) for c in ACTUATOR_COLUMNS}
        for s in r.service_ratings:
            for f in SERVICE_FIELDS:
                flat[f"{s.service_type}_{f}"] = getattr(s, f)
        for e in r.electrical_ratings:
            for f in ELECTRICAL_FIELDS:
                flat[f"{freq_prefix[e.frequency_hz]}_{f}"] = getattr(e, f)
        cols = ACTUATOR_COLUMNS + SERVICE_COLUMNS + ELECTRICAL_COLUMNS
        writer.writerow([_csv_value(flat.get(c)) for c in cols])
    return out.getvalue()


def run_build(data_dir: Path = DATA_DIR) -> list[ActuatorRecord]:
    spec = load_spec()
    records = build_catalog(
        spec,
        read_extracted(data_dir / "extracted", spec),
        read_corrections(data_dir / "corrections.yaml"),
    )
    processed = data_dir / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    (processed / "catalog.json").write_text(catalog_json(records), encoding="utf-8", newline="\n")
    (processed / "actuators.csv").write_text(actuators_csv(records), encoding="utf-8", newline="\n")
    return records
