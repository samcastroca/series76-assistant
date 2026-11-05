"""ingest/tables.yaml describes the 7 tables of the data PDF consistently."""

import copy

import pytest
import yaml
from pydantic import ValidationError

from ingest.spec import SPEC_PATH, Spec, load_spec

LAYOUT_WIDTHS = {
    "ac_1ph_dual_service": 16,
    "ac_3ph_single_torque": 13,
    "dc_24v_on_off": 9,
    "dc_24v_modulating": 9,
}


@pytest.fixture(scope="module")
def spec() -> Spec:
    return load_spec()


@pytest.fixture
def raw() -> dict:
    with SPEC_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_seven_tables_with_68_part_numbers(spec: Spec) -> None:
    assert [t.expected_rows for t in spec.tables] == [11, 16, 11, 5, 5, 10, 10]
    assert sum(t.expected_rows for t in spec.tables) == 68


def test_only_the_three_phase_table_has_na_rows(spec: Spec) -> None:
    na = {t.id: t.expected_na_rows for t in spec.tables if t.expected_na_rows}
    assert na == {"wp_220v_3ph": 4}


def test_layout_widths(spec: Spec) -> None:
    assert {name: len(cols) for name, cols in spec.layouts.items()} == LAYOUT_WIDTHS


def test_supplies_and_current_types(spec: Spec) -> None:
    supplies = {(t.voltage_v, t.phase, t.current_type, tuple(t.services)) for t in spec.tables}
    assert (220, 3, "AC", ("unspecified",)) in supplies
    assert (24, None, "AC/DC", ("on_off",)) in supplies
    assert (24, None, "DC", ("modulating",)) in supplies


def test_only_220v_1ph_weatherproof_marks_non_csa(spec: Spec) -> None:
    marked = {t.id: t.markers for t in spec.tables if t.markers}
    assert list(marked) == ["wp_220v_1ph"]
    assert marked["wp_220v_1ph"]["*"].csa_certified is False


def test_explosionproof_defaults_carry_hazardous_locations(spec: Spec) -> None:
    assert spec.defaults["explosionproof"].hazardous_locations
    assert spec.defaults["weatherproof"].hazardous_locations is None


def test_rejects_unknown_target(raw: dict) -> None:
    raw["layouts"]["dc_24v_on_off"][1] = "on_off.torque_lbs"
    with pytest.raises(ValidationError, match="unknown field"):
        Spec.model_validate(raw)


def test_rejects_services_not_matching_layout(raw: dict) -> None:
    bad = copy.deepcopy(raw)
    bad["tables"][0]["services"] = ["on_off"]
    with pytest.raises(ValidationError, match="do not match layout"):
        Spec.model_validate(bad)


def test_rejects_phase_on_24v(raw: dict) -> None:
    raw["tables"][3]["phase"] = 1
    with pytest.raises(ValidationError, match="phase must be null"):
        Spec.model_validate(raw)
