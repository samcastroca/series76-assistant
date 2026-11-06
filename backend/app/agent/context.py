"""Per-turn runtime context handed to tools and middleware through `ToolRuntime.context`."""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.recorder import ToolRecorder
from app.schemas.trace import UsageRecord


@dataclass
class AgentContext:
    # Tools open their own short read-only session; the turn holds no connection while the
    # model runs.
    sessionmaker: async_sessionmaker[AsyncSession]
    recorder: ToolRecorder = field(default_factory=ToolRecorder)
    usage: list[UsageRecord] = field(default_factory=list)  # one per model call, as it happens
    active_parts: list[str] = field(default_factory=list)  # last part numbers shown
    shown_parts: list[str] = field(default_factory=list)  # earlier in the session, oldest first
    # Per-turn budgets, shared by every agent run of the turn (the validator's retry included).
    # The built-in limit middleware counts per run; these counters make the limit per turn.
    model_call_limit: int | None = None
    tool_call_limit: int | None = None
    default_model: str = ""  # usage label when the provider does not name the model
