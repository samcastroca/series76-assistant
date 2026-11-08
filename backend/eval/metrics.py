"""Scoring for one evaluated turn, and the aggregates over a run. Pure functions over the API
response and the debug trace; no network, no LLM.

A metric is True (passed), False (failed) or None (not applicable to this turn)."""

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from app.agent.validator import extract_numbers, unsupported_numbers
from app.tools.normalize import normalize_part_key

# Units of spec values (torque, stroke time, current, power, duty cycle). Voltages, frequencies
# and series sizes identify a product and may appear even when no specs are published.
_SPEC_UNIT = re.compile(
    r"^(?:in-lb|lb-in|in·lb|N·m|Nm|A|W|s|%|seg(?:undos)?|seconds?)$", re.IGNORECASE
)

METRICS = (
    "tool_selection",
    "argument_accuracy",
    "expected_part_numbers",
    "response_type",
    "numeric_faithfulness",
    "coreference",
)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        return Decimal(str(value)).normalize()
    except InvalidOperation:
        return None


def value_matches(key: str, expected: Any, actual: Any) -> bool:
    if actual is None:
        return expected is None
    if key == "part_number":
        return normalize_part_key(str(expected)) == normalize_part_key(str(actual))
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    exp_num, act_num = _decimal(expected), _decimal(actual)
    if exp_num is not None and act_num is not None:
        return exp_num == act_num
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.casefold() == actual.casefold()
    return expected == actual


def args_match(subset: dict[str, Any], args: dict[str, Any]) -> bool:
    return all(value_matches(k, v, args.get(k)) for k, v in subset.items())


def tool_selection(expected: dict[str, Any], calls: Sequence[dict[str, Any]]) -> bool | None:
    """Every expected tool was called (as many times as listed); [] means no tool at all."""
    wanted = expected.get("tools")
    if wanted is None:
        return None
    called = Counter(c["tool_name"] for c in calls)
    if not wanted:
        return not called
    return not (Counter(wanted) - called)


def argument_accuracy(expected: dict[str, Any], calls: Sequence[dict[str, Any]]) -> bool | None:
    """Each expected (tool, argument subset) is satisfied by a distinct call."""
    specs = expected.get("tool_args") or []
    if not specs:
        return None
    unused = list(calls)
    for spec in specs:
        hit = next(
            (
                c
                for c in unused
                if c["tool_name"] == spec["tool"] and args_match(spec["args"], c["args"])
            ),
            None,
        )
        if hit is None:
            return False
        unused.remove(hit)
    return True


def expected_part_numbers(expected: dict[str, Any], response: dict[str, Any]) -> bool | None:
    wanted = expected.get("part_numbers")
    if wanted is None:
        return None
    shown = [p["part_number"] for p in response.get("products", [])]
    if expected.get("ordered"):
        return shown == list(wanted)
    return sorted(set(shown)) == sorted(set(wanted))


def response_type(expected: dict[str, Any], response: dict[str, Any]) -> bool:
    return response.get("response_type") == expected["response_type"]


def outcome(expected: dict[str, Any], response: dict[str, Any]) -> tuple[bool | None, bool]:
    """(expected_part_numbers, response_type) against the expected outcome or, when it fails,
    any accepted alternative ({response_type, part_numbers, ordered?}) matched as a whole."""
    primary = (expected_part_numbers(expected, response), response_type(expected, response))
    if primary[0] is not False and primary[1]:
        return primary
    for alt in expected.get("alternatives") or []:
        candidate = (expected_part_numbers(alt, response), response_type(alt, response))
        if candidate[0] is not False and candidate[1]:
            return candidate
    return primary


def numeric_faithfulness(
    expected: dict[str, Any], response: dict[str, Any], session_results: Iterable[Any]
) -> bool:
    """Every number in the answer is backed by a tool result of the session (or the allowlist),
    and the backend's validator did not have to strip any (`numbers_removed`). With
    must_not_contain_specs (not found, no published data), no number may carry a spec unit."""
    answer = response.get("answer", "")
    if unsupported_numbers(answer, list(session_results)):
        return False
    if "numbers_removed" in response.get("warnings", []):
        return False
    if expected.get("must_not_contain_specs"):
        return not spec_values(answer)
    return True


def spec_values(answer: str) -> list[str]:
    """Numbers printed with a spec unit ("700 in-lb", "17 s", "1,2 A")."""
    out = []
    for t in extract_numbers(answer):
        unit = answer[t.start + len(t.text) : t.end].strip()
        if _SPEC_UNIT.match(unit):
            out.append(answer[t.start : t.end])
    return out


def score_turn(
    expected: dict[str, Any],
    response: dict[str, Any],
    calls: Sequence[dict[str, Any]],
    session_results: Iterable[Any],
) -> dict[str, bool | None]:
    selection = tool_selection(expected, calls)
    arguments = argument_accuracy(expected, calls)
    coreference = None
    if expected.get("coreference"):
        coreference = selection is not False and arguments is not False
    parts, kind = outcome(expected, response)
    return {
        "tool_selection": selection,
        "argument_accuracy": arguments,
        "expected_part_numbers": parts,
        "response_type": kind,
        "numeric_faithfulness": numeric_faithfulness(expected, response, session_results),
        "coreference": coreference,
    }


def failed_turn(expected: dict[str, Any]) -> dict[str, bool | None]:
    """Scores for a turn the API could not answer: every applicable metric fails."""
    return {
        "tool_selection": False if expected.get("tools") is not None else None,
        "argument_accuracy": False if expected.get("tool_args") else None,
        "expected_part_numbers": False if expected.get("part_numbers") is not None else None,
        "response_type": False,
        "numeric_faithfulness": False,
        "coreference": False if expected.get("coreference") else None,
    }


def passed(scores: dict[str, bool | None]) -> bool:
    return all(v is not False for v in scores.values())


# --- Aggregates --------------------------------------------------------------------------------


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def turn_cost(usage: Sequence[dict[str, Any]]) -> Decimal | None:
    """Sum of the turn's model-call costs; None when any call has no reported cost (or there
    were no model calls), never 0."""
    if not usage or any(u.get("cost_usd") is None for u in usage):
        return None
    return sum((Decimal(str(u["cost_usd"])) for u in usage), Decimal(0))


def turn_tokens(usage: Sequence[dict[str, Any]]) -> int | None:
    values = [
        (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
        for u in usage
        if u.get("input_tokens") is not None or u.get("output_tokens") is not None
    ]
    return sum(values) if values else None


def metric_rates(turns: Sequence[dict[str, Any]]) -> dict[str, float | None]:
    """Share of applicable turns that passed each metric."""
    out: dict[str, float | None] = {}
    for name in METRICS:
        applicable = [t["scores"][name] for t in turns if t["scores"][name] is not None]
        out[name] = sum(applicable) / len(applicable) if applicable else None
    return out


def summarize(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """`cases`: evaluated cases, each with `passed` and `turns` (scores, latency_ms, usage)."""
    turns = [t for c in cases for t in c["turns"]]
    latencies = [t["latency_ms"] for t in turns if t.get("latency_ms") is not None]
    costs = [c for t in turns if (c := turn_cost(t.get("usage", []))) is not None]
    tokens = [n for t in turns if (n := turn_tokens(t.get("usage", []))) is not None]
    return {
        "cases": len(cases),
        "cases_passed": sum(c["passed"] for c in cases),
        "pass_rate": sum(c["passed"] for c in cases) / len(cases) if cases else None,
        "turns": len(turns),
        "metrics": metric_rates(turns),
        "latency_ms_p50": percentile(latencies, 50),
        "latency_ms_p95": percentile(latencies, 95),
        "cost_usd_per_turn": str(sum(costs) / len(costs)) if costs else None,
        "cost_usd_total": str(sum(costs)) if costs else None,
        "turns_with_cost": len(costs),
        "tokens_per_turn": sum(tokens) / len(tokens) if tokens else None,
    }


def by_category(cases: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    categories = list(dict.fromkeys(c["category"] for c in cases))
    return {cat: summarize([c for c in cases if c["category"] == cat]) for cat in categories}
