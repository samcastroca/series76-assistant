"""Per-turn record of tool calls: the typed results feed `products` and the validator, and the
records are persisted to tool_calls for the debug trace."""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.schemas.catalog import ActuatorSpec
from app.schemas.trace import ToolCallRecord


@dataclass(frozen=True)
class ToolOutput:
    """A tool's message artifact. `result` is exactly what the model sees (as JSON) and what the
    validator checks against; `specs` are full specs of the products a search shows, used only
    to draw complete product cards."""

    result: BaseModel
    specs: list[ActuatorSpec] = field(default_factory=list)


@dataclass(frozen=True)
class RecordedCall:
    record: ToolCallRecord
    result: BaseModel | None  # the tool's typed result; None when the call failed
    specs: list[ActuatorSpec] = field(default_factory=list)


@dataclass
class ToolRecorder:
    calls: list[RecordedCall] = field(default_factory=list)

    def add(
        self,
        tool_name: str,
        args: dict[str, Any],
        output: ToolOutput | None,
        latency_ms: int,
        error: str | None = None,
    ) -> None:
        result = None if output is None else output.result
        record = ToolCallRecord(
            seq=len(self.calls),
            tool_name=tool_name,
            args=args,
            result=None if result is None else result.model_dump(mode="json"),
            latency_ms=latency_ms,
            error=error,
        )
        self.calls.append(RecordedCall(record, result, [] if output is None else output.specs))

    @property
    def records(self) -> list[ToolCallRecord]:
        return [c.record for c in self.calls]

    @property
    def results(self) -> list[BaseModel]:
        return [c.result for c in self.calls if c.result is not None]

    @property
    def result_dumps(self) -> list[dict[str, Any]]:
        return [c.record.result for c in self.calls if c.record.result is not None]
