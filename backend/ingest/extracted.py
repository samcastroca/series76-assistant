"""Shape of the raw extraction stored in data/extracted/<table_id>.json, and the cell-by-cell diff
between two extraction runs. Pure Pydantic: no LLM client is imported here."""

from functools import cache

from pydantic import BaseModel, ConfigDict, Field, create_model

from ingest.spec import Spec, TableSpec, Target

PART_NUMBER = Target(group="actuator", field="part_number")
MOTOR_POWER = Target(group="actuator", field="motor_power_w")
MOTOR_POWER_GROUPS = "motor_power_groups"


def column_key(target: Target) -> str:
    """Flat key used in extracted rows and in actuators.csv: `part_number`, `on_off_torque_inlb`,
    `hz60_fla_a`, ..."""
    return target.field if target.group == "actuator" else f"{target.group}_{target.field}"


def row_columns(spec: Spec, table: TableSpec) -> list[Target]:
    """Columns extracted per row: the layout without the merged Motor Power column."""
    return [t for t in spec.layouts[table.layout] if t != MOTOR_POWER]


class MotorPowerGroup(BaseModel):
    """One merged Motor Power cell and the rows it spans, as printed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    watts: str
    first_part_number: str
    last_part_number: str


class ExtractionRun(BaseModel):
    """One extraction of one table. Every cell is a string copied as printed."""

    model_config = ConfigDict(extra="forbid")

    caption: str
    rows: list[dict[str, str | None]]
    motor_power_groups: list[MotorPowerGroup]


class ExtractedTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_id: str
    page: int
    model: str
    extracted_at: str
    runs: list[ExtractionRun] = Field(min_length=2, max_length=2)


@cache
def _row_model(keys: tuple[str, ...]) -> type[BaseModel]:
    fields = {key: (str | None, ...) for key in keys}
    return create_model("Row", __config__=ConfigDict(extra="forbid"), **fields)


def run_schema(spec: Spec, table: TableSpec) -> type[BaseModel]:
    """Strict response schema for one table: the row model has exactly the layout's columns."""
    row = _row_model(tuple(column_key(t) for t in row_columns(spec, table)))
    return create_model(
        f"Extraction_{table.layout}",
        __config__=ConfigDict(extra="forbid"),
        caption=(str, ...),
        rows=(list[row], ...),
        motor_power_groups=(list[MotorPowerGroup], ...),
    )


class Discrepancy(BaseModel):
    """A cell where the two runs disagree. `row` is 1-based; None for table-level fields."""

    model_config = ConfigDict(frozen=True)

    table_id: str
    row: int | None
    field: str
    first: object
    second: object


def diff_runs(table_id: str, first: ExtractionRun, second: ExtractionRun) -> list[Discrepancy]:
    """Rows are compared by position, so a misread part number still shows up as one cell."""
    out: list[Discrepancy] = []
    if len(first.rows) != len(second.rows):
        out.append(
            Discrepancy(
                table_id=table_id,
                row=None,
                field="row_count",
                first=len(first.rows),
                second=len(second.rows),
            )
        )
    for i, (a, b) in enumerate(zip(first.rows, second.rows, strict=False), start=1):
        for key in sorted(a.keys() | b.keys()):
            if a.get(key) != b.get(key):
                out.append(
                    Discrepancy(
                        table_id=table_id, row=i, field=key, first=a.get(key), second=b.get(key)
                    )
                )
    if first.motor_power_groups != second.motor_power_groups:
        out.append(
            Discrepancy(
                table_id=table_id,
                row=None,
                field=MOTOR_POWER_GROUPS,
                first=[g.model_dump() for g in first.motor_power_groups],
                second=[g.model_dump() for g in second.motor_power_groups],
            )
        )
    return out


def discrepancies_markdown(discrepancies: list[Discrepancy], part_numbers: dict) -> str:
    """Review sheet for the human. `part_numbers[(table_id, row)]` labels each row."""
    lines = [
        "# Extraction discrepancies",
        "",
        "Cells where the two Gemini runs disagree. Check each one against the PDF and add",
        "an entry to `data/corrections.yaml`; `build` refuses to run until every cell here",
        "is covered.",
        "",
    ]
    if not discrepancies:
        lines.append("None: both runs agree on every cell.")
        return "\n".join(lines) + "\n"
    lines += [
        "| table | row | part number (run 1) | field | run 1 | run 2 |",
        "|---|---|---|---|---|---|",
    ]
    for d in discrepancies:
        pn = part_numbers.get((d.table_id, d.row), "")
        row = "" if d.row is None else d.row
        lines.append(f"| {d.table_id} | {row} | {pn} | {d.field} | {d.first!r} | {d.second!r} |")
    return "\n".join(lines) + "\n"
