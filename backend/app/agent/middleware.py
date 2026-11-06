"""Agent middleware: record every tool call and turn tool failures into a message the model can
handle, and build the system prompt with the session's active part numbers."""

import logging
from collections.abc import Awaitable, Callable
from functools import cache
from pathlib import Path
from time import perf_counter

from langchain.agents.middleware import ModelRequest, dynamic_prompt, wrap_tool_call
from langchain.messages import ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from langgraph.types import Command

from app.agent.context import AgentContext

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_PATH = Path(__file__).with_name("system_prompt.md")
# Seen by the model only; the real error goes to the trace.
TOOL_FAILED = "The catalog lookup failed. Tell the user the data is unavailable right now."
INVALID_ARGS = (
    "Invalid arguments: check the allowed values in the tool schema. min_torque and "
    "torque_unit must be given together."
)


@cache
def base_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def render_system_prompt(active_parts: list[str], shown_parts: list[str]) -> str:
    lines = [base_system_prompt().rstrip()]
    if active_parts:
        lines += ["", "## Session", "", f"Active part numbers: {', '.join(active_parts)}"]
        earlier = [p for p in shown_parts if p not in active_parts]
        if earlier:
            lines.append(f"Shown earlier in this conversation: {', '.join(earlier)}")
    return "\n".join(lines)


@dynamic_prompt
def session_prompt(request: ModelRequest) -> str:
    ctx: AgentContext = request.runtime.context
    return render_system_prompt(ctx.active_parts, ctx.shown_parts)


@wrap_tool_call
async def record_tool_calls(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
) -> ToolMessage | Command:
    ctx: AgentContext = request.runtime.context
    call = request.tool_call
    start = perf_counter()
    try:
        message = await handler(request)
    except Exception as exc:  # any tool failure becomes a message, never a crashed turn
        logger.exception("tool %s failed", call["name"])
        ctx.recorder.add(
            call["name"], call["args"], None, _ms(start), f"{type(exc).__name__}: {exc}"
        )
        return ToolMessage(
            content=TOOL_FAILED, tool_call_id=call["id"], name=call["name"], status="error"
        )
    if isinstance(message, ToolMessage):
        failed = message.status == "error"  # arguments rejected by the schema
        ctx.recorder.add(
            call["name"],
            call["args"],
            None if failed else message.artifact,
            _ms(start),
            str(message.content) if failed else None,
        )
        if failed:  # the full validation text stays in the trace, not in the model's context
            return message.model_copy(update={"content": INVALID_ARGS})
    return message


def _ms(start: float) -> int:
    return round((perf_counter() - start) * 1000)
