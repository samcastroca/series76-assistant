"""The evaluation dataset: generated deterministically from catalog.json, valid, and the
committed cases.jsonl is up to date. No LLM, no database."""

import json

from eval import generate_cases as gen

CATALOG = gen.load_catalog()
CASES = gen.build_cases()
BY_ID = {c["id"]: c for c in CASES}


def test_generation_is_deterministic() -> None:
    assert gen.render(gen.build_cases()) == gen.render(CASES)


def test_committed_cases_file_is_up_to_date() -> None:
    committed = gen.CASES_PATH.read_text(encoding="utf-8")
    assert committed == gen.render(CASES), "run: make eval-cases"


def test_ids_are_unique_and_every_case_has_one_expected_entry_per_turn() -> None:
    assert len(BY_ID) == len(CASES)
    assert all(len(c["expected"]) == len(c["turns"]) for c in CASES)


def test_exactly_twenty_fast_cases_covering_every_category() -> None:
    fast = [c for c in CASES if c["fast"]]
    assert len(fast) == 20
    assert {c["category"] for c in fast} == set(gen.CATEGORIES)


def test_every_category_is_present() -> None:
    assert {c["category"] for c in CASES} == set(gen.CATEGORIES)


def test_expected_part_numbers_exist_in_the_catalog() -> None:
    known = {a["part_number"] for a in CATALOG}
    for c in CASES:
        for e in c["expected"]:
            assert set(e["part_numbers"] or []) <= known, c["id"]


def test_manual_cases_are_merged() -> None:
    manual = gen.load_manual()
    assert manual and all(BY_ID[m["id"]]["source"] == "manual" for m in manual)


def test_search_expectations_match_the_catalog() -> None:
    """Re-derive each search case from the catalog: every expected part has a rating that meets
    the stated filters, and no other part does."""
    for t in gen.SEARCH_TEMPLATES:
        spec = gen.with_threshold(CATALOG, t.base, t.count)
        case = BY_ID[t.case_id]
        expected = case["expected"][0]
        pairs = gen.eligible(CATALOG, spec)
        assert sorted({pn for pn, _ in pairs}) == expected["part_numbers"]
        assert 1 <= len(pairs) <= gen.SEARCH_LIMIT
        assert str(spec.min_torque) in case["turns"][0]
        assert expected["tool_args"][0]["args"]["min_torque"] == spec.min_torque


def test_threshold_is_a_real_catalog_torque() -> None:
    torques = {(r["torque_inlb"], r["torque_nm"]) for a in CATALOG for r in a["service_ratings"]}
    for t in gen.SEARCH_TEMPLATES:
        spec = gen.with_threshold(CATALOG, t.base, t.count)
        column = 0 if spec.torque_unit == "in_lb" else 1
        assert spec.min_torque in {pair[column] for pair in torques}


def test_three_phase_modulating_has_no_match() -> None:
    spec = gen.SearchSpec(voltage_v=220, phase=3, service_type="modulating")
    assert gen.eligible(CATALOG, spec) == []
    assert BY_ID["no-match-3ph-modulating"]["expected"][0]["response_type"] == "no_match"


def test_missing_data_cases_use_rows_without_published_data() -> None:
    rows = {a["part_number"]: a for a in CATALOG}
    for c in CASES:
        if c["category"] == "missing_data":
            (pn,) = c["expected"][0]["part_numbers"]
            assert rows[pn]["data_available"] is False
            assert c["expected"][0]["must_not_contain_specs"] is True


def test_csa_cases_use_rows_that_are_not_csa_certified() -> None:
    rows = {a["part_number"]: a for a in CATALOG}
    for case_id in ("csa-not-certified-1", "csa-not-certified-2"):
        (pn,) = BY_ID[case_id]["expected"][0]["part_numbers"]
        assert rows[pn]["csa_certified"] is False


def test_sibling_follow_up_targets_the_same_size_and_supply() -> None:
    rows = {a["part_number"]: a for a in CATALOG}
    first, follow = BY_ID["multi-turn-sibling-variant"]["expected"]
    a, b = rows[first["part_numbers"][0]], rows[follow["part_numbers"][0]]
    assert a["source_table"] == b["source_table"] and a["series_size"] == b["series_size"]
    assert a["variant"] != b["variant"] and follow["coreference"] is True


def test_cases_file_is_one_json_object_per_line() -> None:
    lines = gen.CASES_PATH.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["id"] for line in lines] == [c["id"] for c in CASES]
