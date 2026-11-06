"""The agent with scripted fake models against the real catalog: real tools run and are recorded,
tool failures become messages, limits and fallback work, and the session prompt carries the
active part numbers. Part numbers come from catalog.json, never typed by hand."""

from collections.abc import AsyncIterator

import pytest
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agent.context import AgentContext
from app.agent.factory import build_agent
from app.agent.fake import RuleBasedFakeModel, ScriptedFakeModel, calls, tool_call
from app.agent.middleware import INVALID_ARGS, TOOL_FAILED
from app.agent.validator import unsupported_numbers
from app.config import Settings
from app.db.engine import make_engine, make_sessionmaker
from app.schemas.catalog import GetActuatorResult, SearchResult
from ingest.load import read_catalog

pytestmark = pytest.mark.db

CATALOG = read_catalog()
WITH_DATA = [r.part_number for r in CATALOG if r.data_available]
PN, OTHER = WITH_DATA[0], WITH_DATA[1]
SETTINGS = Settings(_env_file=None, model_call_limit=4, tool_call_limit=4)


@pytest.fixture
async def maker(catalog_db_url: str) -> AsyncIterator[async_sessionmaker]:
    engine = make_engine(catalog_db_url)
    yield make_sessionmaker(engine)
    await engine.dispose()


async def _run(agent, maker, query: str, **ctx) -> tuple[list, AgentContext]:
    context = AgentContext(sessionmaker=maker, **ctx)
    out = await agent.ainvoke({"messages": [HumanMessage(query)]}, context=context)
    return out["messages"], context


async def test_tool_runs_against_db_and_is_recorded(maker) -> None:
    model = ScriptedFakeModel(
        steps=[calls(tool_call("get_actuator", part_number=PN)), AIMessage("done")]
    )
    messages, ctx = await _run(build_agent(SETTINGS, model), maker, "q")

    assert messages[-1].content == "done"
    (call,) = ctx.recorder.calls
    assert isinstance(call.result, GetActuatorResult)
    assert call.result.status == "found" and call.result.actuator.part_number == PN
    assert call.record.tool_name == "get_actuator"
    assert call.record.args == {"part_number": PN}
    assert call.record.result["actuator"]["part_number"] == PN
    assert call.record.latency_ms >= 0 and call.record.error is None
    tool_msg = next(m for m in messages if isinstance(m, ToolMessage))
    assert PN in tool_msg.content  # the model sees the JSON result


async def test_tool_exception_becomes_message_and_is_traced(maker, monkeypatch) -> None:
    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.tools.catalog_tools.get_actuator", boom)
    model = ScriptedFakeModel(
        steps=[calls(tool_call("get_actuator", part_number=PN)), AIMessage("sorry")]
    )
    messages, ctx = await _run(build_agent(SETTINGS, model), maker, "q")

    tool_msg = next(m for m in messages if isinstance(m, ToolMessage))
    assert tool_msg.content == TOOL_FAILED and tool_msg.status == "error"
    assert "db down" not in tool_msg.content
    (call,) = ctx.recorder.calls
    assert call.result is None and call.record.error == "RuntimeError: db down"


async def test_invalid_arguments_are_recorded_as_error(maker) -> None:
    bad = tool_call("search_actuators", min_torque=100)  # torque without its unit
    model = ScriptedFakeModel(steps=[calls(bad), AIMessage("need unit")])
    messages, ctx = await _run(build_agent(SETTINGS, model), maker, "q")

    assert messages[-1].content == "need unit"
    (call,) = ctx.recorder.calls
    assert call.result is None and call.record.error.startswith("Error invoking tool")
    tool_msg = next(m for m in messages if isinstance(m, ToolMessage))
    assert tool_msg.content == INVALID_ARGS  # the model never sees the raw validation text


async def test_search_result_is_typed(maker) -> None:
    model = ScriptedFakeModel(
        steps=[calls(tool_call("search_actuators", enclosure="weatherproof")), AIMessage("ok")]
    )
    _, ctx = await _run(build_agent(SETTINGS, model), maker, "q")
    (call,) = ctx.recorder.calls
    assert isinstance(call.result, SearchResult)
    # full specs travel next to the result, for the product cards only; one per part number
    shown = list(dict.fromkeys(m.part_number for m in call.result.matches))
    assert [s.part_number for s in call.specs] == shown


async def test_model_call_limit_raises(maker) -> None:
    loop = [calls(tool_call("list_catalog_options", field="enclosure")) for _ in range(10)]
    agent = build_agent(SETTINGS, ScriptedFakeModel(steps=loop))
    with pytest.raises(ModelCallLimitExceededError):
        await _run(agent, maker, "q")


async def test_tool_call_limit_raises(maker) -> None:
    many = calls(*(tool_call("list_catalog_options") for _ in range(SETTINGS.tool_call_limit + 1)))
    agent = build_agent(SETTINGS, ScriptedFakeModel(steps=[many, AIMessage("x")]))
    with pytest.raises(ToolCallLimitExceededError):
        await _run(agent, maker, "q")


async def test_rule_based_fake_admits_tool_failure(maker, monkeypatch) -> None:
    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.tools.catalog_tools.get_actuator", boom)
    messages, _ = await _run(build_agent(SETTINGS, RuleBasedFakeModel()), maker, PN)
    assert messages[-1].content == "No pude consultar el catálogo en este momento."


async def test_fallback_model_answers_when_primary_fails(maker) -> None:
    primary = ScriptedFakeModel(steps=[ConnectionError("provider down")])
    fallback = ScriptedFakeModel(steps=[AIMessage("from fallback")], model_name="fake-fallback")
    messages, _ = await _run(build_agent(SETTINGS, primary, fallback), maker, "q")
    assert messages[-1].content == "from fallback"
    assert messages[-1].response_metadata["model_name"] == "fake-fallback"


async def test_session_prompt_lists_active_parts(maker) -> None:
    seen: list[str] = []

    def capture(messages):
        seen.append(next(str(m.content) for m in messages if isinstance(m, SystemMessage)))
        return AIMessage("ok")

    model = ScriptedFakeModel(steps=[capture])
    await _run(build_agent(SETTINGS, model), maker, "q", active_parts=[PN], shown_parts=[OTHER, PN])
    assert f"Active part numbers: {PN}" in seen[0]
    assert f"Shown earlier in this conversation: {OTHER}" in seen[0]
    assert "Series 76 technical assistant" in seen[0]


async def test_rule_based_fake_looks_up_part_numbers_in_the_query(maker) -> None:
    agent = build_agent(SETTINGS, RuleBasedFakeModel())
    messages, ctx = await _run(agent, maker, f"datos del {PN.lower()}")
    assert [c.record.args for c in ctx.recorder.calls] == [{"part_number": PN}]
    assert messages[-1].content
    assert unsupported_numbers(messages[-1].content, []) == []  # the fake never writes specs


async def test_rule_based_fake_resolves_follow_up_from_active_parts(maker) -> None:
    agent = build_agent(SETTINGS, RuleBasedFakeModel())
    _, ctx = await _run(agent, maker, "¿y cuánta potencia consume?", active_parts=[PN])
    assert [c.record.args for c in ctx.recorder.calls] == [{"part_number": PN}]


def test_model_facing_schemas_hide_the_injected_runtime() -> None:
    from app.agent.tools import TOOLS

    for t in TOOLS:
        assert "runtime" not in t.tool_call_schema.model_json_schema().get("properties", {})
    search = next(t for t in TOOLS if t.name == "search_actuators")
    assert "min_torque" in search.tool_call_schema.model_json_schema()["properties"]
