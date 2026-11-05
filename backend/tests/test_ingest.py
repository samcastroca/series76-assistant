"""`build` over the committed extraction (data/extracted/) and corrections (data/corrections.yaml).

Structural expectations come from the PDF's layout (row counts, N/A rows, CSA marks). Spot values
in SPOT_CHECKS are typed by a human from the PDF; they are never generated or guessed."""

from collections import Counter
from decimal import Decimal

import pytest

from ingest.build import DATA_DIR, actuators_csv, build_catalog, catalog_json, read_corrections
from ingest.build import read_extracted as _read_extracted
from ingest.records import ActuatorRecord
from ingest.spec import load_spec
from ingest.validate import torque_consistent

SPEC = load_spec()


@pytest.fixture(scope="module")
def records() -> list[ActuatorRecord]:
    return build_catalog(
        SPEC,
        _read_extracted(DATA_DIR / "extracted", SPEC),
        read_corrections(DATA_DIR / "corrections.yaml"),
    )


@pytest.fixture(scope="module")
def by_pn(records) -> dict[str, ActuatorRecord]:
    return {r.part_number: r for r in records}


def test_68_unique_part_numbers(records) -> None:
    assert len({r.part_number for r in records}) == len(records) == 68


def test_rows_per_table(records) -> None:
    counts = Counter(r.source_table for r in records)
    assert [counts[t.id] for t in SPEC.tables] == [11, 16, 11, 5, 5, 10, 10]


def test_four_rows_without_data(records) -> None:
    na = [r for r in records if not r.data_available]
    assert len(na) == 4
    assert {r.source_table for r in na} == {"wp_220v_3ph"}
    for r in na:
        assert r.motor_power_w is None and not r.service_ratings and not r.electrical_ratings


def test_five_without_csa_are_sizes_6_and_7_of_220v_weatherproof(records) -> None:
    no_csa = [r for r in records if not r.csa_certified]
    assert len(no_csa) == 5
    for r in no_csa:
        assert r.source_table == "wp_220v_1ph" and r.series_size in (6, 7)
        assert r.certifications == ["CE", "UKCA"]


def test_torque_inlb_vs_nm(records) -> None:
    for r in records:
        for s in r.service_ratings:
            assert torque_consistent(s.torque_inlb, s.torque_nm), (r.part_number, s.service_type)


def test_fla_not_above_lra(records) -> None:
    for r in records:
        for e in r.electrical_ratings:
            assert e.fla_a <= e.lra_a, (r.part_number, e.frequency_hz)


def test_three_phase_torque_is_unspecified(records) -> None:
    for r in records:
        if r.source_table == "wp_220v_3ph" and r.data_available:
            assert [s.service_type for s in r.service_ratings] == ["unspecified"]


def test_24v_has_one_rating_without_frequency(records) -> None:
    for r in records:
        if r.voltage_v == 24:
            assert [e.frequency_hz for e in r.electrical_ratings] == [None]
            expected = "DC" if r.source_table == "xp_24v_modulating" else "AC/DC"
            assert r.current_type == expected


def test_every_data_row_has_motor_power(records) -> None:
    assert all(r.motor_power_w is not None for r in records if r.data_available)


def test_committed_outputs_match_build(records) -> None:
    processed = DATA_DIR / "processed"
    assert (processed / "catalog.json").read_text("utf-8") == catalog_json(records)
    assert (processed / "actuators.csv").read_text("utf-8") == actuators_csv(records)


# --- hand-verified spot values ---------------------------------------------------------------
# Typed by the human from the PDF (page given), never copied from data/extracted/. Integers for
# in-lb, Nm, W, s, cycles and starts; Decimal("x.y") for FLA/LRA. A None case is skipped.
# Field is "<service>.<field>", "<hz60|hz50|single>.<field>" or "motor_power_w".
SPOT_CHECKS = [
    ("p1", "761A00-11300000/A", "on_off.torque_inlb", 700),
    ("p1", "763C00-11300000/A", "motor_power_w", 90),  # fill-down
    ("p1", "765B00-11300000/A", "hz50.speed_s", 130),
    ("p1", "766A00-11320000/A", "modulating.torque_inlb", 30975),  # row with *
    ("p1", "767C00-11320000/A", "hz60.lra_a", Decimal("17.0")),
    ("p1", "764B00-11320000/A", "on_off.cycles_per_hour", 10),
    ("p2", "763C00-11330C00/A", "unspecified.torque_inlb", 5300),
    ("p2", "765A00-11330C00/A", "unspecified.starts_per_hour", 1200),
    ("p3", "763X30-11380000/A", "single.lra_a", Decimal("30.8")),
    ("p3", "762Y30-11370000/A", "modulating.torque_nm", 145),
    ("p4", "765Y30-11300000/A", "on_off.torque_inlb", 25900),
    ("p4", "764Y30-11320000/A", "hz50.fla_a", Decimal("3.3")),
]
FREQUENCY = {"hz60": 60, "hz50": 50, "single": None}


def _value(record: ActuatorRecord, field: str) -> int | Decimal | None:
    if field == "motor_power_w":
        return record.motor_power_w
    group, name = field.split(".")
    if group in FREQUENCY:
        [rating] = [e for e in record.electrical_ratings if e.frequency_hz == FREQUENCY[group]]
    else:
        [rating] = [s for s in record.service_ratings if s.service_type == group]
    return getattr(rating, name)


@pytest.mark.parametrize(("page", "part_number", "field", "expected"), SPOT_CHECKS)
def test_spot_values(by_pn, page: str, part_number: str, field: str, expected) -> None:
    if expected is None:
        pytest.skip(f"TODO: fill {part_number} {field} from the PDF ({page})")
    assert _value(by_pn[part_number], field) == expected
