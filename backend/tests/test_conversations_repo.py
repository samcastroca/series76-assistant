"""Conversation repository against Postgres: ordering, history window, cascade delete, traces
and per-user scoping of sessions."""

import uuid

import pytest
from sqlalchemy import func, select

from app.db import conversations as repo
from app.db.models import LlmUsage, Message, ToolCall
from app.schemas.trace import ToolCallRecord, UsageRecord
from tests._db import create_user

pytestmark = pytest.mark.db


@pytest.fixture
async def uid(db_session) -> uuid.UUID:
    return (await create_user(db_session, "owner@example.com")).id


async def _turn(db, session_id, n: int, result: dict | None = None):
    await repo.add_message(db, session_id, "user", f"question {n}")
    answer = await repo.add_message(db, session_id, "assistant", f"answer {n}", {"n": n})
    call = ToolCallRecord(
        seq=0, tool_name="get_actuator", args={"q": n}, result=result, latency_ms=3
    )
    usage = UsageRecord(model="fake", input_tokens=10, output_tokens=2)
    await repo.add_trace(db, answer.id, [call], [usage])
    await db.commit()  # one transaction per turn, as the service does
    return answer


async def test_messages_are_chronological_with_user_first_in_a_turn(db_session, uid) -> None:
    s = await repo.create_session(db_session, uid, "t")
    for n in range(3):
        await _turn(db_session, s.id, n)
    contents = [m.content for m in await repo.list_messages(db_session, s.id)]
    assert contents == [
        "question 0",
        "answer 0",
        "question 1",
        "answer 1",
        "question 2",
        "answer 2",
    ]


async def test_recent_messages_keeps_last_turns_and_skips_failed(db_session, uid) -> None:
    s = await repo.create_session(db_session, uid, "t")
    for n in range(3):
        await _turn(db_session, s.id, n)
    await repo.add_message(db_session, s.id, "user", "failed", status="error")
    await db_session.commit()

    recent = await repo.recent_messages(db_session, s.id, turns=2)
    assert [m.content for m in recent] == ["question 1", "answer 1", "question 2", "answer 2"]


async def test_session_tool_results_only_include_that_session(db_session, uid) -> None:
    a = await repo.create_session(db_session, uid, "a")
    b = await repo.create_session(db_session, uid, "b")
    await _turn(db_session, a.id, 0, result={"x": 1})
    await _turn(db_session, b.id, 0, result={"x": 2})
    await _turn(db_session, a.id, 1, result=None)  # failed tool call: nothing to validate against
    assert await repo.session_tool_results(db_session, a.id) == [{"x": 1}]


async def test_list_sessions_counts_messages_and_orders_by_update(db_session, uid) -> None:
    old = await repo.create_session(db_session, uid, "old")
    await db_session.commit()
    new = await repo.create_session(db_session, uid, "new")
    await _turn(db_session, new.id, 0)
    await repo.touch_session(db_session, new.id, {"active_parts": ["P"]})
    await db_session.commit()

    rows = await repo.list_sessions(db_session, uid)
    assert [(r.session.title, r.message_count) for r in rows] == [("new", 2), ("old", 0)]
    await db_session.refresh(rows[0].session)
    assert rows[0].session.state == {"active_parts": ["P"]}
    assert old.id != new.id


async def test_delete_session_cascades_to_messages_and_traces(db_session, uid) -> None:
    s = await repo.create_session(db_session, uid, "t")
    await _turn(db_session, s.id, 0, result={"x": 1})
    assert await repo.delete_session(db_session, s.id, uid)
    await db_session.commit()
    for model in (Message, ToolCall, LlmUsage):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0
    assert not await repo.delete_session(db_session, s.id, uid)


async def test_get_trace_returns_calls_in_order_and_usage(db_session, uid) -> None:
    s = await repo.create_session(db_session, uid, "t")
    answer = await _turn(db_session, s.id, 0, result={"x": 1})
    trace = await repo.get_trace(db_session, answer.id)
    assert trace is not None
    assert [c.tool_name for c in trace.tool_calls] == ["get_actuator"]
    assert trace.tool_calls[0].result == {"x": 1}
    assert trace.usage[0].input_tokens == 10
    assert await repo.get_trace(db_session, uuid.uuid4()) is None


async def test_sessions_are_scoped_to_their_owner(db_session, uid) -> None:
    other = (await create_user(db_session, "other@example.com")).id
    s = await repo.create_session(db_session, uid, "mine")
    await db_session.commit()

    assert await repo.get_session(db_session, s.id, uid) is not None
    assert await repo.get_session(db_session, s.id, other) is None
    assert await repo.list_sessions(db_session, other) == []
    assert not await repo.delete_session(db_session, s.id, other)
    assert [r.session.id for r in await repo.list_sessions(db_session, uid)] == [s.id]
