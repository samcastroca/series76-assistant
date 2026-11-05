"""Unit tests for the deterministic ingest stages, on synthetic inputs.

The numbers here are made up to exercise the code; they are not catalog values. No LLM client is
imported (checked below)."""

import csv
import sys
from decimal import Decimal

import pytest

from ingest.build import (
    BuildError,
    Correction,
    actuators_csv,
    apply_corrections,
    build_table,
    catalog_json,
    expand_motor_power,
    uncovered_discrepancies,
)
from ingest.clean import CellError, clean_part_number, is_na, parse_decimal, parse_int, part_key
from ingest.extracted import (
    ExtractedTable,
    ExtractionRun,
    MotorPowerGroup,
    diff_runs,
    run_schema,
)
from ingest.records import ActuatorRecord
from ingest.spec import load_spec
from ingest.validate import check_catalog, torque_consistent

SPEC = load_spec()
TABLES = {t.id: t for t in SPEC.tables}


# --- clean ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1,234", 1234), ("12,345", 12345), ("1.234", 1234), ("12.345.678", 12345678), (" 42 ", 42)],
)
def test_parse_int_strips_thousands_separators(raw: str, expected: int) -> None:
    assert parse_int(raw) == expected


@pytest.mark.parametrize("raw", ["5E", "9'5", "1.2", "12.34", "", "-3", "1,2a"])
def test_parse_int_rejects_misreads(raw: str) -> None:
    with pytest.raises(CellError):
        parse_int(raw)


def test_parse_decimal() -> None:
    assert parse_decimal(" 1.25 ") == Decimal("1.25")
    assert parse_decimal("7") == Decimal("7")
    with pytest.raises(CellError):
        parse_decimal("S'E")


def test_part_number_joins_split_bold_letters_and_strips_markers() -> None:
    assert clean_part_number("76 1 A 00-11300000/A", {"*"}) == ("761A00-11300000/A", set())
    assert clean_part_number("766B00-11320000/A*", {"*"}) == ("766B00-11320000/A", {"*"})
    assert clean_part_number("762x30-11380000/a", set())[0] == "762X30-11380000/A"


def test_part_key() -> None:
    assert part_key("763B00-11320000/A") == "763B0011320000A"


def test_is_na() -> None:
    assert is_na("N/A") and is_na(" n / a ")
    assert not is_na(None) and not is_na("") and not is_na("NA1")


# --- extraction schema and diff ------------------------------------------------------------


def test_run_schema_has_layout_columns_without_motor_power() -> None:
    schema = run_schema(SPEC, TABLES["wp_220v_3ph"])
    row_model = schema.model_fields["rows"].annotation.__args__[0]
    expected = ["part_number"] + [
        f"{t.group}_{t.field}" for t in SPEC.layouts["ac_3ph_single_torque"][1:-1]
    ]
    assert list(row_model.model_fields) == expected
    assert "motor_power_w" not in row_model.model_fields
    assert row_model.model_config["extra"] == "forbid"


SUFFIX = TABLES["xp_24v_on_off"].part_suffix
PNS = [f"76{n}X30{SUFFIX}" for n in (1, 2, 3)]


def _row(pn: str, **overrides: str) -> dict[str, str | None]:
    row = {
        "part_number": pn,
        "on_off_torque_inlb": "885",
        "on_off_torque_nm": "100",
        "on_off_duty_cycle_pct": "50",
        "on_off_cycles_per_hour": "1,000",
        "single_speed_s": "10",
        "single_fla_a": "1.5",
        "single_lra_a": "3.0",
    }
    return row | overrides


def _run(
    rows: list[dict], groups: list[MotorPowerGroup] | None = None, caption: str = ""
) -> ExtractionRun:
    return ExtractionRun(
        caption=caption or TABLES["xp_24v_on_off"].title,
        rows=rows,
        motor_power_groups=groups
        if groups is not None
        else [MotorPowerGroup(watts="25", first_part_number=PNS[0], last_part_number=PNS[-1])],
    )


def test_diff_runs_reports_each_cell_and_groups() -> None:
    a = _run([_row(p) for p in PNS])
    b = _run([_row(PNS[0]), _row(PNS[1], single_fla_a="1.6"), _row(PNS[2])], groups=[])
    diffs = diff_runs("xp_24v_on_off", a, b)
    assert [(d.row, d.field) for d in diffs] == [(2, "single_fla_a"), (None, "motor_power_groups")]
    assert diff_runs("xp_24v_on_off", a, a) == []


def test_diff_runs_reports_row_count() -> None:
    a = _run([_row(p) for p in PNS])
    b = _run([_row(p) for p in PNS[:2]])
    assert diff_runs("t", a, b)[0].field == "row_count"


# --- corrections ---------------------------------------------------------------------------


def _extracted(first: ExtractionRun, second: ExtractionRun) -> dict[str, ExtractedTable]:
    table = ExtractedTable(
        table_id="xp_24v_on_off", page=3, model="m", extracted_at="t", runs=[first, second]
    )
    return {"xp_24v_on_off": table}


def test_discrepancy_without_correction_blocks_build() -> None:
    a = _run([_row(p) for p in PNS])
    b = _run([_row(PNS[0]), _row(PNS[1], single_fla_a="1.6"), _row(PNS[2])])
    assert uncovered_discrepancies(_extracted(a, b), []) != []
    fix = Correction(table="xp_24v_on_off", row=2, field="single_fla_a", value="1.5", reason="PDF")
    assert uncovered_discrepancies(_extracted(a, b), [fix]) == []


def test_apply_corrections_changes_only_the_target_cell() -> None:
    run = _run([_row(p) for p in PNS])
    fix = Correction(table="xp_24v_on_off", row=3, field="single_lra_a", value="4.0", reason="PDF")
    fixed = apply_corrections("xp_24v_on_off", run, [fix])
    assert fixed.rows[2]["single_lra_a"] == "4.0"
    assert run.rows[2]["single_lra_a"] == "3.0"  # input untouched
    assert fixed.rows[:2] == run.rows[:2]


@pytest.mark.parametrize(("row", "field"), [(9, "single_lra_a"), (1, "no_such_field")])
def test_correction_pointing_nowhere_fails(row: int, field: str) -> None:
    fix = Correction(table="xp_24v_on_off", row=row, field=field, value="1", reason="PDF")
    with pytest.raises(BuildError):
        apply_corrections("xp_24v_on_off", _run([_row(p) for p in PNS]), [fix])


def test_correction_shape() -> None:
    with pytest.raises(ValueError):
        Correction(table="t", field="motor_power_groups", value="25", reason="r")
    with pytest.raises(ValueError):
        Correction(table="t", row=1, field="single_fla_a", value="1", reason="")


# --- motor power fill-down -----------------------------------------------------------------


def _g(watts: str, first: int, last: int) -> MotorPowerGroup:
    return MotorPowerGroup(watts=watts, first_part_number=PNS[first], last_part_number=PNS[last])


def test_expand_motor_power_fills_down() -> None:
    assert expand_motor_power([_g("25", 0, 1), _g("60", 2, 2)], PNS, set(), set()) == {
        0: 25,
        1: 25,
        2: 60,
    }


@pytest.mark.parametrize(
    ("groups", "na_rows"),
    [
        ([_g("25", 0, 1)], set()),  # gap
        ([_g("25", 0, 1), _g("60", 1, 2)], set()),  # overlap
        ([_g("25", 0, 2)], {1}),  # covers an N/A row
        ([_g("25", 2, 0)], set()),  # reversed
    ],
)
def test_expand_motor_power_rejects_bad_groups(groups, na_rows) -> None:
    with pytest.raises(CellError):
        expand_motor_power(groups, PNS, na_rows, set())


# --- build_table ---------------------------------------------------------------------------


def test_build_table_builds_records() -> None:
    records = build_table(SPEC, TABLES["xp_24v_on_off"], _run([_row(p) for p in PNS]))
    first = records[0]
    assert first.part_number == PNS[0] and first.part_key == part_key(PNS[0])
    assert (first.series_size, first.variant, first.enclosure) == (1, "X", "explosionproof")
    assert first.motor_power_w == 25 and first.data_available
    assert first.phase is None and first.current_type == "AC/DC"
    [service] = first.service_ratings
    assert service.service_type == "on_off" and service.cycles_per_hour == 1000
    assert service.starts_per_hour is None
    [electrical] = first.electrical_ratings
    assert electrical.frequency_hz is None and electrical.fla_a == Decimal("1.5")


def test_build_table_rejects_wrong_caption_and_suffix() -> None:
    rows = [_row(p) for p in PNS]
    rows[0]["part_number"] = "761X30-11300000/A"
    with pytest.raises(BuildError) as exc:
        build_table(SPEC, TABLES["xp_24v_on_off"], _run(rows, caption="Something else"))
    text = "\n".join(exc.value.errors)
    assert "caption" in text and "does not end with" in text


def test_build_table_rejects_misread_and_empty_cells() -> None:
    rows = [_row(PNS[0], on_off_torque_nm="5E"), _row(PNS[1], single_speed_s=None), _row(PNS[2])]
    with pytest.raises(BuildError) as exc:
        build_table(SPEC, TABLES["xp_24v_on_off"], _run(rows))
    assert len(exc.value.errors) == 2


def _three_phase_row(pn: str, cell: str | None) -> dict[str, str | None]:
    keys = [f"{t.group}_{t.field}" for t in SPEC.layouts["ac_3ph_single_torque"][1:-1]]
    return {"part_number": pn} | {k: cell for k in keys}


def test_na_rows_have_no_specs_and_mixed_rows_fail() -> None:
    table = TABLES["wp_220v_3ph"]
    pns = [f"76{n}A00{table.part_suffix}" for n in (1, 2)]
    full = _three_phase_row(pns[1], "10") | {
        "unspecified_torque_inlb": "885",
        "unspecified_torque_nm": "100",
        "hz60_fla_a": "1.0",
        "hz50_fla_a": "1.0",
    }
    run = ExtractionRun(
        caption=table.title,
        rows=[_three_phase_row(pns[0], "N/A"), full],
        motor_power_groups=[
            MotorPowerGroup(watts="25", first_part_number=pns[1], last_part_number=pns[1])
        ],
    )
    na, data = build_table(SPEC, table, run)
    assert not na.data_available and na.motor_power_w is None
    assert na.service_ratings == [] and na.electrical_ratings == []
    assert data.data_available and data.service_ratings[0].service_type == "unspecified"
    assert [e.frequency_hz for e in data.electrical_ratings] == [60, 50]

    run.rows[0]["hz60_speed_s"] = "10"
    with pytest.raises(BuildError, match="mixes N/A"):
        build_table(SPEC, table, run)


def test_star_marker_removes_csa() -> None:
    table = TABLES["wp_220v_1ph"]
    keys = [f"{t.group}_{t.field}" for t in SPEC.layouts["ac_1ph_dual_service"][1:-1]]
    pns = [f"766A00{table.part_suffix}", f"765A00{table.part_suffix}"]
    row = {k: "10" for k in keys} | {
        "on_off_torque_inlb": "885",
        "on_off_torque_nm": "100",
        "modulating_torque_inlb": "885",
        "modulating_torque_nm": "100",
    }
    run = ExtractionRun(
        caption=table.title,
        rows=[{"part_number": pns[0] + "*"} | row, {"part_number": pns[1]} | row],
        motor_power_groups=[
            MotorPowerGroup(watts="25", first_part_number=pns[0] + "*", last_part_number=pns[1])
        ],
    )
    starred, plain = build_table(SPEC, table, run)
    assert starred.part_number == pns[0]
    assert not starred.csa_certified and starred.certifications == ["CE", "UKCA"]
    assert plain.csa_certified and plain.certifications == ["CSA", "CE", "UKCA"]


# --- cross-checks and outputs --------------------------------------------------------------


def test_torque_consistency_tolerance() -> None:
    assert torque_consistent(885, 100)
    assert torque_consistent(885, 104)
    assert not torque_consistent(885, 106)
    assert not torque_consistent(8850, 100)  # factor-10 misread


def _records() -> list[ActuatorRecord]:
    return build_table(SPEC, TABLES["xp_24v_on_off"], _run([_row(p) for p in PNS]))


def test_check_catalog_reports_counts_torque_and_currents() -> None:
    records = _records()
    records[0].service_ratings[0].torque_nm = 200
    records[1].electrical_ratings[0].lra_a = Decimal("1.0")
    errors = "\n".join(check_catalog(SPEC, records + [records[2]]))
    assert "duplicated part number" in errors
    assert "xp_24v_on_off: 4 rows" in errors
    assert "torque" in errors and "FLA" in errors


def test_fla_equal_to_lra_is_allowed() -> None:
    records = _records()
    for r in records:
        r.electrical_ratings[0].lra_a = r.electrical_ratings[0].fla_a
    assert not [e for e in check_catalog(SPEC, records) if "FLA" in e]


def test_outputs_are_stable_and_null_is_empty() -> None:
    records = _records()
    assert catalog_json(records) == catalog_json(_records())
    lines = actuators_csv(records).splitlines()
    header = lines[0].split(",")
    first = dict(zip(header, next(csv.reader([lines[1]])), strict=True))
    assert first["on_off_starts_per_hour"] == ""  # not published, never 0
    assert first["phase"] == "" and first["data_available"] == "true"


def test_gemini_client_is_not_imported_by_build() -> None:
    assert "google.genai" not in sys.modules
