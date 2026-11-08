"""Generate the agent evaluation dataset.

Template cases are built from data/processed/catalog.json: every expected part number, filter
value and torque threshold is computed here from the catalog, never typed by hand. They are merged
with the hand-written eval/cases_manual.jsonl (edge cases that need no catalog values) into
eval/cases.jsonl, and FAST_IDS marks the cases run by `make eval-fast`.

Selection is deterministic (catalog order, fixed indices), so re-running gives the same file.

    uv run python -m eval.generate_cases
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.schemas.catalog import SearchFilters

EVAL_DIR = Path(__file__).resolve().parent
CATALOG_PATH = EVAL_DIR.parent / "data" / "processed" / "catalog.json"
MANUAL_PATH = EVAL_DIR / "cases_manual.jsonl"
CASES_PATH = EVAL_DIR / "cases.jsonl"

CATEGORIES = (
    "detail",
    "search",
    "comparison",
    "multi_turn",
    "invalid_part",
    "missing_data",
    "ambiguous",
    "no_match",
    "certification_tolerance",
    "out_of_scope",
)
RESPONSE_TYPES = (
    "detail",
    "recommendation",
    "comparison",
    "clarification",
    "no_match",
    "not_found",
    "no_data",
    "error",
)
# search_actuators' default limit: an expected set must fit in one response.
SEARCH_LIMIT: int = SearchFilters.model_fields["limit"].default

# The ~20 most critical cases, run by `make eval-fast`. Each category appears at least once.
FAST_IDS = (
    "detail-wp_110v_1ph",
    "detail-wp_220v_3ph",
    "detail-xp_24v_modulating",
    "detail-normalized-part-number",
    "missing-data-1",
    "csa-not-certified-1",
    "tolerance-230v",
    "search-wp110-modulating-inlb",
    "search-xp24-modulating-nm",
    "search-3ph-nm",
    "search-csa-required-on-off",
    "comparison-wp_110v_1ph",
    "comparison-xp_24v_on_off",
    "multi-turn-power-followup",
    "multi-turn-sibling-variant",
    "multi-turn-search-then-compare",
    "not-found-summary-example",
    "ambiguous-generic-request",
    "no-match-3ph-modulating",
    "out-of-scope-capital",
)
FAST_COUNT = 20


# --- Catalog access ----------------------------------------------------------------------------


def load_catalog(path: Path = CATALOG_PATH) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return sorted(data["actuators"], key=lambda a: (a["source_table"], a["part_number"]))


def tables(catalog: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(a["source_table"] for a in catalog))


def rows_of(catalog: list[dict[str, Any]], table: str) -> list[dict[str, Any]]:
    return [a for a in catalog if a["source_table"] == table]


def with_data(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in rows if a["data_available"]]


# --- Search oracle -----------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchSpec:
    """Exact filters over catalog fields; a deliberately simple oracle, independent of the
    backend's search code (no voltage tolerance, no ranking)."""

    enclosure: str | None = None
    voltage_v: int | None = None
    phase: int | None = None
    current_type: str | None = None  # 'AC' or 'DC'; AC/DC supplies match both
    service_type: str | None = None
    csa_required: bool = False
    min_torque: int | None = None
    torque_unit: str | None = None  # 'in_lb' or 'nm'


def _torque(rating: dict[str, Any], unit: str) -> int | None:
    return rating["torque_inlb"] if unit == "in_lb" else rating["torque_nm"]


def eligible(
    catalog: list[dict[str, Any]], spec: SearchSpec, ignore_torque: bool = False
) -> list[tuple[str, str]]:
    """(part_number, service_type) pairs that meet every filter, as search_actuators counts its
    matches (one per actuator and service rating)."""
    out = []
    for a in with_data(catalog):
        if spec.enclosure and a["enclosure"] != spec.enclosure:
            continue
        if spec.voltage_v is not None and a["voltage_v"] != spec.voltage_v:
            continue
        if spec.phase is not None and a["phase"] != spec.phase:
            continue
        if spec.current_type and a["current_type"] not in (spec.current_type, "AC/DC"):
            continue
        if spec.csa_required and not a["csa_certified"]:
            continue
        for r in a["service_ratings"]:
            if spec.service_type and r["service_type"] != spec.service_type:
                continue
            if spec.min_torque is not None and not ignore_torque:
                value = _torque(r, spec.torque_unit or "in_lb")
                if value is None or value < spec.min_torque:
                    continue
            out.append((a["part_number"], r["service_type"]))
    return out


def with_threshold(catalog: list[dict[str, Any]], spec: SearchSpec, count: int) -> SearchSpec:
    """The same spec with min_torque set to a real catalog torque so that `count` matches pass:
    the count-th highest torque among the matches without a torque filter."""
    unit = spec.torque_unit or "in_lb"
    ratings = []
    for pn, service in eligible(catalog, spec, ignore_torque=True):
        a = next(x for x in catalog if x["part_number"] == pn)
        r = next(x for x in a["service_ratings"] if x["service_type"] == service)
        if (value := _torque(r, unit)) is not None:
            ratings.append(value)
    threshold = sorted(ratings, reverse=True)[count - 1]
    return SearchSpec(**{**spec.__dict__, "min_torque": threshold, "torque_unit": unit})


def expected_parts(catalog: list[dict[str, Any]], spec: SearchSpec) -> list[str]:
    pairs = eligible(catalog, spec)
    if not 1 <= len(pairs) <= SEARCH_LIMIT:
        raise ValueError(f"{spec} yields {len(pairs)} matches; expected 1..{SEARCH_LIMIT}")
    return sorted(dict.fromkeys(pn for pn, _ in pairs))


def search_args(spec: SearchSpec) -> dict[str, Any]:
    """The filters the query states explicitly, as search_actuators arguments."""
    args = {k: v for k, v in spec.__dict__.items() if v is not None and v is not False}
    return args


# --- Case builders -----------------------------------------------------------------------------


def turn(
    tools: list[str] | None,
    response_type: str,
    part_numbers: list[str] | None,
    tool_args: list[dict[str, Any]] | None = None,
    coreference: bool = False,
    must_not_contain_numbers: bool = False,
) -> dict[str, Any]:
    return {
        "tools": tools,
        "tool_args": tool_args or [],
        "response_type": response_type,
        "part_numbers": part_numbers,
        "ordered": False,
        "coreference": coreference,
        "must_not_contain_numbers": must_not_contain_numbers,
    }


def case(
    case_id: str, category: str, turns: list[str], expected: list[dict], notes: str
) -> dict[str, Any]:
    return {
        "id": case_id,
        "category": category,
        "fast": False,
        "source": "generated",
        "turns": turns,
        "expected": expected,
        "notes": notes,
    }


def lookup(pn: str) -> dict[str, Any]:
    return {"tool": "get_actuator", "args": {"part_number": pn}}


DETAIL_QUESTIONS = (
    "¿Cuál es el torque del {pn}?",
    "What is the stroke time of the {pn}?",
    "¿Qué potencia tiene el motor del {pn}?",
    "What duty cycle does the {pn} have?",
    "Dame las especificaciones del {pn}",
    "How much current does the {pn} draw?",
    "¿Qué certificaciones tiene el {pn}?",
)


def detail_cases(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for i, table in enumerate(tables(catalog)):
        rows = with_data(rows_of(catalog, table))
        pn = rows[i % len(rows)]["part_number"]
        question = DETAIL_QUESTIONS[i % len(DETAIL_QUESTIONS)].format(pn=pn)
        expected = turn(["get_actuator"], "detail", [pn], [lookup(pn)])
        notes = f"Direct lookup in {table}."
        if table == "wp_220v_3ph":
            notes += " 3-phase ratings have no on/off vs modulating split; the agent must say so."
        out.append(case(f"detail-{table}", "detail", [question], [expected], notes))
    return out


def missing_data_cases(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [a for a in catalog if not a["data_available"]]
    picks = [rows[0], rows[-1]]
    questions = ("¿Qué torque tiene el {pn}?", "Give me the specs of {pn}")
    return [
        case(
            f"missing-data-{i + 1}",
            "missing_data",
            [questions[i].format(pn=a["part_number"])],
            [
                turn(
                    ["get_actuator"],
                    "no_data",
                    [a["part_number"]],
                    [lookup(a["part_number"])],
                    must_not_contain_numbers=True,
                )
            ],
            "N/A row in the data PDF: the part exists but no specs are published.",
        )
        for i, a in enumerate(picks)
    ]


def certification_cases(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [a for a in with_data(catalog) if not a["csa_certified"]]
    picks = [rows[0], rows[-1]]
    questions = ("¿El {pn} tiene certificación CSA?", "Is the {pn} CSA certified?")
    return [
        case(
            f"csa-not-certified-{i + 1}",
            "certification_tolerance",
            [questions[i].format(pn=a["part_number"])],
            [turn(["get_actuator"], "detail", [a["part_number"]], [lookup(a["part_number"])])],
            "Marked '*' in the table: not CSA certified (csa_certified = false).",
        )
        for i, a in enumerate(picks)
    ]


@dataclass(frozen=True)
class SearchTemplate:
    case_id: str
    base: SearchSpec
    count: int  # how many matches the computed threshold lets through
    query: Callable[[SearchSpec], str]
    notes: str
    stated: tuple[str, ...] = ()  # filters the query states; default: all set in the spec


def _unit(spec: SearchSpec) -> str:
    return "in-lb" if spec.torque_unit == "in_lb" else "Nm"


SEARCH_TEMPLATES = (
    SearchTemplate(
        "search-wp110-modulating-inlb",
        SearchSpec("weatherproof", 110, 1, None, "modulating", torque_unit="in_lb"),
        3,
        lambda s: (
            f"Necesito un actuador weatherproof de 110 V monofásico, modulante, con al menos "
            f"{s.min_torque} {_unit(s)}"
        ),
        "Exact voltage, service type and torque floor in in-lb.",
        ("enclosure", "voltage_v", "service_type", "min_torque", "torque_unit"),
    ),
    SearchTemplate(
        "search-wp220-on-off-nm",
        SearchSpec("weatherproof", 220, 1, None, "on_off", torque_unit="nm"),
        2,
        lambda s: (
            f"I need a weatherproof on/off actuator, 220 V single phase, at least "
            f"{s.min_torque} {_unit(s)}"
        ),
        "Top of the 220 V 1-phase table: sizes 6/7 are not CSA certified; the agent must say so.",
        ("enclosure", "voltage_v", "service_type", "min_torque", "torque_unit"),
    ),
    SearchTemplate(
        "search-xp110-on-off-inlb",
        SearchSpec("explosionproof", 110, 1, None, "on_off", torque_unit="in_lb"),
        3,
        lambda s: (
            f"Busco un actuador a prueba de explosión, 110 V, on/off, de mínimo "
            f"{s.min_torque} {_unit(s)}"
        ),
        "Explosionproof 110 V search.",
        ("enclosure", "voltage_v", "service_type", "min_torque", "torque_unit"),
    ),
    SearchTemplate(
        "search-xp24-modulating-nm",
        SearchSpec("explosionproof", 24, None, "DC", "modulating", torque_unit="nm"),
        2,
        lambda s: (
            f"Necesito un actuador modulante a prueba de explosión de 24 VDC con al menos "
            f"{s.min_torque} {_unit(s)}"
        ),
        "24 V modulating exists only as DC.",
        ("voltage_v", "service_type", "min_torque", "torque_unit"),
    ),
    SearchTemplate(
        "search-3ph-nm",
        SearchSpec("weatherproof", 220, 3, torque_unit="nm"),
        3,
        lambda s: f"Actuador trifásico de 220 V con al menos {s.min_torque} {_unit(s)}",
        "3-phase table: one torque column, service type unspecified; the agent must say so.",
        ("voltage_v", "phase", "min_torque", "torque_unit"),
    ),
    SearchTemplate(
        "search-csa-required-on-off",
        SearchSpec("weatherproof", 220, 1, None, "on_off", csa_required=True, torque_unit="in_lb"),
        2,
        lambda s: (
            f"I need a CSA certified weatherproof on/off actuator for 220 V single phase with at "
            f"least {s.min_torque} {_unit(s)}"
        ),
        "CSA filter excludes sizes 6/7 of the 220 V 1-phase table.",
        ("voltage_v", "service_type", "csa_required", "min_torque", "torque_unit"),
    ),
)


def _search_tool_args(spec: SearchSpec, stated: tuple[str, ...]) -> list[dict[str, Any]]:
    args = search_args(spec)
    return [{"tool": "search_actuators", "args": {k: args[k] for k in stated if k in args}}]


def search_cases(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in SEARCH_TEMPLATES:
        spec = with_threshold(catalog, t.base, t.count)
        parts = expected_parts(catalog, spec)
        expected = turn(
            ["search_actuators"], "recommendation", parts, _search_tool_args(spec, t.stated)
        )
        out.append(case(t.case_id, "search", [t.query(spec)], [expected], t.notes))

    # No match: 3-phase ratings are 'unspecified', so asking for modulating excludes them all.
    spec = SearchSpec(voltage_v=220, phase=3, service_type="modulating")
    if eligible(catalog, spec):
        raise ValueError("3-phase modulating unexpectedly has matches")
    out.append(
        case(
            "no-match-3ph-modulating",
            "no_match",
            ["Necesito un actuador modulante trifásico de 220 V"],
            [
                turn(
                    ["search_actuators"],
                    "no_match",
                    None,  # closest alternatives are shown; which ones is the backend's ranking
                    _search_tool_args(spec, ("voltage_v", "phase", "service_type")),
                )
            ],
            "The 3-phase table has no modulating split: no match, closest alternatives instead.",
        )
    )
    return out


def comparison_cases(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    questions = ("Compara el {a} con el {b}", "Compare {a} and {b}")
    out = []
    for i, table in enumerate(("wp_110v_1ph", "xp_24v_on_off", "xp_220v_1ph", "wp_220v_3ph")):
        rows = with_data(rows_of(catalog, table))
        a, b = rows[0]["part_number"], rows[1]["part_number"]
        out.append(
            case(
                f"comparison-{table}",
                "comparison",
                [questions[i % 2].format(a=a, b=b)],
                [
                    turn(
                        ["get_actuator", "get_actuator"],
                        "comparison",
                        [a, b],
                        [lookup(a), lookup(b)],
                    )
                ],
                f"Two part numbers from {table}.",
            )
        )
    return out


def multi_turn_cases(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []

    pn = with_data(rows_of(catalog, "wp_220v_1ph"))[2]["part_number"]
    out.append(
        case(
            "multi-turn-power-followup",
            "multi_turn",
            [f"¿Qué torque tiene el {pn}?", "¿Y cuánta corriente consume?"],
            [
                turn(["get_actuator"], "detail", [pn], [lookup(pn)]),
                turn(["get_actuator"], "detail", [pn], [lookup(pn)], coreference=True),
            ],
            "Follow-up resolved from the active part; the agent must call the tool again.",
        )
    )

    pn = with_data(rows_of(catalog, "xp_110v_1ph"))[4]["part_number"]
    out.append(
        case(
            "multi-turn-speed-50hz",
            "multi_turn",
            [f"Tell me about the {pn}", "What about its speed at 50 Hz?"],
            [
                turn(["get_actuator"], "detail", [pn], [lookup(pn)]),
                turn(["get_actuator"], "detail", [pn], [lookup(pn)], coreference=True),
            ],
            "English follow-up about the same product.",
        )
    )

    # Sibling: same table and size, next variant.
    rows = with_data(rows_of(catalog, "wp_110v_1ph"))
    first = next(
        a
        for a in rows
        if any(b["series_size"] == a["series_size"] and b["variant"] > a["variant"] for b in rows)
    )
    sibling = min(
        (b for b in rows if b["series_size"] == first["series_size"]),
        key=lambda b: (b["variant"] <= first["variant"], b["variant"]),
    )
    pa, pb = first["part_number"], sibling["part_number"]
    out.append(
        case(
            "multi-turn-sibling-variant",
            "multi_turn",
            [f"Dame los datos del {pa}", f"¿Y el modelo {sibling['variant']} del mismo tamaño?"],
            [
                turn(["get_actuator"], "detail", [pa], [lookup(pa)]),
                turn(["get_actuator"], "detail", [pb], [lookup(pb)], coreference=True),
            ],
            "The follow-up names a variant; the agent must build the sibling part number from "
            "the active one (same size and supply).",
        )
    )

    base = SearchSpec("explosionproof", 220, 1, None, "modulating", torque_unit="nm")
    spec = with_threshold(catalog, base, 2)
    parts = expected_parts(catalog, spec)
    stated = ("enclosure", "voltage_v", "service_type", "min_torque", "torque_unit")
    out.append(
        case(
            "multi-turn-search-then-compare",
            "multi_turn",
            [
                f"Busco un actuador a prueba de explosión, 220 V, modulante, de al menos "
                f"{spec.min_torque} Nm",
                "Compara esos dos",
            ],
            [
                turn(
                    ["search_actuators"], "recommendation", parts, _search_tool_args(spec, stated)
                ),
                turn(
                    ["get_actuator", "get_actuator"],
                    "comparison",
                    parts,
                    [lookup(p) for p in parts],
                    coreference=True,
                ),
            ],
            "The search returns exactly two products; 'esos dos' refers to them.",
        )
    )
    return out


# --- Assembly ----------------------------------------------------------------------------------


def generated_cases(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        *detail_cases(catalog),
        *missing_data_cases(catalog),
        *certification_cases(catalog),
        *search_cases(catalog),
        *comparison_cases(catalog),
        *multi_turn_cases(catalog),
    ]


def load_manual(path: Path = MANUAL_PATH) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append({"fast": False, **json.loads(line), "source": "manual"})
    return out


def validate(cases: list[dict[str, Any]], catalog: list[dict[str, Any]]) -> None:
    known = {a["part_number"] for a in catalog}
    ids = [c["id"] for c in cases]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise ValueError(f"duplicate case ids: {dupes}")
    for c in cases:
        if c["category"] not in CATEGORIES:
            raise ValueError(f"{c['id']}: unknown category {c['category']!r}")
        if len(c["expected"]) != len(c["turns"]):
            raise ValueError(f"{c['id']}: one expected entry per turn")
        for e in c["expected"]:
            if e["response_type"] not in RESPONSE_TYPES:
                raise ValueError(f"{c['id']}: unknown response_type {e['response_type']!r}")
            if unknown := set(e.get("part_numbers") or []) - known:
                raise ValueError(f"{c['id']}: part numbers not in the catalog: {sorted(unknown)}")
    missing = set(FAST_IDS) - set(ids)
    if missing or len(FAST_IDS) != FAST_COUNT:
        raise ValueError(f"FAST_IDS must name {FAST_COUNT} existing cases; missing {missing}")


def build_cases(
    catalog_path: Path = CATALOG_PATH, manual_path: Path = MANUAL_PATH
) -> list[dict[str, Any]]:
    catalog = load_catalog(catalog_path)
    cases = [*generated_cases(catalog), *load_manual(manual_path)]
    validate(cases, catalog)
    for c in cases:
        c["fast"] = c["id"] in FAST_IDS
    order = {name: i for i, name in enumerate(CATEGORIES)}
    return sorted(cases, key=lambda c: (order[c["category"]], c["id"]))


def render(cases: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases)


def main() -> None:
    cases = build_cases()
    CASES_PATH.write_text(render(cases), encoding="utf-8", newline="\n")
    fast = sum(c["fast"] for c in cases)
    print(f"Wrote {len(cases)} cases ({fast} fast) to {CASES_PATH.relative_to(EVAL_DIR.parent)}")


if __name__ == "__main__":
    main()
