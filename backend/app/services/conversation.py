"""Conversation service: one chat turn from query to persisted answer.

read session + history (short read) -> run the agent (no connection held) -> validate the
answer's numbers (one retry, then strip them) -> build products and response_type from tool
results -> persist the user message, the answer, its tool calls and LLM usage in one
transaction.

- A provider failure raises LlmUnavailable (503). In an existing session the user message is
  stored with status 'error' (with the turn's tool calls and usage) so the app can show it and
  retry; a successful retry of the same query reuses that message instead of duplicating it.
  A new session is not created, since the 503 body cannot return its id.
- A per-turn call limit or an empty answer returns a controlled 'error' answer; the internal
  detail goes to the trace only.
- The call limits are per turn: the validator's retry shares the first run's budget.
- With DAILY_BUDGET_USD set, a turn whose day (UTC) already spent the cap raises
  BudgetExceeded (503) before the model runs; nothing is stored."""

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.context import AgentContext
from app.agent.tools import TOOLS
from app.agent.validator import strip_numbers, unsupported_numbers, unsupported_part_numbers
from app.config import Settings
from app.db import conversations as repo
from app.schemas import catalog as catalog_schemas
from app.schemas.conversation import ConversationResponse
from app.services.history import to_messages, update_parts
from app.services.products import TurnView, build_view
from app.services.titles import make_title

logger = logging.getLogger(__name__)

# Shown when the agent could not finish a turn. Fixed text, so it covers both app languages.
FALLBACK_ANSWER = (
    "No pude completar esta consulta. Intenta reformularla. / "
    "I could not complete this request. Please rephrase it."
)
# Tool names in any case, with '_' or a space between words.
_TOOL_NAMES = re.compile(
    "|".join(re.escape(t.name).replace("_", "[_ ]") for t in TOOLS), re.IGNORECASE
)
# Field names of the tool schemas and results ("tolerance_applied", "current_type"): only the
# snake_case ones, since single words like "status" are also plain language.
_FIELD_NAMES = re.compile(
    r"(?<![0-9A-Za-z_])(?:"
    + "|".join(
        re.escape(name)
        for model in vars(catalog_schemas).values()
        if isinstance(model, type)
        and issubclass(model, BaseModel)
        and model.__module__ == catalog_schemas.__name__
        for name in model.model_fields
        if "_" in name
    )
    + r")(?![0-9A-Za-z_])",
    re.IGNORECASE,
)
# A JSON object: braces around at least one quoted key and a colon.
_JSON_OBJECT = re.compile(r'\{[^{}]*"[^{}"]*"\s*:[^{}]*\}')
_RETRY_NOTE = (
    "Automatic check of your previous answer: {problems}. Rewrite the answer for the user. "
    "Use only numbers that appear in tool results of this conversation (call a tool again if "
    "you need a value) and never mention tools or internal details. Do not mention this check."
)
_LIMITS = (ModelCallLimitExceededError, ToolCallLimitExceededError)


class ConversationError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SessionNotFound(ConversationError):
    status_code = 404
    code = "session_not_found"


class LlmUnavailable(ConversationError):
    status_code = 503
    code = "llm_unavailable"


class BudgetExceeded(ConversationError):
    status_code = 503
    code = "budget_exceeded"


class _ProviderError(Exception):
    """The agent run failed for a reason other than a call limit (provider, network, auth)."""


class _TurnStopped(Exception):
    """The turn cannot produce an answer (call limit, empty answer); detail is for the trace."""


@dataclass
class _Loaded:
    user_id: uuid.UUID
    session_id: uuid.UUID
    is_new: bool
    title: str
    state: dict[str, Any]
    history: list[BaseMessage] = field(default_factory=list)
    earlier_results: list[dict[str, Any]] = field(default_factory=list)


class ConversationService:
    def __init__(
        self,
        agent: CompiledStateGraph,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self.agent = agent
        self.sessionmaker = sessionmaker
        self.settings = settings

    async def handle(
        self, user_id: uuid.UUID, session_id: uuid.UUID | None, query: str
    ) -> ConversationResponse:
        loaded = await self._load(user_id, session_id, query)
        await self._check_budget()
        ctx = AgentContext(
            sessionmaker=self.sessionmaker,
            active_parts=loaded.state.get("active_parts", []),
            shown_parts=loaded.state.get("shown_parts", []),
            model_call_limit=self.settings.model_call_limit,
            tool_call_limit=self.settings.tool_call_limit,
            default_model=self.settings.agent_model or "",
        )
        internal_error = None
        try:
            answer, warnings = await self._answer(loaded, query, ctx)
        except _TurnStopped as exc:
            answer, warnings, internal_error = FALLBACK_ANSWER, [], str(exc)
        except _ProviderError as exc:
            await self._save_failed(loaded, query, ctx, str(exc))
            raise LlmUnavailable("The language model is unavailable. Try again.") from exc

        view = build_view(ctx.recorder.calls, answer)
        if internal_error:
            view = TurnView("error", warnings=view.warnings)
        view.warnings = list(dict.fromkeys([*view.warnings, *warnings]))
        message_id = await self._save_turn(loaded, query, answer, view, ctx, internal_error)
        return ConversationResponse(
            session_id=loaded.session_id,
            message_id=message_id,
            answer=answer,
            response_type=view.response_type,
            products=view.products,
            highlight_fields=view.highlight_fields,
            clarification=view.clarification,
            suggestions=view.suggestions,
            warnings=view.warnings,
        )

    # --- Read and write phases (no connection is held while the model runs) ----------------

    async def _load(self, user_id: uuid.UUID, session_id: uuid.UUID | None, query: str) -> _Loaded:
        if session_id is None:  # created in the write transaction, with the first turn
            return _Loaded(user_id, uuid.uuid4(), True, make_title(query), {})
        async with self.sessionmaker() as db:
            session = await repo.get_session(db, session_id, user_id)
            if session is None:  # missing or another user's: the same 404
                raise SessionNotFound("Conversation not found.")
            rows = await repo.recent_messages(db, session.id, self.settings.history_turns)
            return _Loaded(
                user_id,
                session.id,
                False,
                session.title,
                dict(session.state or {}),
                to_messages(rows),
                await repo.session_tool_results(db, session.id),
            )

    async def _check_budget(self) -> None:
        budget = self.settings.daily_budget_usd
        if budget is None:
            return
        async with self.sessionmaker() as db:
            spent = await repo.spent_today(db)
        if spent >= budget:
            logger.warning("daily LLM budget reached: %s of %s USD", spent, budget)
            raise BudgetExceeded("The daily usage limit was reached. Try again tomorrow.")

    async def _save_turn(
        self,
        loaded: _Loaded,
        query: str,
        answer: str,
        view: TurnView,
        ctx: AgentContext,
        internal_error: str | None,
    ) -> uuid.UUID:
        payload = _payload(view)
        if internal_error:
            payload["internal_error"] = internal_error  # trace only, never in the chat
        async with self.sessionmaker() as db:
            if loaded.is_new:
                await repo.create_session(db, loaded.user_id, loaded.title, loaded.session_id)
                await repo.add_message(db, loaded.session_id, "user", query)
            elif not await repo.reuse_failed_user_message(db, loaded.session_id, query):
                await repo.add_message(db, loaded.session_id, "user", query)
            message = await repo.add_message(db, loaded.session_id, "assistant", answer, payload)
            await repo.add_trace(db, message.id, ctx.recorder.records, ctx.usage)
            parts = [p.part_number for p in view.products]
            await repo.touch_session(db, loaded.session_id, update_parts(loaded.state, parts))
            await db.commit()
            return message.id

    async def _save_failed(
        self, loaded: _Loaded, query: str, ctx: AgentContext, error: str
    ) -> None:
        if loaded.is_new:
            logger.warning("provider failed on a new session; nothing stored (%s)", error)
            return
        async with self.sessionmaker() as db:
            payload = {"error": LlmUnavailable.code, "internal_error": error}
            message = await repo.add_message(
                db, loaded.session_id, "user", query, payload, status="error"
            )
            await repo.add_trace(db, message.id, ctx.recorder.records, ctx.usage)
            await db.commit()

    # --- Agent run and validation ------------------------------------------------------------

    async def _answer(
        self, loaded: _Loaded, query: str, ctx: AgentContext
    ) -> tuple[str, list[str]]:
        """Run the agent; on unbacked numbers or leaked internals, retry once, then strip."""
        out = await self._invoke([*loaded.history, HumanMessage(query)], ctx)
        answer = _final_text(out)
        if answer is None:
            raise _TurnStopped("empty_answer")
        problems = _problems(answer, ctx, loaded.earlier_results)
        if not problems:
            return answer, []

        retry = [*out, HumanMessage(_RETRY_NOTE.format(problems="; ".join(problems)))]
        try:
            retried = _final_text(await self._invoke(retry, ctx))
        except (_TurnStopped, _ProviderError) as exc:  # keep the first answer, stripped
            logger.warning("validator retry failed: %s", exc)
            retried = None
        if retried is not None:
            answer = retried
            if not _problems(answer, ctx, loaded.earlier_results):
                return answer, []

        results = [*ctx.recorder.result_dumps, *loaded.earlier_results]
        bad = unsupported_numbers(answer, results) + unsupported_part_numbers(answer, results)
        stripped = strip_numbers(answer, bad)
        if _has_internals(stripped):
            logger.warning("removing internal details from the answer")
            stripped = _strip_internals(stripped)
        return stripped, ["numbers_removed"] if bad else []

    async def _invoke(self, messages: list[BaseMessage], ctx: AgentContext) -> list[BaseMessage]:
        try:
            out = await self.agent.ainvoke({"messages": messages}, context=ctx)
        except _LIMITS as exc:
            logger.warning("turn stopped by a call limit: %s", exc)
            raise _TurnStopped(f"{type(exc).__name__}: {exc}") from exc
        except Exception as exc:  # tools never raise here (middleware); this is the provider
            logger.exception("agent run failed")
            raise _ProviderError(f"{type(exc).__name__}: {exc}") from exc
        return out["messages"]


def _problems(answer: str, ctx: AgentContext, earlier_results: list[dict[str, Any]]) -> list[str]:
    problems = []
    results = [*ctx.recorder.result_dumps, *earlier_results]
    bad = unsupported_numbers(answer, results)
    if bad:
        problems.append(
            "these numbers are not in any tool result: " + ", ".join(t.text for t in bad)
        )
    bad_parts = unsupported_part_numbers(answer, results)
    if bad_parts:
        problems.append(
            "these part numbers are not in any tool result: " + ", ".join(t.text for t in bad_parts)
        )
    if _has_internals(answer):
        problems.append("it shows internal details (tool names, result field names or raw data)")
    return problems


def _has_internals(text: str) -> bool:
    return any(p.search(text) for p in (_TOOL_NAMES, _FIELD_NAMES, _JSON_OBJECT))


def _strip_internals(text: str) -> str:
    while (inner_removed := _JSON_OBJECT.sub("", text)) != text:  # nested objects, inside out
        text = inner_removed
    return _FIELD_NAMES.sub("", _TOOL_NAMES.sub("", text))


def _final_text(messages: list[BaseMessage]) -> str | None:
    last = messages[-1]
    text = last.text.strip() if isinstance(last, AIMessage) else ""
    return text or None


def _payload(view: TurnView) -> dict[str, Any]:
    return {
        "response_type": view.response_type,
        "products": [p.model_dump(mode="json") for p in view.products],
        "highlight_fields": view.highlight_fields,
        "clarification": None
        if view.clarification is None
        else view.clarification.model_dump(mode="json"),
        "suggestions": view.suggestions,
        "warnings": view.warnings,
    }
