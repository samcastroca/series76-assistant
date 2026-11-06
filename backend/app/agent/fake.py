"""Fake chat models for LLM_PROVIDER=fake: LangChain chat models that emit predefined tool calls,
so the real tools, middleware and persistence run without any LLM or key.

- ScriptedFakeModel replays a script step by step (tests). A step is an AIMessage, a callable that
  builds one from the conversation so far, or an exception to raise (a provider failure).
- RuleBasedFakeModel follows a fixed rule script (`make up` without a key): part numbers in the
  query -> get_actuator; a follow-up -> get_actuator on the active part numbers; "options" ->
  list_catalog_options; otherwise search_actuators with the filters it recognizes. Its final
  answers contain no technical numbers (the specs travel in `products`) and are always in
  Spanish: it is a demo stand-in, not a language model.
"""

import json
import re
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

Step = AIMessage | Callable[[list[BaseMessage]], AIMessage] | Exception


def tool_call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}


def calls(*tool_calls: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=list(tool_calls))


class _FakeBase(BaseChatModel):
    model_name: str = "fake"

    @property
    def _llm_type(self) -> str:
        return self.model_name

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "_FakeBase":
        return self  # tool calls are scripted; the schemas are not needed

    def _respond(self, messages: list[BaseMessage]) -> AIMessage:
        raise NotImplementedError

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self._respond(messages).model_copy()
        if message.usage_metadata is None:  # deterministic token counts so usage gets logged
            prompt = sum(len(str(m.content)) for m in messages) // 4
            output = len(str(message.content)) // 4 + 10 * len(message.tool_calls)
            message.usage_metadata = {
                "input_tokens": prompt,
                "output_tokens": output,
                "total_tokens": prompt + output,
            }
        message.response_metadata = {"model_name": self.model_name, **message.response_metadata}
        return ChatResult(generations=[ChatGeneration(message=message)])


class ScriptedFakeModel(_FakeBase):
    steps: list[Any]
    model_name: str = "fake-scripted"
    _cursor: int = PrivateAttr(default=0)

    def _respond(self, messages: list[BaseMessage]) -> AIMessage:
        if self._cursor >= len(self.steps):
            raise RuntimeError("ScriptedFakeModel: script exhausted")
        step = self.steps[self._cursor]
        self._cursor += 1
        if isinstance(step, Exception):
            raise step
        return step(messages) if callable(step) else step

    @property
    def remaining(self) -> int:
        return len(self.steps) - self._cursor


# --- Rule-based script ------------------------------------------------------------------------

_PART = re.compile(r"(?<![0-9A-Za-z])76[1-7][A-CXYa-cxy][0-9A-Za-z\-/]*")
_ACTIVE = re.compile(r"^Active part numbers: (.+)$", re.MULTILINE)
_VOLTAGE = re.compile(r"(\d{2,3})\s?v(?:olts?)?\b", re.IGNORECASE)
_TORQUE = re.compile(r"(\d[\d,.]*)\s?(nm|n·m|in-?lb|lb-?in)\b", re.IGNORECASE)
_OPTIONS = re.compile(r"opcion|option|disponib|availab|qué hay|what .* (have|offer)", re.IGNORECASE)
_REQUIREMENT = re.compile(
    r"\d\s?(v|nm|in-?lb)|weatherproof|explosion|modulat|on.?off|recomien|recommend|necesito|need",
    re.IGNORECASE,
)


def _last_human(messages: list[BaseMessage]) -> int:
    return max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))


def _search_args(text: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    lower = text.lower()
    if m := _VOLTAGE.search(text):
        args["voltage_v"] = int(m.group(1))
    if m := _TORQUE.search(text):
        value = m.group(1).replace(",", "")
        args["min_torque"] = value
        args["torque_unit"] = "nm" if m.group(2).lower().startswith("n") else "in_lb"
    if "explosion" in lower:
        args["enclosure"] = "explosionproof"
    elif "weatherproof" in lower:
        args["enclosure"] = "weatherproof"
    if "modula" in lower:
        args["service_type"] = "modulating"
    elif re.search(r"on.?off|todo.?nada", lower):
        args["service_type"] = "on_off"
    if re.search(r"tri(f|ph)|3.?ph|3.?fase", lower):
        args["phase"] = 3
    elif re.search(r"mono|1.?ph|single", lower):
        args["phase"] = 1
    return args


def _final_answer(results: list[dict[str, Any]]) -> str:
    if not results or not all(results):  # a failed call has no JSON result
        return "No pude consultar el catálogo en este momento."
    statuses = {r.get("status") for r in results}
    if "partial_match" in statuses:
        return "Hay varios modelos que coinciden. ¿Cuál de ellos te interesa?"
    if "no_data" in statuses:
        return "Ese part number existe, pero sus datos no están publicados en las tablas."
    if "not_found" in statuses:
        return "No encontré ese part number. Revisa las sugerencias."
    if any("matches" in r and not r["matches"] for r in results):
        return "Ningún modelo cumple todos los requisitos. Estas son las alternativas más cercanas."
    if any("matches" in r for r in results):
        return "Estos modelos cumplen los requisitos. Los datos están en las tarjetas."
    if any("enclosures" in r or "power_supplies" in r for r in results):
        return "Estas son las opciones disponibles en el catálogo. ¿Cuál necesitas?"
    return "Aquí tienes los datos del catálogo Series 76. Los valores están en las tarjetas."


class RuleBasedFakeModel(_FakeBase):
    model_name: str = "fake-rules"

    def _respond(self, messages: list[BaseMessage]) -> AIMessage:
        start = _last_human(messages)
        tool_messages = [m for m in messages[start:] if isinstance(m, ToolMessage)]
        if tool_messages:
            parsed = [{} if m.status == "error" else _parse(m) for m in tool_messages]
            return AIMessage(content=_final_answer(parsed))

        text = str(messages[start].content)
        parts = [p.upper() for p in _PART.findall(text)]
        if parts:
            return calls(*(tool_call("get_actuator", part_number=p) for p in parts))
        active = _active_parts(messages)
        if active and not _REQUIREMENT.search(text) and not _OPTIONS.search(text):
            return calls(*(tool_call("get_actuator", part_number=p) for p in active))
        args = _search_args(text)
        if not args or _OPTIONS.search(text):
            return calls(tool_call("list_catalog_options"))
        return calls(tool_call("search_actuators", **args))


def _active_parts(messages: list[BaseMessage]) -> list[str]:
    for m in messages:
        if isinstance(m, SystemMessage) and (found := _ACTIVE.search(str(m.content))):
            return [p.strip() for p in found.group(1).split(",")]
    return []


def _parse(message: ToolMessage) -> dict[str, Any]:
    try:
        parsed = json.loads(str(message.content))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
