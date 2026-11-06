"""Per-turn runtime context handed to tools and middleware through `ToolRuntime.context`."""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.recorder import ToolRecorder


@dataclass
class AgentContext:
    # Tools open their own short read-only session, so a failing query cannot poison the
    # transaction the conversation service writes the turn in.
    sessionmaker: async_sessionmaker[AsyncSession]
    recorder: ToolRecorder = field(default_factory=ToolRecorder)
    active_parts: list[str] = field(default_factory=list)  # last part numbers shown
    shown_parts: list[str] = field(default_factory=list)  # earlier in the session, oldest first
