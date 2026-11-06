"""What one assistant turn did internally: its tool calls and its LLM usage. Recorded by the agent
layer, stored in tool_calls / llm_usage, and returned only by the debug trace endpoint."""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    tool_name: str
    args: dict[str, Any]
    result: dict[str, Any] | None  # JSON-mode dump of the tool's typed result; None on error
    latency_ms: int
    error: str | None = None


class UsageRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None  # None when the provider does not report it
