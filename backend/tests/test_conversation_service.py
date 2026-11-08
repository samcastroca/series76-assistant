"""ConversationService with scripted fake models against Postgres: one-transaction persistence,
provider failure, the numeric validator's retry and strip, call limits, multi-turn context and
session ownership.
Catalog values in answers are taken from catalog.json."""

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy import func, select, update

from app.agent.factory import build_agent
from app.agent.fake import ScriptedFakeModel, calls, tool_call
from app.config import Settings
from app.db import conversations as repo
from app.db.engine import make_engine, make_sessionmaker
from app.db.models import LlmUsage, Message, ToolCall
from app.schemas.trace import UsageRecord
from app.services.conversation import (
    FALLBACK_ANSWER,
    BudgetExceeded,
    ConversationService,
    LlmUnavailable,
    SessionNotFound,
)
from ingest.load import read_catalog

pytestmark = pytest.mark.db

CATALOG = read_catalog()
ROW = next(r for r in CATALOG if r.data_available and r.motor_power_w is not None)
PN, POWER = ROW.part_number, ROW.motor_power_w
UNBACKED = 987_654  # invented; not a catalog value
SETTINGS = Settings(_env_file=None, model_call_limit=4, tool_call_limit=4)

# The turn's user; inserted by the fixture (the hash is never verified here).
USER = uuid.UUID("00000000-0000-0000-0000-0000000000e1")
OTHER = uuid.UUID("00000000-0000-0000-0000-0000000000e2")

ServiceFactory = Callable[[list], ConversationService]


@pytest.fixture
async def make_service(catalog_db_url: str) -> AsyncIterator[ServiceFactory]:
    engine = make_engine(catalog_db_url)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("TRUNCATE users, sessions CASCADE")
        await conn.exec_driver_sql(
            "INSERT INTO users (id, email, password_hash, role) VALUES "
            f"('{USER}', 'user@example.com', 'x', 'engineer'), "
            f"('{OTHER}', 'other@example.com', 'x', 'engineer')"
        )
    maker = make_sessionmaker(engine)

    def factory(steps: list) -> ConversationService:
        return ConversationService(
            build_agent(SETTINGS, ScriptedFakeModel(steps=steps)), maker, SETTINGS
        )

    factory.maker = maker  # type: ignore[attr-defined]
    yield factory
    await engine.dispose()


def lookup(pn: str = PN) -> AIMessage:
    return calls(tool_call("get_actuator", part_number=pn))


async def _count(maker, model, **where) -> int:
    async with maker() as db:
        stmt = select(func.count()).select_from(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return await db.scalar(stmt)


async def test_turn_is_persisted_with_trace_and_usage(make_service) -> None:
    service = make_service([lookup(), AIMessage(f"El {PN} consume {POWER} W.")])
    response = await service.handle(USER, None, f"datos del {PN}")

    assert response.response_type == "detail"
    assert response.answer == f"El {PN} consume {POWER} W."
    assert [p.part_number for p in response.products] == [PN]
    assert response.warnings == []

    async with make_service.maker() as db:
        messages = await repo.list_messages(db, response.session_id)
        assert [(m.role, m.status) for m in messages] == [("user", "ok"), ("assistant", "ok")]
        assert messages[1].id == response.message_id
        assert messages[1].payload["products"][0]["part_number"] == PN
        trace = await repo.get_trace(db, response.message_id)
        session = await repo.get_session(db, response.session_id, USER)
    (call,) = trace.tool_calls
    assert call.tool_name == "get_actuator" and call.args == {"part_number": PN}
    assert call.result["actuator"]["motor_power_w"] == POWER
    assert call.latency_ms >= 0
    assert len(trace.usage) == 2  # one per model call
    assert all(u.model == "fake-scripted" and u.input_tokens for u in trace.usage)
    assert session.title == PN
    assert session.state["active_parts"] == [PN]


async def test_provider_failure_on_a_new_session_stores_nothing(make_service) -> None:
    service = make_service([ConnectionError("provider down")])
    with pytest.raises(LlmUnavailable):
        await service.handle(USER, None, "hola")

    maker = make_service.maker  # the 503 cannot return a session id, so no orphan session
    assert await _count(maker, Message) == 0
    assert await _count(maker, ToolCall) == 0 and await _count(maker, LlmUsage) == 0


async def test_provider_failure_keeps_the_user_message_and_its_trace(make_service) -> None:
    service = make_service([lookup(), AIMessage("Listo."), lookup(), ConnectionError("down")])
    first = await service.handle(USER, None, "q")
    with pytest.raises(LlmUnavailable):
        await service.handle(USER, first.session_id, "otra")

    maker = make_service.maker
    async with maker() as db:
        messages = await repo.list_messages(db, first.session_id)
        trace = await repo.get_trace(db, messages[-1].id)
    assert [(m.role, m.status) for m in messages] == [
        ("user", "ok"),
        ("assistant", "ok"),
        ("user", "error"),
    ]
    assert [c.tool_name for c in trace.tool_calls] == ["get_actuator"]
    assert len(trace.usage) == 1  # the model call that succeeded before the failure
    assert "ConnectionError" in messages[-1].payload["internal_error"]


async def test_retry_after_failure_reuses_the_failed_user_message(make_service) -> None:
    script = [lookup(), AIMessage("Listo."), lookup(), ConnectionError("down")]
    service = make_service([*script, lookup(), AIMessage("Ahora sí.")])
    first = await service.handle(USER, None, "q")
    with pytest.raises(LlmUnavailable):
        await service.handle(USER, first.session_id, "otra")
    retried = await service.handle(USER, first.session_id, "otra")

    async with make_service.maker() as db:
        messages = await repo.list_messages(db, first.session_id)
        failed_trace = await repo.get_trace(db, messages[2].id)
    assert [(m.role, m.status, m.content) for m in messages] == [
        ("user", "ok", "q"),
        ("assistant", "ok", "Listo."),
        ("user", "ok", "otra"),
        ("assistant", "ok", "Ahora sí."),
    ]
    assert messages[3].id == retried.message_id
    assert "internal_error" not in messages[2].payload
    assert len(failed_trace.tool_calls) == 1  # the failed attempt's cost is still recorded


async def test_a_different_query_after_failure_keeps_the_failed_message(make_service) -> None:
    script = [lookup(), AIMessage("Listo."), ConnectionError("down"), AIMessage("Otra cosa.")]
    service = make_service(script)
    first = await service.handle(USER, None, "q")
    with pytest.raises(LlmUnavailable):
        await service.handle(USER, first.session_id, "otra")
    await service.handle(USER, first.session_id, "distinta")

    async with make_service.maker() as db:
        messages = await repo.list_messages(db, first.session_id)
    assert [(m.role, m.status) for m in messages][2:] == [
        ("user", "error"),
        ("user", "ok"),
        ("assistant", "ok"),
    ]


async def test_unbacked_number_triggers_one_retry_with_a_note(make_service) -> None:
    notes: list[str] = []

    def corrected(messages):
        notes.append(str(messages[-1].content))
        return AIMessage(f"Consume {POWER} W.")

    service = make_service([lookup(), AIMessage(f"Consume {UNBACKED} W."), corrected])
    response = await service.handle(USER, None, "q")

    assert response.answer == f"Consume {POWER} W."
    assert response.warnings == []
    assert str(UNBACKED) in notes[0].replace(",", "")
    async with make_service.maker() as db:  # the retry note is never stored
        contents = [m.content for m in await repo.list_messages(db, response.session_id)]
    assert contents == ["q", f"Consume {POWER} W."]


async def test_numbers_are_stripped_after_a_failed_retry(make_service) -> None:
    bad = AIMessage(f"Consume {UNBACKED} W y {POWER} W.")
    service = make_service([lookup(), bad, bad])
    response = await service.handle(USER, None, "q")

    assert str(UNBACKED) not in response.answer.replace(",", "")
    assert str(POWER) in response.answer  # backed numbers stay
    assert "numbers_removed" in response.warnings
    assert [p.part_number for p in response.products] == [PN]  # cards are unaffected


async def test_tool_names_in_the_answer_are_retried(make_service) -> None:
    service = make_service([lookup(), AIMessage("Usé get_actuator."), AIMessage("Aquí está.")])
    response = await service.handle(USER, None, "q")
    assert response.answer == "Aquí está."


async def test_call_limit_returns_a_controlled_error(make_service) -> None:
    loop = [calls(tool_call("list_catalog_options", field="enclosure")) for _ in range(10)]
    service = make_service(loop)
    response = await service.handle(USER, None, "q")

    assert response.response_type == "error"
    assert response.answer == FALLBACK_ANSWER
    assert response.products == []
    async with make_service.maker() as db:
        trace = await repo.get_trace(db, response.message_id)
    assert "ModelCallLimitExceededError" in trace.message.payload["internal_error"]
    assert trace.tool_calls  # the calls made before the limit are still traced
    assert len(trace.usage) == SETTINGS.model_call_limit  # and so is the usage they cost


async def test_follow_up_resolves_the_active_part_number(make_service) -> None:
    def follow_up(messages):
        # The previous turn's text is in history and the session prompt names the active part.
        assert any(isinstance(m, HumanMessage) and PN in str(m.content) for m in messages)
        system = next(str(m.content) for m in messages if isinstance(m, SystemMessage))
        active = system.split("Active part numbers: ")[1].splitlines()[0]
        return lookup(active)

    service = make_service(
        [lookup(), AIMessage("Listo."), follow_up, AIMessage(f"Consume {POWER} W.")]
    )
    first = await service.handle(USER, None, f"datos del {PN}")
    second = await service.handle(USER, first.session_id, "¿y cuánta potencia consume?")

    assert second.session_id == first.session_id
    assert second.response_type == "detail"
    assert [p.part_number for p in second.products] == [PN]
    assert second.products[0].motor_power_w == POWER


async def test_numbers_from_earlier_turns_are_accepted(make_service) -> None:
    service = make_service([lookup(), AIMessage("Listo."), AIMessage(f"Eran {POWER} W.")])
    first = await service.handle(USER, None, f"datos del {PN}")
    second = await service.handle(USER, first.session_id, "¿cuánto era?")
    assert second.answer == f"Eran {POWER} W."
    assert second.warnings == []


async def test_unknown_session_is_rejected(make_service) -> None:
    with pytest.raises(SessionNotFound):
        await make_service([]).handle(USER, uuid.uuid4(), "q")


async def test_another_users_session_is_not_found(make_service) -> None:
    service = make_service([lookup(), AIMessage("Listo."), lookup(), AIMessage("Listo.")])
    first = await service.handle(USER, None, f"datos del {PN}")
    with pytest.raises(SessionNotFound):
        await service.handle(OTHER, first.session_id, "¿y la potencia?")
    assert await _count(make_service.maker, Message) == 2  # nothing stored for OTHER


async def test_retry_shares_the_turn_call_budget(make_service) -> None:
    loop = [calls(tool_call("list_catalog_options", field="enclosure")) for _ in range(10)]
    service = make_service([lookup(), AIMessage(f"Consume {UNBACKED} W."), *loop])
    response = await service.handle(USER, None, "q")

    # The retry ran out of the turn's budget, so the first answer is kept, stripped.
    assert str(UNBACKED) not in response.answer.replace(",", "")
    assert "numbers_removed" in response.warnings
    async with make_service.maker() as db:
        trace = await repo.get_trace(db, response.message_id)
    assert len(trace.usage) <= SETTINGS.model_call_limit
    assert len(trace.tool_calls) <= SETTINGS.tool_call_limit


async def test_retry_provider_failure_keeps_the_first_answer_stripped(make_service) -> None:
    service = make_service([lookup(), AIMessage(f"Consume {UNBACKED} W."), ConnectionError()])
    response = await service.handle(USER, None, "q")
    assert response.response_type == "detail"
    assert "numbers_removed" in response.warnings


async def test_tool_names_are_removed_when_the_retry_repeats_them(make_service) -> None:
    leak = AIMessage("Según Get_Actuator, aquí está.")
    response = await make_service([lookup(), leak, leak]).handle(USER, None, "q")
    assert "actuator" not in response.answer.lower()


INVENTED_PN = "767C99-99999999/Z"  # well formed, not in the catalog


async def test_unbacked_part_number_is_retried_then_stripped(make_service) -> None:
    assert INVENTED_PN not in {r.part_number for r in CATALOG}
    notes: list[str] = []

    def still_bad(messages):
        notes.append(str(messages[-1].content))
        return AIMessage(f"Te sirve el {PN} o el {INVENTED_PN}.")

    first = AIMessage(f"Te sirve el {PN} o el {INVENTED_PN}.")
    response = await make_service([lookup(), first, still_bad]).handle(USER, None, "q")

    assert INVENTED_PN in notes[0]
    assert INVENTED_PN not in response.answer
    assert PN in response.answer  # the part number the tool returned stays
    assert "numbers_removed" in response.warnings


async def test_short_name_of_a_returned_part_number_is_backed(make_service) -> None:
    answer = AIMessage(f"El {PN[:4]} es el modelo.")
    response = await make_service([lookup(), answer]).handle(USER, None, "q")
    assert response.answer == f"El {PN[:4]} es el modelo."
    assert response.warnings == []


async def test_result_field_names_in_the_answer_are_retried(make_service) -> None:
    leak = AIMessage("Revisé current_type y tolerance_applied.")
    response = await make_service([lookup(), leak, AIMessage("Aquí está.")]).handle(USER, None, "q")
    assert response.answer == "Aquí está."


async def test_raw_json_and_field_names_are_removed_when_the_retry_repeats_them(
    make_service,
) -> None:
    leak = AIMessage('Aquí está {"status": "found", "actuator": {"data_available": true}} y fin.')
    response = await make_service([lookup(), leak, leak]).handle(USER, None, "q")
    assert "{" not in response.answer and "data_available" not in response.answer
    assert response.answer.startswith("Aquí está") and response.answer.endswith("y fin.")


async def test_numbers_only_in_history_text_are_not_backed(make_service) -> None:
    service = make_service(
        [
            AIMessage("¿Qué voltaje tienes?"),
            AIMessage(f"Dijiste {UNBACKED}."),
            AIMessage(f"Dijiste {UNBACKED}."),
        ]
    )
    first = await service.handle(USER, None, f"tengo {UNBACKED}")
    second = await service.handle(USER, first.session_id, "¿qué dije?")
    assert str(UNBACKED) not in second.answer.replace(",", "")
    assert "numbers_removed" in second.warnings


async def test_empty_answer_is_a_controlled_error(make_service) -> None:
    response = await make_service([lookup(), AIMessage("")]).handle(USER, None, "q")
    assert response.response_type == "error"
    assert response.answer == FALLBACK_ANSWER
    assert response.products == []


# --- Daily budget -----------------------------------------------------------------------------


async def _spend(maker, cost: str, days_ago: int = 0) -> None:
    """A stored turn whose model call cost `cost` USD, created `days_ago` days back."""
    async with maker() as db:
        session = await repo.create_session(db, USER, "spent")
        message = await repo.add_message(db, session.id, "assistant", "a")
        await repo.add_trace(db, message.id, [], [UsageRecord(model="m", cost_usd=Decimal(cost))])
        if days_ago:
            stmt = update(Message).where(Message.id == message.id)
            await db.execute(stmt.values(created_at=func.now() - timedelta(days=days_ago)))
        await db.commit()


def _budget_service(
    maker, budget: str, steps: list
) -> tuple[ConversationService, ScriptedFakeModel]:
    settings = SETTINGS.model_copy(update={"daily_budget_usd": Decimal(budget)})
    model = ScriptedFakeModel(steps=steps)
    return ConversationService(build_agent(settings, model), maker, settings), model


async def test_spent_budget_rejects_the_turn_before_the_model_runs(make_service) -> None:
    maker = make_service.maker
    await _spend(maker, "0.30")
    await _spend(maker, "0.20")
    service, model = _budget_service(maker, "0.50", [lookup(), AIMessage("Listo.")])

    with pytest.raises(BudgetExceeded):
        await service.handle(USER, None, "hola")

    assert model.remaining == 2  # the model was never called
    assert await _count(maker, Message) == 2  # only the seeded turns


async def test_turn_runs_while_under_the_budget(make_service) -> None:
    maker = make_service.maker
    await _spend(maker, "0.30")
    await _spend(maker, "5.00", days_ago=1)  # yesterday does not count
    service, _ = _budget_service(
        maker, "0.50", [lookup(), AIMessage(f"El {PN} consume {POWER} W.")]
    )

    response = await service.handle(USER, None, f"datos del {PN}")
    assert response.response_type == "detail"
