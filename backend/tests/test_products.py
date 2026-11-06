"""products / response_type built from real tool results, plus history state and titles. Every
expected value is derived from catalog.json; nothing is parsed from text."""

import pytest

from app.agent.recorder import ToolOutput, ToolRecorder
from app.schemas.catalog import CatalogOptionsArgs, GetActuatorArgs, SearchFilters
from app.services.history import update_parts
from app.services.products import build_view
from app.services.titles import make_title
from app.tools import catalog_tools
from ingest.load import read_catalog

CATALOG = read_catalog()
BY_PN = {r.part_number: r for r in CATALOG}
WITH_DATA = [r for r in CATALOG if r.data_available]
NO_DATA = next(r for r in CATALOG if not r.data_available)
NOT_CSA = next(r for r in WITH_DATA if not r.csa_certified)
NOT_IN_CATALOG = "761A00-11380000/A"  # the summary PDF's example; absent from the tables


async def _lookup(db, recorder: ToolRecorder, pn: str) -> None:
    result = await catalog_tools.get_actuator(db, GetActuatorArgs(part_number=pn))
    recorder.add("get_actuator", {"part_number": pn}, ToolOutput(result), 1)


async def _search(db, recorder: ToolRecorder, **filters) -> None:
    result = await catalog_tools.search_actuators(db, SearchFilters(**filters))
    specs = await catalog_tools.specs_for(db, result)
    recorder.add("search_actuators", filters, ToolOutput(result, specs), 1)


def _row_motor_power(pn: str):
    return BY_PN[pn].motor_power_w


# --- get_actuator -------------------------------------------------------------------------------


@pytest.mark.db
async def test_found_part_is_a_detail_card_with_db_values(catalog_session) -> None:
    rec = ToolRecorder()
    pn = WITH_DATA[0].part_number
    await _lookup(catalog_session, rec, pn)
    view = build_view(rec.calls, "answer")

    assert view.response_type == "detail"
    (product,) = view.products
    assert product.part_number == pn
    assert product.motor_power_w == _row_motor_power(pn)
    assert product.enclosure == BY_PN[pn].enclosure
    assert {s.type for s in product.service} == {s.service_type for s in BY_PN[pn].service_ratings}


@pytest.mark.db
async def test_two_found_parts_are_a_comparison(catalog_session) -> None:
    rec = ToolRecorder()
    for row in WITH_DATA[:2]:
        await _lookup(catalog_session, rec, row.part_number)
    view = build_view(rec.calls, "answer")
    assert view.response_type == "comparison"
    assert [p.part_number for p in view.products] == [r.part_number for r in WITH_DATA[:2]]


@pytest.mark.db
async def test_partial_match_asks_with_candidate_options(catalog_session) -> None:
    prefix = WITH_DATA[0].part_number[:4]
    expected = sorted(r.part_number for r in CATALOG if r.part_number.startswith(prefix))
    assert len(expected) > 1  # the prefix is ambiguous in the catalog
    rec = ToolRecorder()
    await _lookup(catalog_session, rec, prefix)
    view = build_view(rec.calls, "¿Cuál de ellos?")

    assert view.response_type == "clarification"
    assert view.clarification.question == "¿Cuál de ellos?"
    assert view.clarification.options == expected[: len(view.clarification.options)]
    assert view.products == []


@pytest.mark.db
async def test_unknown_part_is_not_found_with_suggestions(catalog_session) -> None:
    rec = ToolRecorder()
    await _lookup(catalog_session, rec, NOT_IN_CATALOG)
    view = build_view(rec.calls, "answer")
    assert view.response_type == "not_found"
    assert view.products == []
    assert view.suggestions and all(s in BY_PN for s in view.suggestions)


@pytest.mark.db
async def test_na_row_is_no_data_with_nulls_not_zeros(catalog_session) -> None:
    rec = ToolRecorder()
    await _lookup(catalog_session, rec, NO_DATA.part_number)
    view = build_view(rec.calls, "answer")

    assert view.response_type == "no_data"
    (product,) = view.products
    assert product.data_available is False
    assert product.motor_power_w is None
    assert product.service == [] and product.electrical == []
    assert "no_published_data" in view.warnings


@pytest.mark.db
async def test_not_csa_part_carries_the_warning(catalog_session) -> None:
    rec = ToolRecorder()
    await _lookup(catalog_session, rec, NOT_CSA.part_number)
    view = build_view(rec.calls, "answer")
    assert "not_csa_certified" in view.warnings
    assert "not_csa_certified" in view.products[0].notes


# --- search_actuators ---------------------------------------------------------------------------


@pytest.mark.db
async def test_search_with_matches_is_a_recommendation_with_full_cards(catalog_session) -> None:
    rec = ToolRecorder()
    await _search(catalog_session, rec, enclosure="weatherproof", service_type="modulating")
    view = build_view(rec.calls, "answer")

    assert view.response_type == "recommendation"
    assert view.products and all(p.meets_requirements for p in view.products)
    assert all(p.matched_service == "modulating" for p in view.products)
    assert all(p.electrical for p in view.products)  # full specs, not just the match summary
    assert set(view.highlight_fields) == {"enclosure", "service"}
    first = view.products[0].part_number
    assert view.products[0].motor_power_w == _row_motor_power(first)


@pytest.mark.db
async def test_impossible_search_is_no_match_with_alternatives(catalog_session) -> None:
    xp_max = max(
        s.torque_inlb
        for r in WITH_DATA
        if r.enclosure == "explosionproof"
        for s in r.service_ratings
        if s.torque_inlb is not None
    )
    rec = ToolRecorder()
    await _search(
        catalog_session, rec, enclosure="explosionproof", min_torque=xp_max + 1, torque_unit="in_lb"
    )
    view = build_view(rec.calls, "answer")

    assert view.response_type == "no_match"
    assert view.products and not any(p.meets_requirements for p in view.products)
    assert view.suggestions == [p.part_number for p in view.products]
    assert "torque" in view.highlight_fields


@pytest.mark.db
async def test_voltage_tolerance_is_reported(catalog_session) -> None:
    nominal = max(r.voltage_v for r in CATALOG)
    rec = ToolRecorder()
    await _search(catalog_session, rec, voltage_v=nominal + nominal // 20)  # +5 %: within tolerance
    view = build_view(rec.calls, "answer")
    assert "tolerance_applied" in view.warnings
    assert all(p.power_supply.voltage_v == nominal for p in view.products)


@pytest.mark.db
async def test_options_become_clarification_chips(catalog_session) -> None:
    rec = ToolRecorder()
    result = await catalog_tools.list_catalog_options(
        catalog_session, CatalogOptionsArgs(field="enclosure")
    )
    rec.add("list_catalog_options", {"field": "enclosure"}, ToolOutput(result), 1)
    view = build_view(rec.calls, "¿Qué encapsulado?")
    assert view.response_type == "clarification"
    assert view.clarification.options == sorted({r.enclosure for r in CATALOG})


# --- no tools / failures ------------------------------------------------------------------------


def test_no_tool_calls_is_a_clarification_without_options() -> None:
    view = build_view([], "¿Qué voltaje tienes?")
    assert view.response_type == "clarification" and view.clarification is None


def test_only_failed_calls_is_an_error() -> None:
    rec = ToolRecorder()
    rec.add("get_actuator", {"part_number": "x"}, None, 1, "RuntimeError: db down")
    view = build_view(rec.calls, "answer")
    assert view.response_type == "error"
    assert view.warnings == ["tool_error"]


# --- session state and titles -------------------------------------------------------------------


def test_update_parts_activates_new_products_and_keeps_history() -> None:
    state = update_parts({"active_parts": ["A"], "shown_parts": ["A"]}, ["B", "C"])
    assert state == {"active_parts": ["B", "C"], "shown_parts": ["A", "B", "C"]}
    assert update_parts(state, [])["active_parts"] == ["B", "C"]  # clarifications keep context


def test_title_prefers_the_part_number_then_truncates() -> None:
    pn = WITH_DATA[0].part_number
    assert make_title(f"datos del {pn.lower()} por favor") == pn
    long = "necesito un actuador " * 10
    title = make_title(long)
    assert len(title) == 60 and title.endswith("…")
    assert make_title("  hola   mundo ") == "hola mundo"
