"""Catalog tools without an LLM. Pure-logic tests use synthetic, made-up rows; the `db` tests run
against the real catalog and derive every expected value from catalog.json (never typed by hand).
The cases mirror "Casos borde y pruebas" in docs/design.md."""

import pytest
from pydantic import ValidationError

from app.schemas.catalog import (
    ActuatorSpec,
    CatalogOptionsArgs,
    ElectricalSpec,
    GetActuatorArgs,
    SearchFilters,
    ServiceSpec,
)
from app.tools import matching
from app.tools.catalog_tools import get_actuator, list_catalog_options, search_actuators
from app.tools.normalize import normalize_part_key
from ingest.load import read_catalog

CATALOG = read_catalog()
BY_PN = {r.part_number: r for r in CATALOG}
NOMINALS = sorted({r.voltage_v for r in CATALOG})


# --- Pure logic (no database) ---------------------------------------------------------------


def _spec(pn: str, *services: ServiceSpec, speeds=((60, 10), (50, 12)), **kw) -> ActuatorSpec:
    fields = dict(
        part_number=pn,
        series_size=1,
        variant="A",
        enclosure="weatherproof",
        certifications=["CSA"],
        csa_certified=True,
        hazardous_locations=None,
        voltage_v=220,
        phase=1,
        current_type="AC",
        motor_power_w=1,
        data_available=True,
        source_table="synthetic",
        source_page=1,
        service_ratings=list(services),
        electrical_ratings=[
            ElectricalSpec(frequency_hz=hz, speed_s=s, fla_a=None, lra_a=None) for hz, s in speeds
        ],
    )
    return ActuatorSpec(**(fields | kw))


def _svc(service_type: str, inlb: int, nm: int, duty: int = 50) -> ServiceSpec:
    return ServiceSpec(
        service_type=service_type,
        torque_inlb=inlb,
        torque_nm=nm,
        duty_cycle_pct=duty,
        cycles_per_hour=None,
        starts_per_hour=None,
    )


@pytest.mark.parametrize(
    "text", ["761a00 11300000 a", "761A00-11300000/A", " 761A00-11300000/A* ", "761a00.11300000.a"]
)
def test_normalize_part_key(text: str) -> None:
    assert normalize_part_key(text) == "761A0011300000A"


def test_normalization_matches_ingested_keys() -> None:
    assert all(normalize_part_key(r.part_number) == r.part_key for r in CATALOG)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(220, 220), (230, 220), (240, 220), (200, 220), (198, 220), (197, None), (243, None),
     (120, 110), (480, None), (24, 24), (26, 24), (27, None)],
)  # fmt: skip
def test_nominal_voltage_within_ten_percent(requested: int, expected: int | None) -> None:
    assert matching.nominal_voltage(requested, [24, 110, 220]) == expected


def test_ac_dc_supply_serves_both_currents() -> None:
    assert matching.current_matches("AC", "AC/DC") and matching.current_matches("DC", "AC/DC")
    assert matching.current_matches("AC", "AC") and not matching.current_matches("AC", "DC")
    assert not matching.current_matches("DC", "AC")


def test_speed_without_frequency_is_the_slowest_published() -> None:
    spec = _spec("S1", _svc("on_off", 100, 10), speeds=((60, 10), (50, 12)))
    assert matching.speed_s(spec, None) == 12
    assert matching.speed_s(spec, 60) == 10
    single = _spec("S2", _svc("on_off", 100, 10), speeds=((None, 7),))
    assert matching.speed_s(single, 50) == 7


def test_rank_by_smallest_margin_then_speed() -> None:
    specs = [
        _spec("BIG", _svc("on_off", 500, 50)),
        _spec("SLOW", _svc("on_off", 200, 20), speeds=((60, 30),)),
        _spec("FAST", _svc("on_off", 200, 20), speeds=((60, 5),)),
        _spec("WEAK", _svc("on_off", 100, 10)),
    ]
    filters = SearchFilters(min_torque=150, torque_unit="in_lb")
    outcome = matching.run_search(specs, filters, None)
    assert [c.actuator.part_number for c in outcome.matches] == ["FAST", "SLOW", "BIG"]


def test_torque_filter_uses_the_requested_unit_only() -> None:
    # Made-up pair where the units disagree: passes in in-lb, fails in Nm.
    specs = [_spec("S1", _svc("on_off", 1000, 10))]
    in_lb = SearchFilters(min_torque=900, torque_unit="in_lb")
    nm = SearchFilters(min_torque=900, torque_unit="nm")
    assert matching.run_search(specs, in_lb, None).matches
    assert not matching.run_search(specs, nm, None).matches


def test_relaxation_reports_each_blocking_filter() -> None:
    specs = [
        _spec("WP", _svc("on_off", 500, 50)),
        _spec("XP", _svc("on_off", 100, 10), enclosure="explosionproof", variant="X"),
    ]
    filters = SearchFilters(enclosure="explosionproof", min_torque=300, torque_unit="in_lb")
    outcome = matching.run_search(specs, filters, None)
    assert outcome.matches == []
    assert outcome.blocking == [(("min_torque",), 1), (("enclosure",), 1)]
    assert not outcome.multiple_blocking
    assert [(c.actuator.part_number, f) for c, f in outcome.alternatives] == [
        ("XP", ("min_torque",)),
        ("WP", ("enclosure",)),
    ]


def test_relaxation_falls_back_to_the_smallest_filter_set() -> None:
    specs = [_spec("WP", _svc("on_off", 100, 10), series_size=3)]
    filters = SearchFilters(
        enclosure="explosionproof", min_torque=300, torque_unit="in_lb", series_size=3
    )
    outcome = matching.run_search(specs, filters, None)
    # No single filter unblocks; the pair does, and series_size (met) is never relaxed.
    assert outcome.multiple_blocking
    assert outcome.blocking == [(("min_torque", "enclosure"), 1)]
    assert [(c.actuator.part_number, s) for c, s in outcome.alternatives] == [
        ("WP", ("min_torque", "enclosure"))
    ]


def test_round_robin_respects_a_small_limit() -> None:
    specs = [
        _spec("WP", _svc("on_off", 500, 50)),
        _spec("XP", _svc("on_off", 100, 10), enclosure="explosionproof", variant="X"),
    ]
    filters = SearchFilters(
        enclosure="explosionproof", min_torque=300, torque_unit="in_lb", limit=1
    )
    outcome = matching.run_search(specs, filters, None)
    assert len(outcome.blocking) == 2
    assert [(c.actuator.part_number, f) for c, f in outcome.alternatives] == [
        ("XP", ("min_torque",))
    ]


def test_without_torque_request_rank_by_smallest_inlb() -> None:
    specs = [_spec("BIG", _svc("on_off", 500, 50)), _spec("SMALL", _svc("on_off", 100, 10))]
    outcome = matching.run_search(specs, SearchFilters(), None)
    assert [c.actuator.part_number for c in outcome.matches] == ["SMALL", "BIG"]


def test_fractional_torque_request_is_not_rounded() -> None:
    specs = [_spec("S1", _svc("on_off", 100, 10))]
    assert matching.run_search(
        specs, SearchFilters(min_torque="9.5", torque_unit="nm"), None
    ).matches
    assert not matching.run_search(
        specs, SearchFilters(min_torque="10.5", torque_unit="nm"), None
    ).matches


def test_speed_limit_with_and_without_frequency() -> None:
    specs = [_spec("S1", _svc("on_off", 100, 10), speeds=((60, 10), (50, 12)))]
    assert matching.run_search(specs, SearchFilters(max_speed_s=10, frequency_hz=60), None).matches
    assert not matching.run_search(specs, SearchFilters(max_speed_s=10), None).matches
    assert matching.run_search(specs, SearchFilters(max_speed_s=12), None).matches


def test_duty_cycle_and_size_filters() -> None:
    specs = [
        _spec("D30", _svc("on_off", 100, 10, duty=30)),
        _spec("D60", _svc("on_off", 100, 10, duty=60), series_size=2),
    ]
    duty = matching.run_search(specs, SearchFilters(min_duty_cycle_pct=50), None)
    assert [c.actuator.part_number for c in duty.matches] == ["D60"]
    size = matching.run_search(specs, SearchFilters(series_size=1), None)
    assert [c.actuator.part_number for c in size.matches] == ["D30"]


def test_torque_requires_unit() -> None:
    with pytest.raises(ValidationError):
        SearchFilters(min_torque=100)


# --- get_actuator (real catalog) ------------------------------------------------------------


@pytest.mark.db
async def test_summary_example_is_not_found_with_suggestions(catalog_session) -> None:
    result = await get_actuator(catalog_session, GetActuatorArgs(part_number="761A00-11380000/A"))
    assert "761A00-11380000/A" not in BY_PN
    assert result.status == "not_found" and result.actuator is None
    suggested = [s.part_number for s in result.suggestions]
    assert suggested and all(pn in BY_PN for pn in suggested)


@pytest.mark.db
async def test_informal_part_number_is_found(catalog_session) -> None:
    result = await get_actuator(catalog_session, GetActuatorArgs(part_number="761a00 11300000 a"))
    assert result.status == "found"
    expected = BY_PN["761A00-11300000/A"]
    assert result.actuator.part_number == expected.part_number
    got = {s.service_type: s.torque_inlb for s in result.actuator.service_ratings}
    assert got == {s.service_type: s.torque_inlb for s in expected.service_ratings}


@pytest.mark.db
async def test_short_name_asks_between_tables(catalog_session) -> None:
    result = await get_actuator(catalog_session, GetActuatorArgs(part_number="763C"))
    expected = sorted(pn for pn, r in BY_PN.items() if r.part_key.startswith("763C"))
    assert result.status == "partial_match"
    assert [c.part_number for c in result.candidates] == expected
    assert result.candidate_count == len(expected) > 1
    assert len({(c.voltage_v, c.phase) for c in result.candidates}) == 3


@pytest.mark.db
async def test_na_row_has_no_data(catalog_session) -> None:
    result = await get_actuator(catalog_session, GetActuatorArgs(part_number="762A00-11330C00/A"))
    assert BY_PN["762A00-11330C00/A"].data_available is False
    assert result.status == "no_data"
    spec = result.actuator
    assert spec.motor_power_w is None
    assert spec.service_ratings == [] and spec.electrical_ratings == []
    assert "no_published_data" in spec.warnings


@pytest.mark.db
async def test_three_phase_part_warns_service_unspecified(catalog_session) -> None:
    result = await get_actuator(catalog_session, GetActuatorArgs(part_number="763C00-11330C00/A"))
    assert result.status == "found"
    assert [s.service_type for s in result.actuator.service_ratings] == ["unspecified"]
    assert "service_type_unspecified" in result.actuator.warnings


@pytest.mark.db
@pytest.mark.parametrize("size", [6, 7])
async def test_sizes_6_and_7_warn_not_csa(catalog_session, size: int) -> None:
    parts = [pn for pn, r in BY_PN.items() if r.series_size == size]
    assert parts
    for pn in parts:
        result = await get_actuator(catalog_session, GetActuatorArgs(part_number=pn))
        assert "not_csa_certified" in result.actuator.warnings


@pytest.mark.db
async def test_csa_part_has_no_csa_warning(catalog_session) -> None:
    pn = next(pn for pn, r in BY_PN.items() if r.csa_certified and r.data_available)
    result = await get_actuator(catalog_session, GetActuatorArgs(part_number=pn))
    assert result.matched_by == "exact"
    assert "not_csa_certified" not in result.actuator.warnings


@pytest.mark.db
async def test_unique_prefix_is_found_but_flagged(catalog_session) -> None:
    record = CATALOG[0]
    prefix = record.part_key[:-1]
    assert [r for r in CATALOG if r.part_key.startswith(prefix)] == [record]
    result = await get_actuator(catalog_session, GetActuatorArgs(part_number=prefix))
    assert result.status == "found" and result.matched_by == "prefix"
    assert result.actuator.part_number == record.part_number


@pytest.mark.db
async def test_empty_key_is_not_found(catalog_session) -> None:
    result = await get_actuator(catalog_session, GetActuatorArgs(part_number="--/"))
    assert result.status == "not_found" and result.suggestions == []


# --- search_actuators (real catalog) --------------------------------------------------------


@pytest.mark.db
async def test_24v_ac_modulating_is_blocked_by_current_and_service(catalog_session) -> None:
    filters = SearchFilters(voltage_v=24, current_type="AC", service_type="modulating")
    result = await search_actuators(catalog_session, filters)
    assert result.matches == []
    blocking = {tuple(b.filters) for b in result.blocking_filters}
    assert {("service_type",), ("current_type",)} <= blocking
    alternatives = {(m.current_type, m.service_type, *m.unmet) for m in result.closest_alternatives}
    assert ("DC", "modulating", "current_type") in alternatives
    assert ("AC/DC", "on_off", "service_type") in alternatives
    assert not any(m.meets_requirements for m in result.closest_alternatives)


@pytest.mark.db
async def test_explosionproof_above_max_torque_offers_the_strongest(catalog_session) -> None:
    xp_max = max(
        s.torque_inlb
        for r in CATALOG
        if r.enclosure == "explosionproof"
        for s in r.service_ratings
        if s.torque_inlb is not None
    )
    filters = SearchFilters(enclosure="explosionproof", min_torque=xp_max + 1, torque_unit="in_lb")
    result = await search_actuators(catalog_session, filters)
    assert result.matches == []
    assert ["min_torque"] in [b.filters for b in result.blocking_filters]
    first = result.closest_alternatives[0]
    assert first.unmet == ["min_torque"] and first.meets_requirements is False
    assert first.enclosure == "explosionproof" and first.torque_inlb == xp_max
    assert first.torque_margin == -1


@pytest.mark.db
async def test_two_blocking_filters_return_the_smallest_relaxation(catalog_session) -> None:
    # Explosionproof 3-phase does not exist, and no model reaches the requested torque.
    catalog_max = max(s.torque_inlb for r in CATALOG for s in r.service_ratings if s.torque_inlb)
    assert not any(r.enclosure == "explosionproof" and r.phase == 3 for r in CATALOG)
    filters = SearchFilters(
        enclosure="explosionproof", phase=3, min_torque=catalog_max + 1, torque_unit="in_lb"
    )
    result = await search_actuators(catalog_session, filters)
    assert result.matches == [] and result.multiple_filters_blocking
    sets = {tuple(b.filters) for b in result.blocking_filters}
    assert sets == {("min_torque", "phase"), ("min_torque", "enclosure")}
    assert result.closest_alternatives
    for m in result.closest_alternatives:
        assert not m.meets_requirements and tuple(m.unmet) in sets
        assert m.torque_inlb <= catalog_max


@pytest.mark.db
async def test_size_7_with_csa_conflict(catalog_session) -> None:
    result = await search_actuators(
        catalog_session, SearchFilters(series_size=7, csa_required=True)
    )
    assert result.matches == []
    blocking = {tuple(b.filters) for b in result.blocking_filters}
    assert {("csa_required",), ("series_size",)} <= blocking


@pytest.mark.db
async def test_230v_uses_the_220v_tables(catalog_session) -> None:
    result = await search_actuators(catalog_session, SearchFilters(voltage_v=230, limit=10))
    assert result.tolerance_applied is True and result.matched_voltage_v == 220
    assert result.matches and all(m.voltage_v == 220 for m in result.matches)
    # No frequency requested: each speed is labelled with the frequency it was read at.
    for m in result.matches:
        rows = {e.frequency_hz: e.speed_s for e in BY_PN[m.part_number].electrical_ratings}
        assert rows[m.speed_frequency_hz] == m.speed_s == max(rows.values())


@pytest.mark.db
async def test_csa_required_excludes_uncertified_with_alternatives(catalog_session) -> None:
    result = await search_actuators(catalog_session, SearchFilters(csa_required=True, limit=10))
    assert result.matches and all(m.csa_certified for m in result.matches)
    uncertified = {pn for pn, r in BY_PN.items() if not r.csa_certified}
    assert not uncertified & {m.part_number for m in result.matches}


@pytest.mark.db
async def test_exact_voltage_does_not_flag_tolerance(catalog_session) -> None:
    result = await search_actuators(catalog_session, SearchFilters(voltage_v=220))
    assert result.tolerance_applied is False and result.matched_voltage_v == 220


@pytest.mark.db
async def test_out_of_tolerance_voltage_blocks(catalog_session) -> None:
    result = await search_actuators(catalog_session, SearchFilters(voltage_v=480))
    assert result.matches == [] and result.matched_voltage_v is None
    assert [b.filters for b in result.blocking_filters] == [["voltage_v"]]


@pytest.mark.db
@pytest.mark.parametrize("unit", ["in_lb", "nm"])
async def test_torque_in_user_unit_ranked_by_margin(catalog_session, unit: str) -> None:
    column = "torque_inlb" if unit == "in_lb" else "torque_nm"
    pool = [
        (r, s)
        for r in CATALOG
        if r.data_available and r.voltage_v == 220 and r.phase == 1
        for s in r.service_ratings
        if s.service_type == "modulating"
    ]
    torques = sorted({getattr(s, column) for _, s in pool})
    minimum = torques[len(torques) // 2]
    filters = SearchFilters(
        voltage_v=220, phase=1, service_type="modulating", min_torque=minimum, torque_unit=unit,
        limit=10,
    )  # fmt: skip
    result = await search_actuators(catalog_session, filters)
    slowest = {r.part_number: max(e.speed_s for e in r.electrical_ratings) for r, _ in pool}
    expected = sorted(
        (getattr(s, column) - minimum, slowest[r.part_number], r.part_number)
        for r, s in pool
        if getattr(s, column) >= minimum
    )
    assert [m.part_number for m in result.matches] == [pn for *_, pn in expected][:10]
    margins = [m.torque_margin for m in result.matches]
    assert margins == sorted(margins) and margins[0] >= 0
    assert all(getattr(m, column) - minimum == m.torque_margin for m in result.matches)


@pytest.mark.db
async def test_three_phase_excluded_from_on_off_but_listed_with_warning(catalog_session) -> None:
    on_off = await search_actuators(
        catalog_session, SearchFilters(voltage_v=220, service_type="on_off", limit=10)
    )
    assert on_off.matches and all(m.phase == 1 for m in on_off.matches)

    any_service = await search_actuators(
        catalog_session, SearchFilters(voltage_v=220, phase=3, limit=10)
    )
    assert any_service.matches
    for m in any_service.matches:
        assert m.service_type == "unspecified"
        assert "service_type_unspecified" in m.warnings


@pytest.mark.db
async def test_na_rows_never_match(catalog_session) -> None:
    na = {pn for pn, r in BY_PN.items() if not r.data_available}
    result = await search_actuators(catalog_session, SearchFilters(phase=3, limit=10))
    assert not na & {m.part_number for m in result.matches}


# --- list_catalog_options (real catalog) ----------------------------------------------------


@pytest.mark.db
async def test_catalog_options_come_from_the_catalog(catalog_session) -> None:
    options = await list_catalog_options(catalog_session, CatalogOptionsArgs())
    supplies = {(p.enclosure, p.voltage_v, p.phase, p.current_type) for p in options.power_supplies}
    assert supplies == {(r.enclosure, r.voltage_v, r.phase, r.current_type) for r in CATALOG}
    assert ("weatherproof", 220, 3, "AC") in supplies

    xp_on_off = [
        s.torque_inlb
        for r in CATALOG
        if r.enclosure == "explosionproof"
        for s in r.service_ratings
        if s.service_type == "on_off"
    ]
    xp_range = next(
        t
        for t in options.torque_ranges
        if t.enclosure == "explosionproof" and t.service_type == "on_off"
    )
    assert (xp_range.min_inlb, xp_range.max_inlb) == (min(xp_on_off), max(xp_on_off))
    assert options.series_sizes == sorted({r.series_size for r in CATALOG})


@pytest.mark.db
async def test_catalog_options_single_field(catalog_session) -> None:
    options = await list_catalog_options(catalog_session, CatalogOptionsArgs(field="enclosure"))
    assert options.enclosures == ["explosionproof", "weatherproof"]
    assert options.power_supplies is None and options.torque_ranges is None
