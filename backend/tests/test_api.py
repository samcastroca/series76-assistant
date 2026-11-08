"""API tests with LLM_PROVIDER=fake (a scripted LangChain model) against Postgres: the response
contract, multi-turn follow-up, persistence across an app restart, sessions, the trace and the
single error shape. Requests run as engineer1 unless a test says otherwise; authentication and
ownership have their own module (test_auth_api.py). Catalog values come from catalog.json."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.fake import calls, tool_call
from ingest.load import read_catalog
from tests._api import INTERNALS, assert_error

pytestmark = pytest.mark.db

CATALOG = read_catalog()
ROW = next(r for r in CATALOG if r.data_available and r.motor_power_w is not None)
PN, POWER = ROW.part_number, ROW.motor_power_w

RESPONSE_KEYS = {
    "session_id",
    "message_id",
    "answer",
    "response_type",
    "products",
    "highlight_fields",
    "clarification",
    "suggestions",
    "warnings",
}
PRODUCT_KEYS = {
    "part_number",
    "series_size",
    "variant",
    "enclosure",
    "power_supply",
    "certifications",
    "csa_certified",
    "hazardous_locations",
    "data_available",
    "service",
    "electrical",
    "motor_power_w",
    "notes",
    "source",
    "matched_service",
    "meets_requirements",
    "unmet",
}


def lookup(pn: str = PN) -> AIMessage:
    return calls(tool_call("get_actuator", part_number=pn))


# --- Contract ---------------------------------------------------------------------------------


async def test_conversation_contract(client) -> None:
    async with client([lookup(), AIMessage(f"El {PN} consume {POWER} W.")]) as c:
        r = await c.post("/api/conversation", json={"query": f"datos del {PN}"})

    assert r.status_code == 200
    body = r.json()
    assert set(body) == RESPONSE_KEYS
    assert body["response_type"] == "detail"
    assert body["answer"] == f"El {PN} consume {POWER} W."
    (product,) = body["products"]
    assert set(product) == PRODUCT_KEYS
    assert product["part_number"] == PN and product["motor_power_w"] == POWER
    assert set(product["power_supply"]) == {"voltage_v", "phase", "current_type"}
    assert set(product["source"]) == {"table", "page"}
    assert all(set(s["torque"]) == {"in_lb", "nm"} for s in product["service"])
    assert body["clarification"] is None


async def test_answer_never_carries_internals(client) -> None:
    script = [lookup(), AIMessage("Llamé a get_actuator."), AIMessage("Llamé a get_actuator.")]
    async with client(script) as c:
        r = await c.post("/api/conversation", json={"query": "q"})
    assert not any(word in r.json()["answer"] for word in INTERNALS)


# --- Multi-turn and restart -------------------------------------------------------------------


def follow_up(messages):
    """The model resolves "it" from the session: the previous turn is in history and the
    session prompt names the active part number."""
    assert any(isinstance(m, HumanMessage) and PN in str(m.content) for m in messages)
    system = next(str(m.content) for m in messages if isinstance(m, SystemMessage))
    active = system.split("Active part numbers: ")[1].splitlines()[0]
    return lookup(active)


async def test_follow_up_resolves_the_previous_part_number(client) -> None:
    script = [lookup(), AIMessage("Listo."), follow_up, AIMessage(f"Consume {POWER} W.")]
    async with client(script) as c:
        first = (await c.post("/api/conversation", json={"query": f"datos del {PN}"})).json()
        second = (
            await c.post(
                "/api/conversation",
                json={"session_id": first["session_id"], "query": "¿y cuánta potencia consume?"},
            )
        ).json()

    assert second["session_id"] == first["session_id"]
    assert second["response_type"] == "detail"
    assert [p["part_number"] for p in second["products"]] == [PN]
    assert second["products"][0]["motor_power_w"] == POWER
    assert second["answer"] == f"Consume {POWER} W."


async def test_session_survives_an_app_restart(client) -> None:
    async with client([lookup(), AIMessage("Listo.")]) as c:
        first = (await c.post("/api/conversation", json={"query": f"datos del {PN}"})).json()

    # A brand-new app: new engine, agent and service; nothing carried over in memory.
    async with client([follow_up, AIMessage(f"Consume {POWER} W.")]) as c:
        second = await c.post(
            "/api/conversation",
            json={"session_id": first["session_id"], "query": "¿y cuánta potencia consume?"},
        )
        messages = (await c.get(f"/api/sessions/{first['session_id']}/messages")).json()

    assert second.status_code == 200
    assert [p["part_number"] for p in second.json()["products"]] == [PN]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[1]["products"][0]["part_number"] == PN  # cards rebuild from history


# --- Sessions, messages, trace ----------------------------------------------------------------


async def test_sessions_list_messages_and_delete(client) -> None:
    async with client([lookup(), AIMessage("Listo.")]) as c:
        sid = (await c.post("/api/conversation", json={"query": f"datos del {PN}"})).json()[
            "session_id"
        ]
        sessions = (await c.get("/api/sessions")).json()
        messages = (await c.get(f"/api/sessions/{sid}/messages")).json()
        deleted = await c.delete(f"/api/sessions/{sid}")
        after = await c.get(f"/api/sessions/{sid}/messages")
        again = await c.delete(f"/api/sessions/{sid}")

    (summary,) = sessions
    assert summary["id"] == sid and summary["title"] == PN and summary["message_count"] == 2
    assert set(summary) == {"id", "title", "created_at", "updated_at", "message_count"}
    assert messages[0]["role"] == "user" and messages[0]["products"] == []
    assert messages[1]["response_type"] == "detail" and messages[1]["status"] == "ok"
    assert deleted.status_code == 204
    assert_error(after, 404, "session_not_found")
    assert_error(again, 404, "session_not_found")


async def test_trace_shows_tool_calls_and_usage_to_a_developer(client) -> None:
    async with client([lookup(), AIMessage("Listo.")], as_user="developer") as c:
        mid = (await c.post("/api/conversation", json={"query": "q"})).json()["message_id"]
        trace = (await c.get(f"/api/messages/{mid}/trace")).json()

    (call,) = trace["tool_calls"]
    assert call["tool_name"] == "get_actuator" and call["args"] == {"part_number": PN}
    assert call["result"]["status"] == "found" and isinstance(call["latency_ms"], int)
    assert [u["model"] for u in trace["usage"]] == ["fake-scripted", "fake-scripted"]
    assert all(u["input_tokens"] > 0 for u in trace["usage"])
    assert trace["message_id"] == mid


async def test_trace_is_forbidden_to_an_engineer(client) -> None:
    async with client([lookup(), AIMessage("Listo.")]) as c:
        mid = (await c.post("/api/conversation", json={"query": "q"})).json()["message_id"]
        r = await c.get(f"/api/messages/{mid}/trace")
    assert_error(r, 403, "forbidden")


async def test_call_limit_error_detail_stays_out_of_messages(client) -> None:
    loop = [calls(tool_call("list_catalog_options", field="enclosure")) for _ in range(10)]
    async with client(loop, as_user="developer") as c:
        body = (await c.post("/api/conversation", json={"query": "q"})).json()
        messages = (await c.get(f"/api/sessions/{body['session_id']}/messages")).json()
        trace = (await c.get(f"/api/messages/{body['message_id']}/trace")).json()

    assert body["response_type"] == "error"
    assert "internal_error" not in messages[1]
    assert "Limit" in trace["payload"]["internal_error"]


async def test_health_reports_database_and_provider(client) -> None:
    async with client([]) as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "database": "ok",
        "llm_provider": "fake",
        "agent_model": None,
        "fallback_model": None,
    }


# --- Error format -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"query": ""}, "empty_query"),
        ({"query": "   "}, "empty_query"),
        ({"query": "x" * 1001}, "query_too_long"),
        ({"query": "x" * 20_001}, "invalid_request"),  # parsing cap
        ({}, "invalid_request"),
        ({"query": "q", "session_id": "not-a-uuid"}, "invalid_request"),
    ],
)
async def test_bad_requests_return_400(client, body, code) -> None:
    async with client([]) as c:
        assert_error(await c.post("/api/conversation", json=body), 400, code)


async def test_unknown_session_returns_404(client) -> None:
    async with client([]) as c:
        sid = "00000000-0000-4000-8000-000000000000"
        assert_error(
            await c.post("/api/conversation", json={"session_id": sid, "query": "q"}),
            404,
            "session_not_found",
        )
        assert_error(await c.get(f"/api/sessions/{sid}/messages"), 404, "session_not_found")


async def test_spent_daily_budget_returns_503_budget_exceeded(client) -> None:
    async with client([lookup(), AIMessage("Listo.")], daily_budget_usd=0) as c:
        r = await c.post("/api/conversation", json={"query": "hola"})
        sessions = (await c.get("/api/sessions")).json()
    assert_error(r, 503, "budget_exceeded")
    assert sessions == []


async def test_llm_failure_returns_503_and_keeps_the_user_message(client) -> None:
    script = [lookup(), AIMessage("Listo."), ConnectionError("provider down: secret detail")]
    async with client(script) as c:
        sid = (await c.post("/api/conversation", json={"query": "q"})).json()["session_id"]
        r = await c.post("/api/conversation", json={"session_id": sid, "query": "otra"})
        listed = await c.get(f"/api/sessions/{sid}/messages")

    assert_error(r, 503, "llm_unavailable")
    assert "secret detail" not in r.text
    messages = listed.json()
    assert (messages[-1]["role"], messages[-1]["status"]) == ("user", "error")
    assert "secret detail" not in listed.text and "internal_error" not in messages[-1]


async def test_llm_failure_on_a_new_conversation_leaves_no_session(client) -> None:
    async with client([ConnectionError("provider down")]) as c:
        r = await c.post("/api/conversation", json={"query": "hola"})
        sessions = (await c.get("/api/sessions")).json()
    assert_error(r, 503, "llm_unavailable")
    assert sessions == []


async def test_query_at_the_limit_is_accepted_after_strip(client) -> None:
    async with client([AIMessage("ok"), AIMessage("ok")]) as c:
        exact = await c.post("/api/conversation", json={"query": "x" * 1000})
        padded = await c.post("/api/conversation", json={"query": "  " + "x" * 1000 + "  "})
    assert exact.status_code == 200 and padded.status_code == 200


async def test_malformed_requests_use_the_shape(client) -> None:
    async with client([], as_user="developer") as c:
        malformed = await c.post(
            "/api/conversation",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert_error(malformed, 400, "invalid_request")
        assert malformed.json()["error"]["message"] == "Invalid request: body."
        assert_error(await c.put("/api/sessions"), 405, "method_not_allowed")
        assert_error(await c.get("/api/sessions/not-a-uuid/messages"), 400, "invalid_request")
        unknown = "00000000-0000-4000-8000-000000000000"
        assert_error(await c.get(f"/api/messages/{unknown}/trace"), 404, "message_not_found")


async def test_trace_route_is_documented_and_requires_a_token(client) -> None:
    async with client([], as_user=None) as c:
        paths = (await c.get("/openapi.json")).json()["paths"]
        unknown = "00000000-0000-4000-8000-000000000000"
        r = await c.get(f"/api/messages/{unknown}/trace")
    assert "/api/messages/{message_id}/trace" in paths
    assert_error(r, 401, "not_authenticated")


async def test_unknown_route_and_unexpected_errors_use_the_shape(client, monkeypatch) -> None:
    async def boom(*args, **kwargs):
        raise RuntimeError("db exploded")

    async with client([]) as c:
        assert_error(await c.get("/api/nope"), 404, "not_found")
        monkeypatch.setattr("app.db.conversations.list_sessions", boom)
        r = await c.get("/api/sessions")
    assert_error(r, 500, "internal_error")
    assert "db exploded" not in r.text
