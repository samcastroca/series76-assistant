"""Eval scoring and the runner's guards, on synthetic responses and traces. No LLM, no API
(the HTTP layer is an httpx.MockTransport). Catalog values come from catalog.json."""

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from eval import generate_cases as gen
from eval import metrics, run_eval

CATALOG = gen.load_catalog()
ROW = next(a for a in CATALOG if a["data_available"] and a["service_ratings"])
PN = ROW["part_number"]
TORQUE = ROW["service_ratings"][0]["torque_inlb"]


def expected(**overrides):
    return {**gen.turn(["get_actuator"], "detail", [PN], [gen.lookup(PN)]), **overrides}


def call(name="get_actuator", **args):
    return {"tool_name": name, "args": args, "result": {"torque_inlb": TORQUE}}


def response(answer="", response_type="detail", parts=(PN,), warnings=()):
    return {
        "answer": answer,
        "response_type": response_type,
        "products": [{"part_number": p} for p in parts],
        "warnings": list(warnings),
    }


# --- Per-turn metrics --------------------------------------------------------------------------


def test_tool_selection_needs_every_expected_call() -> None:
    two = expected(tools=["get_actuator", "get_actuator"])
    assert metrics.tool_selection(two, [call(part_number=PN)]) is False
    assert metrics.tool_selection(two, [call(part_number=PN), call(part_number="x")]) is True
    assert metrics.tool_selection(expected(), [call("search_actuators")]) is False


def test_tool_selection_empty_means_no_tool_and_none_means_unchecked() -> None:
    assert metrics.tool_selection(expected(tools=[]), []) is True
    assert metrics.tool_selection(expected(tools=[]), [call()]) is False
    assert metrics.tool_selection(expected(tools=None), [call()]) is None


def test_argument_accuracy_normalizes_part_numbers() -> None:
    loose = PN.lower().replace("-", " ").replace("/", " ")
    assert metrics.argument_accuracy(expected(), [call(part_number=loose)]) is True
    assert metrics.argument_accuracy(expected(), [call(part_number="761Q")]) is False


def test_argument_accuracy_compares_numbers_by_value() -> None:
    exp = expected(tool_args=[{"tool": "search_actuators", "args": {"min_torque": TORQUE}}])
    assert metrics.argument_accuracy(exp, [call("search_actuators", min_torque=f"{TORQUE}.00")])
    assert not metrics.argument_accuracy(exp, [call("search_actuators", min_torque=TORQUE - 1)])
    assert not metrics.argument_accuracy(exp, [call("search_actuators")])


def test_argument_accuracy_needs_a_distinct_call_per_expected_spec() -> None:
    exp = expected(tool_args=[gen.lookup(PN), gen.lookup(PN)])
    assert metrics.argument_accuracy(exp, [call(part_number=PN)]) is False


def test_expected_part_numbers_is_order_insensitive_unless_ordered() -> None:
    exp = expected(part_numbers=["A", "B"])
    assert metrics.expected_part_numbers(exp, response(parts=("B", "A"))) is True
    assert (
        metrics.expected_part_numbers({**exp, "ordered": True}, response(parts=("B", "A"))) is False
    )
    assert metrics.expected_part_numbers(exp, response(parts=("A",))) is False
    assert metrics.expected_part_numbers(expected(part_numbers=None), response()) is None


def test_answer_format_rejects_markdown_tables() -> None:
    table = f"Datos del {PN}:\n\n| Servicio | Par |\n|---|---|\n| On/off | x |\n"
    aligned = f"| a | b |\n| :--- | ---: |\n| {PN} | x |"
    assert metrics.answer_format(response(answer=table)) is False
    assert metrics.answer_format(response(answer=aligned)) is False


def test_answer_format_accepts_prose_lists_and_rules() -> None:
    prose = f"El **{PN}** cumple.\n\n- Weatherproof\n- On/off\n\n---\n\nA | B no es tabla."
    assert metrics.answer_format(response(answer=prose)) is True
    assert metrics.score_turn(expected(), response(answer=prose), [], [])["answer_format"] is True


def test_numeric_faithfulness_uses_session_tool_results() -> None:
    results = [{"torque_inlb": TORQUE}]
    ok = response(answer=f"It gives {TORQUE} in-lb at 60 Hz.")
    invented = response(answer=f"It gives {TORQUE + 1} in-lb.")
    assert metrics.numeric_faithfulness(expected(), ok, results) is True
    assert metrics.numeric_faithfulness(expected(), invented, results) is False


def test_numeric_faithfulness_fails_when_the_validator_stripped_numbers() -> None:
    stripped = response(answer="It gives — in-lb.", warnings=["numbers_removed"])
    assert metrics.numeric_faithfulness(expected(), stripped, []) is False


def test_must_not_contain_specs_rejects_numbers_with_spec_units() -> None:
    exp = expected(must_not_contain_specs=True)
    results = [{"torque_inlb": TORQUE, "speed_s": 17, "fla_a": "1.2"}]
    for spec in (f"{TORQUE} in-lb", f"{TORQUE} in·lb", "17 s", "1,2 A"):
        assert not metrics.numeric_faithfulness(exp, response(answer=f"It has {spec}."), results)


def test_must_not_contain_specs_allows_identifying_numbers() -> None:
    """Regression (paid run 2026-11-07): not_found / no_data answers that list suggestions
    or the row's supply ("220 V, 3-phase", "Series size: 2") are not leaking specs."""
    supply = ROW["voltage_v"]
    exp = expected(must_not_contain_specs=True)
    results = [{"voltage_v": supply, "phase": 3, "series_size": 2}]
    answer = f"Not published. Supply: {supply} V, 3-phase, 60 Hz. Series size: 2."
    assert metrics.numeric_faithfulness(exp, response(answer=answer), results)


def test_alternatives_accept_another_whole_outcome() -> None:
    other = "OTHER"
    exp = expected(alternatives=[{"response_type": "comparison", "part_numbers": [PN, other]}])
    scores = metrics.score_turn(
        exp, response(response_type="comparison", parts=(other, PN)), [call(part_number=PN)], []
    )
    assert scores["response_type"] and scores["expected_part_numbers"]


def test_alternatives_must_match_type_and_parts_together() -> None:
    exp = expected(alternatives=[{"response_type": "comparison", "part_numbers": [PN, "OTHER"]}])
    mixed = response(response_type="comparison", parts=(PN,))  # alt type, primary parts
    scores = metrics.score_turn(exp, mixed, [call(part_number=PN)], [])
    assert not metrics.passed(scores)


def test_coreference_only_applies_to_flagged_turns() -> None:
    calls = [call(part_number=PN)]
    plain = metrics.score_turn(expected(), response(), calls, [])
    assert plain["coreference"] is None and metrics.passed(plain)
    flagged = metrics.score_turn(expected(coreference=True), response(), calls, [])
    assert flagged["coreference"] is True
    wrong = metrics.score_turn(expected(coreference=True), response(), [call(part_number="x")], [])
    assert wrong["coreference"] is False and not metrics.passed(wrong)


def test_failed_turn_fails_every_applicable_metric() -> None:
    scores = metrics.failed_turn(expected(part_numbers=None))
    assert scores["expected_part_numbers"] is None and scores["coreference"] is None
    assert scores["response_type"] is False and not metrics.passed(scores)


# --- Aggregates --------------------------------------------------------------------------------


def test_percentile_nearest_rank() -> None:
    assert metrics.percentile(list(range(1, 21)), 95) == 19
    assert metrics.percentile([5], 95) == 5
    assert metrics.percentile([], 95) is None


def test_cost_is_none_when_not_reported_never_zero() -> None:
    assert metrics.turn_cost([]) is None
    assert metrics.turn_cost([{"cost_usd": "0.001"}, {"cost_usd": None}]) is None
    assert metrics.turn_cost([{"cost_usd": "0.001"}, {"cost_usd": "0.002"}]) == Decimal("0.003")


def test_summary_rates_skip_not_applicable_metrics() -> None:
    ok = metrics.score_turn(expected(), response(), [call(part_number=PN)], [])
    bad = metrics.score_turn(expected(), response(response_type="error"), [], [])
    cases = [
        {
            "category": "detail",
            "passed": True,
            "turns": [{"scores": ok, "latency_ms": 100, "usage": []}],
        },
        {
            "category": "search",
            "passed": False,
            "turns": [{"scores": bad, "latency_ms": 300, "usage": []}],
        },
    ]
    summary = metrics.summarize(cases)
    assert summary["pass_rate"] == 0.5
    assert summary["metrics"]["response_type"] == 0.5
    assert summary["metrics"]["coreference"] is None
    assert summary["cost_usd_per_turn"] is None and summary["latency_ms_p95"] == 300
    assert set(metrics.by_category(cases)) == {"detail", "search"}


# --- Runner ------------------------------------------------------------------------------------


def test_paid_guard() -> None:
    assert run_eval.paid_guard("openrouter", "fake", confirmed=False)
    assert run_eval.paid_guard(None, "openrouter", confirmed=False)
    assert run_eval.paid_guard("fake", "openrouter", confirmed=True) is None
    assert run_eval.paid_guard("google", "fake", confirmed=False) is None
    assert run_eval.paid_guard("fake", None, confirmed=False)  # unknown server: maybe paid
    assert run_eval.paid_guard("fake", None, confirmed=True) is None


def _transport(provider: str, seen: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"llm_provider": provider, "agent_model": "m"})
        return httpx.Response(500, json={"error": {"code": "unexpected", "message": "no"}})

    return httpx.MockTransport(handler)


def test_runner_refuses_a_paid_server_without_confirmation(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    seen: list[str] = []
    assert run_eval.main(["--fast"], transport=_transport("openrouter", seen)) == 2
    assert seen == ["/health"]  # no conversation call was made


def test_runner_refuses_a_paid_local_provider_without_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    seen: list[str] = []
    assert run_eval.main(["--fast"], transport=_transport("fake", seen)) == 2
    assert seen == ["/health"]


def test_runner_rejects_unknown_case_ids(monkeypatch) -> None:
    """Regression: a typo in --ids silently ran fewer cases."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    seen: list[str] = []
    argv = ["--ids", "out-of-scope-capital", "no-such-case"]
    assert run_eval.main(argv, transport=_transport("fake", seen)) == 1
    assert seen == ["/health"]  # stopped before any conversation call


def _login_transport(login: httpx.Response, seen: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"llm_provider": "fake", "agent_model": None})
        if request.url.path == "/api/auth/login":
            return login
        return httpx.Response(500, json={"error": {"code": "unexpected", "message": "no"}})

    return httpx.MockTransport(handler)


def _login_ok(role: str) -> httpx.Response:
    user = {"email": "e@example.com", "role": role}
    return httpx.Response(200, json={"access_token": "t", "token_type": "bearer", "user": user})


@pytest.mark.parametrize(
    ("password", "login", "calls"),
    [
        (None, _login_ok("developer"), ["/health"]),  # no password: never tries to log in
        (
            "pw",
            httpx.Response(401, json={"error": {"code": "invalid_credentials", "message": "x"}}),
            ["/health", "/api/auth/login"],
        ),
        ("pw", _login_ok("engineer"), ["/health", "/api/auth/login"]),  # trace needs developer
    ],
)
def test_runner_requires_a_developer_login(monkeypatch, password, login, calls) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("EVAL_PASSWORD", raising=False)
    if password:
        monkeypatch.setenv("EVAL_PASSWORD", password)
    seen: list[str] = []
    assert run_eval.main(["--fast"], transport=_login_transport(login, seen)) == 1
    assert seen == calls  # stopped before any conversation call


def test_runner_logs_in_again_when_the_token_expires() -> None:
    logins: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            logins.append(json.loads(request.content)["email"])
            token = f"t{len(logins)}"
            user = {"email": "d@example.com", "role": "developer"}
            return httpx.Response(
                200, json={"access_token": token, "token_type": "bearer", "user": user}
            )
        if request.headers["Authorization"] == "Bearer t1":
            return httpx.Response(401, json={"error": {"code": "token_expired", "message": "x"}})
        return httpx.Response(200, json={"ok": True})

    auth = run_eval.DeveloperAuth("d@example.com", SecretStr("pw"))
    with httpx.Client(base_url="http://t", transport=httpx.MockTransport(handler), auth=auth) as c:
        r = c.get("/api/sessions")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert logins == ["d@example.com", "d@example.com"]


def test_run_case_scores_turns_and_cleans_up() -> None:
    case = {
        "id": "c",
        "category": "detail",
        "fast": True,
        "turns": [f"torque of {PN}?"],
        "expected": [expected()],
    }
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = {**response(answer=f"{TORQUE} in-lb"), "session_id": "s1", "message_id": "m1"}
            return httpx.Response(200, json=body)
        if request.method == "DELETE":
            deleted.append(request.url.path)
            return httpx.Response(204)
        trace = {"tool_calls": [call(part_number=PN)], "usage": [{"model": "m", "cost_usd": None}]}
        return httpx.Response(200, json=trace)

    with httpx.Client(base_url="http://t", transport=httpx.MockTransport(handler)) as client:
        result = run_eval.run_case(client, case)
    assert result["passed"] is True
    assert result["turns"][0]["internal_error"] is None
    assert deleted == ["/api/sessions/s1"]


def test_run_case_marks_turns_after_an_api_error_as_failed() -> None:
    case = {
        "id": "c",
        "category": "multi_turn",
        "fast": False,
        "turns": ["a", "b"],
        "expected": [expected(), expected(coreference=True)],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"code": "llm_unavailable", "message": "x"}})

    with httpx.Client(base_url="http://t", transport=httpx.MockTransport(handler)) as client:
        result = run_eval.run_case(client, case)
    assert result["passed"] is False
    assert [t["error"] for t in result["turns"]] == ["llm_unavailable", "skipped"]


def test_result_path_is_dated_and_never_overwrites(tmp_path) -> None:
    first = run_eval.result_path("google/gemini-x:free", date(2026, 11, 7), tmp_path)
    assert first.name == "2026-11-07-google-gemini-x-free.json"
    first.write_text(json.dumps({}))
    assert run_eval.result_path("google/gemini-x:free", date(2026, 11, 7), tmp_path).name.endswith(
        "-2.json"
    )


def test_format_table_has_a_row_per_category_and_a_total() -> None:
    ok = metrics.score_turn(expected(), response(), [call(part_number=PN)], [])
    cases = [
        {
            "category": "detail",
            "passed": True,
            "turns": [{"scores": ok, "latency_ms": 10, "usage": []}],
        }
    ]
    table = run_eval.format_table(metrics.summarize(cases), metrics.by_category(cases))
    assert "detail" in table and "TOTAL" in table and "n/a" in table
