"""LangChain wrappers over the catalog tools in app/tools/catalog_tools.py. Arguments are the
existing typed Pydantic schemas; the database session comes from the runtime context. Each tool
returns (JSON for the model, typed result as the message artifact) so the backend builds
`products` from the typed result and never from text."""

from typing import Any

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, ConfigDict, Field

from app.agent.context import AgentContext
from app.agent.recorder import ToolOutput
from app.schemas.catalog import CatalogOptionsArgs, GetActuatorArgs, SearchFilters
from app.tools import catalog_tools


def _out(result: BaseModel, specs: list | None = None) -> tuple[str, ToolOutput]:
    return result.model_dump_json(), ToolOutput(result, specs or [])


# The argument schemas forbid extra fields, so each tool schema declares the injected runtime.
# LangChain hides a ToolRuntime field from the schema the model sees.
class _Injected(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    runtime: ToolRuntime = Field(exclude=True)  # never serialized


class _GetActuatorInput(GetActuatorArgs, _Injected):
    pass


class _SearchInput(SearchFilters, _Injected):
    pass


class _OptionsInput(CatalogOptionsArgs, _Injected):
    pass


@tool(
    "get_actuator",
    args_schema=_GetActuatorInput,
    response_format="content_and_artifact",
    description=(
        "Look up one Series 76 actuator by part number, complete or partial. Spaces, dashes, "
        "slashes and case are ignored. Returns status: 'found' (full specs), 'no_data' (the "
        "part number exists but its specs are not published), 'partial_match' (the input is "
        "the start of several part numbers; candidates are listed, ask the user which one) or "
        "'not_found' (similar part numbers may be suggested). Specs include torque per service "
        "type, speed and currents per frequency, motor power and certifications."
    ),
)
async def get_actuator(part_number: str, runtime: ToolRuntime[AgentContext]) -> Any:
    async with runtime.context.sessionmaker() as db:
        return _out(await catalog_tools.get_actuator(db, GetActuatorArgs(part_number=part_number)))


@tool(
    "search_actuators",
    args_schema=_SearchInput,
    response_format="content_and_artifact",
    description=(
        "Find actuators that meet requirements. Every filter is optional. voltage_v is the "
        "user's supply voltage; a tolerance is applied in code and reported as "
        "tolerance_applied. min_torque must come with torque_unit ('in_lb' or 'nm'), in the "
        "unit the user gave. service_type is 'on_off', 'modulating' or 'unspecified'; the "
        "3-phase table only has 'unspecified' ratings (no on/off vs modulating split), and "
        "asking for 'on_off' or 'modulating' excludes them. current_type 'AC' or 'DC' also "
        "matches AC/DC supplies. "
        "max_speed_s is the slowest acceptable stroke time in seconds, at frequency_hz when "
        "given. Matches are ranked by the smallest torque margin. When nothing matches, "
        "blocking_filters names the filters that prevent a match and closest_alternatives "
        "lists products that do NOT meet every requirement (see unmet)."
    ),
)
async def search_actuators(runtime: ToolRuntime[AgentContext], **filters: Any) -> Any:
    async with runtime.context.sessionmaker() as db:
        result = await catalog_tools.search_actuators(db, SearchFilters(**filters))
        return _out(result, await catalog_tools.specs_for(db, result))


@tool(
    "list_catalog_options",
    args_schema=_OptionsInput,
    response_format="content_and_artifact",
    description=(
        "List what the catalog offers, to answer 'what is available' or to ask a clarifying "
        "question with real options. field narrows the answer to one of: 'enclosure', "
        "'power_supply' (voltage, phase, current type, service types and frequencies per "
        "enclosure), 'service_type', 'series_size' or 'torque' (torque range per enclosure "
        "and service type). Omit field to get everything."
    ),
)
async def list_catalog_options(runtime: ToolRuntime[AgentContext], field: str | None = None) -> Any:
    async with runtime.context.sessionmaker() as db:
        args = CatalogOptionsArgs(field=field)
        return _out(await catalog_tools.list_catalog_options(db, args))


TOOLS = [get_actuator, search_actuators, list_catalog_options]
