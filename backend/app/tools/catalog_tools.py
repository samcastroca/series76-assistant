"""The three catalog tools. Plain async functions with typed Pydantic arguments; the agent layer
wraps them later. They read through app/db/catalog.py and never compute catalog values."""

from collections.abc import Iterable, Sequence
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import catalog
from app.db.models import Actuator
from app.schemas.catalog import (
    ActuatorSpec,
    ActuatorSummary,
    BlockingFilter,
    CatalogOptions,
    CatalogOptionsArgs,
    GetActuatorArgs,
    GetActuatorResult,
    Match,
    PowerSupplyOption,
    SearchFilters,
    SearchResult,
    TorqueRange,
)
from app.tools import matching
from app.tools.normalize import normalize_part_key

MAX_CANDIDATES = 10
_FREQUENCY_ORDER = {60: 0, 50: 1, None: 2}


def to_spec(actuator: Actuator) -> ActuatorSpec:
    spec = ActuatorSpec.model_validate(actuator, from_attributes=True)
    spec.service_ratings.sort(key=lambda s: matching.SERVICE_ORDER[s.service_type])
    spec.electrical_ratings.sort(key=lambda e: _FREQUENCY_ORDER[e.frequency_hz])
    spec.warnings = matching.actuator_warnings(spec)
    return spec


def _summaries(actuators: Iterable[Actuator]) -> list[ActuatorSummary]:
    return [ActuatorSummary.model_validate(a, from_attributes=True) for a in actuators]


# --- get_actuator ---------------------------------------------------------------------------


async def get_actuator(session: AsyncSession, args: GetActuatorArgs) -> GetActuatorResult:
    key = normalize_part_key(args.part_number)
    result = GetActuatorResult(status="not_found", query=args.part_number, normalized=key)
    if not key:
        return result

    exact = await catalog.get_by_key(session, key)
    if exact is not None:
        return _found(result, exact, "exact")

    prefixed = await catalog.find_by_prefix(session, key)
    if len(prefixed) == 1:
        return _found(result, prefixed[0], "prefix")
    if prefixed:
        result.status = "partial_match"
        result.candidates = _summaries(prefixed[:MAX_CANDIDATES])
        result.candidate_count = len(prefixed)
        return result

    result.suggestions = _summaries(await catalog.suggest_similar(session, key))
    return result


def _found(
    result: GetActuatorResult, actuator: Actuator, matched_by: Literal["exact", "prefix"]
) -> GetActuatorResult:
    result.matched_by = matched_by
    result.actuator = to_spec(actuator)
    result.status = "found" if actuator.data_available else "no_data"
    return result


# --- search_actuators -----------------------------------------------------------------------


async def search_actuators(session: AsyncSession, filters: SearchFilters) -> SearchResult:
    specs = [to_spec(a) for a in await catalog.list_actuators(session, with_data_only=True)]
    nominal = None
    if filters.voltage_v is not None:
        nominal = matching.nominal_voltage(filters.voltage_v, (s.voltage_v for s in specs))
    outcome = matching.run_search(specs, filters, nominal)
    return SearchResult(
        matches=[_match(c, filters) for c in outcome.matches],
        matched_voltage_v=nominal,
        tolerance_applied=nominal is not None and nominal != filters.voltage_v,
        blocking_filters=[BlockingFilter(filter=n, matches_without=k) for n, k in outcome.blocking],
        closest_alternatives=[_match(c, filters, unmet=n) for c, n in outcome.alternatives],
        multiple_filters_blocking=outcome.multiple_blocking,
        applied_filters=filters,
    )


def _match(c: matching.Candidate, filters: SearchFilters, unmet: str | None = None) -> Match:
    a, s = c.actuator, c.service
    rating = matching.speed_rating(a, filters.frequency_hz)
    return Match(
        part_number=a.part_number,
        enclosure=a.enclosure,
        voltage_v=a.voltage_v,
        phase=a.phase,
        current_type=a.current_type,
        series_size=a.series_size,
        csa_certified=a.csa_certified,
        service_type=s.service_type,
        torque_inlb=s.torque_inlb,
        torque_nm=s.torque_nm,
        torque_margin=matching.torque_margin(c, filters),
        duty_cycle_pct=s.duty_cycle_pct,
        speed_s=None if rating is None else rating.speed_s,
        speed_frequency_hz=None if rating is None else rating.frequency_hz,
        warnings=a.warnings,
        meets_requirements=unmet is None,
        unmet=[unmet] if unmet else [],
    )


# --- list_catalog_options -------------------------------------------------------------------


async def list_catalog_options(session: AsyncSession, args: CatalogOptionsArgs) -> CatalogOptions:
    specs = [to_spec(a) for a in await catalog.list_actuators(session)]
    with_data = [s for s in specs if s.data_available]
    wanted = args.field
    options = CatalogOptions()
    if wanted in (None, "enclosure"):
        options.enclosures = sorted({s.enclosure for s in specs})
    if wanted in (None, "power_supply"):
        options.power_supplies = _power_supplies(specs)
    if wanted in (None, "service_type"):
        services = {r.service_type for s in with_data for r in s.service_ratings}
        options.service_types = sorted(services, key=matching.SERVICE_ORDER.__getitem__)
    if wanted in (None, "series_size"):
        options.series_sizes = sorted({s.series_size for s in specs})
    if wanted in (None, "torque"):
        options.torque_ranges = _torque_ranges(with_data)
    return options


def _power_supplies(specs: Sequence[ActuatorSpec]) -> list[PowerSupplyOption]:
    groups: dict[tuple, list[ActuatorSpec]] = {}
    for s in specs:
        groups.setdefault((s.enclosure, s.voltage_v, s.phase, s.current_type), []).append(s)
    return [
        PowerSupplyOption(
            enclosure=enclosure,
            voltage_v=voltage,
            phase=phase,
            current_type=current,
            service_types=sorted(
                {r.service_type for s in group for r in s.service_ratings},
                key=matching.SERVICE_ORDER.__getitem__,
            ),
            frequencies_hz=sorted(
                {e.frequency_hz for s in group for e in s.electrical_ratings} - {None}
            ),
        )
        for (enclosure, voltage, phase, current), group in sorted(
            groups.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] or 0, item[0][3])
        )
    ]


def _torque_ranges(specs: Sequence[ActuatorSpec]) -> list[TorqueRange]:
    groups: dict[tuple, list] = {}
    for s in specs:
        for r in s.service_ratings:
            if r.torque_inlb is not None and r.torque_nm is not None:
                groups.setdefault((s.enclosure, r.service_type), []).append(r)
    return [
        TorqueRange(
            enclosure=enclosure,
            service_type=service,
            min_inlb=min(r.torque_inlb for r in rows),
            max_inlb=max(r.torque_inlb for r in rows),
            min_nm=min(r.torque_nm for r in rows),
            max_nm=max(r.torque_nm for r in rows),
        )
        for (enclosure, service), rows in sorted(
            groups.items(), key=lambda item: (item[0][0], matching.SERVICE_ORDER[item[0][1]])
        )
    ]
