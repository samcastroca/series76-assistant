"""Conversation repository: parameterized reads and writes of sessions, messages, tool calls and
LLM usage. Functions never commit; the conversation service owns the transaction.

Session reads and deletes take the owner's user_id: another user's session behaves exactly like
a missing one."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatSession, LlmUsage, Message, ToolCall
from app.schemas.trace import ToolCallRecord, UsageRecord

# now() is the transaction start, so both messages of a turn share created_at; sorting by role
# descending puts 'user' before 'assistant' inside a turn.
_CHRONOLOGICAL = (Message.created_at, Message.role.desc())


async def create_session(
    db: AsyncSession, user_id: uuid.UUID, title: str, session_id: uuid.UUID | None = None
) -> ChatSession:
    session = ChatSession(id=session_id or uuid.uuid4(), user_id=user_id, title=title, state={})
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session


async def get_session(
    db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
) -> ChatSession | None:
    stmt = select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id)
    return await db.scalar(stmt)


@dataclass(frozen=True)
class SessionRow:
    session: ChatSession
    message_count: int


async def list_sessions(db: AsyncSession, user_id: uuid.UUID) -> list[SessionRow]:
    stmt = (
        select(ChatSession, func.count(Message.id))
        .outerjoin(Message, Message.session_id == ChatSession.id)
        .where(ChatSession.user_id == user_id)
        .group_by(ChatSession.id)
        .order_by(ChatSession.updated_at.desc(), ChatSession.id)
    )
    return [SessionRow(s, n) for s, n in (await db.execute(stmt)).all()]


async def delete_session(db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    result = await db.execute(
        delete(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == user_id)
    )
    return result.rowcount > 0


async def list_messages(db: AsyncSession, session_id: uuid.UUID) -> Sequence[Message]:
    stmt = select(Message).where(Message.session_id == session_id).order_by(*_CHRONOLOGICAL)
    return (await db.scalars(stmt)).all()


async def recent_messages(db: AsyncSession, session_id: uuid.UUID, turns: int) -> list[Message]:
    """The last `turns` turns (user + assistant), oldest first. Failed turns are left out."""
    stmt = (
        select(Message)
        .where(Message.session_id == session_id, Message.status == "ok")
        .order_by(Message.created_at.desc(), Message.role.asc())
        .limit(turns * 2)
    )
    return list(reversed((await db.scalars(stmt)).all()))


async def session_tool_results(db: AsyncSession, session_id: uuid.UUID) -> list[dict[str, Any]]:
    """Every stored tool result of the session, for the numeric validator."""
    stmt = (
        select(ToolCall.result)
        .join(Message, Message.id == ToolCall.message_id)
        .where(Message.session_id == session_id, ToolCall.result.is_not(None))
    )
    return list((await db.scalars(stmt)).all())


async def add_message(
    db: AsyncSession,
    session_id: uuid.UUID,
    role: str,
    content: str,
    payload: dict[str, Any] | None = None,
    status: str = "ok",
) -> Message:
    message = Message(
        id=uuid.uuid4(),
        session_id=session_id,
        role=role,
        content=content,
        payload=payload or {},
        status=status,
    )
    db.add(message)
    await db.flush()
    return message


async def reuse_failed_user_message(
    db: AsyncSession, session_id: uuid.UUID, query: str
) -> Message | None:
    """If the session's last message is this same query and it failed, mark it 'ok' and return
    it: a retry then answers the stored message instead of adding a duplicate. Its trace (the
    failed attempt's tool calls and usage) stays attached."""
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc(), Message.role.asc())
        .limit(1)
    )
    last = await db.scalar(stmt)
    if last is None or (last.role, last.status, last.content) != ("user", "error", query):
        return None
    last.status = "ok"
    last.payload = {}
    await db.flush()
    return last


async def add_trace(
    db: AsyncSession,
    message_id: uuid.UUID,
    tool_calls: Sequence[ToolCallRecord],
    usage: Sequence[UsageRecord],
) -> None:
    db.add_all(ToolCall(message_id=message_id, **c.model_dump()) for c in tool_calls)
    db.add_all(LlmUsage(message_id=message_id, **u.model_dump()) for u in usage)
    await db.flush()


async def spent_today(db: AsyncSession) -> Decimal:
    """LLM cost of every message created since 00:00 UTC, by the database clock (the same clock
    that sets created_at). NULL costs (not reported by the provider) count as zero."""
    midnight_utc = func.timezone("UTC", func.date_trunc("day", func.timezone("UTC", func.now())))
    stmt = (
        select(func.coalesce(func.sum(LlmUsage.cost_usd), 0))
        .join(Message, Message.id == LlmUsage.message_id)
        .where(Message.created_at >= midnight_utc)
    )
    return Decimal(await db.scalar(stmt))


async def touch_session(db: AsyncSession, session_id: uuid.UUID, state: dict[str, Any]) -> None:
    await db.execute(
        update(ChatSession)
        .where(ChatSession.id == session_id)
        .values(state=state, updated_at=func.now())
    )


@dataclass(frozen=True)
class Trace:
    message: Message
    tool_calls: list[ToolCallRecord]
    usage: list[UsageRecord]


async def get_trace(db: AsyncSession, message_id: uuid.UUID) -> Trace | None:
    message = await db.get(Message, message_id)
    if message is None:
        return None
    calls = await db.scalars(
        select(ToolCall).where(ToolCall.message_id == message_id).order_by(ToolCall.seq)
    )
    usage = await db.scalars(
        select(LlmUsage).where(LlmUsage.message_id == message_id).order_by(LlmUsage.id)
    )
    return Trace(
        message=message,
        tool_calls=[ToolCallRecord.model_validate(c) for c in calls],
        usage=[UsageRecord.model_validate(u) for u in usage],
    )
