"""Deterministic search logic: voltage tolerance, filters, ranking and filter relaxation. Pure
functions over ActuatorSpec objects; no database and no LLM."""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import combinations

from app.schemas.catalog import (
    ActuatorSpec,
    ElectricalSpec,
    FilterName,
    SearchFilters,
    ServiceSpec,
    TorqueUnit,
    Warning,
)

# Table note: "Voltage tolerance ±10 %". A table note, not a catalog value.
VOLTAGE_TOLERANCE_PCT = 10

# Relaxation order: performance requirements first, hard constraints (certification,
# hazardous-area enclosure) last. Closest alternatives follow this order.
RELAXATION_ORDER: tuple[FilterName, ...] = (
    "min_torque",
    "max_speed_s",
    "min_duty_cycle_pct",
    "series_size",
    "service_type",
    "current_type",
    "phase",
    "voltage_v",
    "csa_required",
    "enclosure",
)

SERVICE_ORDER = {"on_off": 0, "modulating": 1, "unspecified": 2}


@dataclass(frozen=True)
class Candidate:
    actuator: ActuatorSpec
    service: ServiceSpec


Relaxation = tuple[FilterName, ...]


@dataclass
class SearchOutcome:
    matches: list[Candidate]
    blocking: list[tuple[Relaxation, int]] = field(default_factory=list)
    alternatives: list[tuple[Candidate, Relaxation]] = field(default_factory=list)
    multiple_blocking: bool = False


Predicate = Callable[[Candidate], bool]


def actuator_warnings(actuator: ActuatorSpec) -> list[Warning]:
    warnings: list[Warning] = []
    if not actuator.data_available:
        warnings.append("no_published_data")
    if any(s.service_type == "unspecified" for s in actuator.service_ratings):
        warnings.append("service_type_unspecified")
    if not actuator.csa_certified:
        warnings.append("not_csa_certified")
    return warnings


def nominal_voltage(requested: int, nominals: Iterable[int]) -> int | None:
    """The catalog voltage within ±10 % of the requested one (the nearest if several)."""
    within = [n for n in set(nominals) if abs(requested - n) * 100 <= VOLTAGE_TOLERANCE_PCT * n]
    return min(within, key=lambda n: (abs(requested - n), n), default=None)


def current_matches(requested: str, available: str) -> bool:
    """An AC/DC actuator serves both AC and DC supplies."""
    return available in (requested, "AC/DC")


def torque(service: ServiceSpec, unit: TorqueUnit) -> int | None:
    """Torque in the user's unit as printed; Nm is rounded in the source, so never converted."""
    return service.torque_inlb if unit == "in_lb" else service.torque_nm


def speed_rating(actuator: ActuatorSpec, frequency_hz: int | None) -> ElectricalSpec | None:
    """The electrical row whose speed is used: the one at the requested frequency (24 V rows have
    a single, frequency-less value). Without a frequency, the slowest published one, so a speed
    limit holds at 50 and 60 Hz."""
    rows = [
        e
        for e in actuator.electrical_ratings
        if e.speed_s is not None
        and (frequency_hz is None or e.frequency_hz in (frequency_hz, None))
    ]
    return max(rows, key=lambda e: e.speed_s, default=None)


def speed_s(actuator: ActuatorSpec, frequency_hz: int | None) -> int | None:
    rating = speed_rating(actuator, frequency_hz)
    return None if rating is None else rating.speed_s


def torque_margin(candidate: Candidate, filters: SearchFilters) -> Decimal | None:
    if filters.min_torque is None or filters.torque_unit is None:
        return None
    value = torque(candidate.service, filters.torque_unit)
    return None if value is None else Decimal(value) - filters.min_torque


def candidates(actuators: Iterable[ActuatorSpec]) -> list[Candidate]:
    return [Candidate(a, s) for a in actuators if a.data_available for s in a.service_ratings]


def build_predicates(filters: SearchFilters, nominal: int | None) -> dict[FilterName, Predicate]:
    """One predicate per active filter, in RELAXATION_ORDER. A missing value never passes."""
    f = filters
    available: dict[FilterName, Predicate | None] = {
        "min_torque": (lambda c: _at_least(torque_margin(c, f), 0))
        if f.min_torque is not None
        else None,
        "max_speed_s": (lambda c: _at_most(speed_s(c.actuator, f.frequency_hz), f.max_speed_s))
        if f.max_speed_s is not None
        else None,
        "min_duty_cycle_pct": (lambda c: _at_least(c.service.duty_cycle_pct, f.min_duty_cycle_pct))
        if f.min_duty_cycle_pct is not None
        else None,
        "series_size": (lambda c: c.actuator.series_size == f.series_size)
        if f.series_size is not None
        else None,
        "service_type": (lambda c: c.service.service_type == f.service_type)
        if f.service_type is not None
        else None,
        "current_type": (lambda c: current_matches(f.current_type, c.actuator.current_type))
        if f.current_type is not None
        else None,
        "phase": (lambda c: c.actuator.phase == f.phase) if f.phase is not None else None,
        "voltage_v": (lambda c: nominal is not None and c.actuator.voltage_v == nominal)
        if f.voltage_v is not None
        else None,
        "csa_required": (lambda c: c.actuator.csa_certified) if f.csa_required else None,
        "enclosure": (lambda c: c.actuator.enclosure == f.enclosure)
        if f.enclosure is not None
        else None,
    }
    return {name: available[name] for name in RELAXATION_ORDER if available[name] is not None}


def rank(
    items: Iterable[Candidate], filters: SearchFilters, relaxed: Relaxation = ()
) -> list[Candidate]:
    """Smallest torque margin over the request first, then fastest, then part number. With
    torque relaxed, smallest shortfall first (the strongest model below the request). Without a
    torque request, smallest in-lb torque first (in-lb is the unrounded column)."""

    def key(c: Candidate) -> tuple:
        if filters.min_torque is not None:
            margin = torque_margin(c, filters)
            primary = None if margin is None else (-margin if "min_torque" in relaxed else margin)
        else:
            primary = c.service.torque_inlb
        speed = speed_s(c.actuator, filters.frequency_hz)
        return (
            primary is None,
            primary or 0,
            speed is None,
            speed or 0,
            c.actuator.part_number,
            SERVICE_ORDER[c.service.service_type],
        )

    return sorted(items, key=key)


def run_search(
    actuators: Sequence[ActuatorSpec], filters: SearchFilters, nominal: int | None
) -> SearchOutcome:
    pool = candidates(actuators)
    predicates = build_predicates(filters, nominal)

    def passing(skip: Relaxation = ()) -> list[Candidate]:
        return [c for c in pool if all(p(c) for n, p in predicates.items() if n not in skip)]

    matches = rank(passing(), filters)
    if matches or not predicates:
        return SearchOutcome(matches=matches[: filters.limit])

    # Nothing matches: find the smallest sets of filters whose removal unblocks the search, one
    # filter at a time first, then pairs, and so on. Because the sets are minimal, every
    # alternative fails exactly the filters of its set.
    for size in range(1, len(predicates) + 1):
        relaxed = {
            skip: found
            for skip in combinations(predicates, size)
            if (found := rank(passing(skip), filters, skip))
        }
        if relaxed:
            return SearchOutcome(
                matches=[],
                blocking=[(skip, len(found)) for skip, found in relaxed.items()],
                alternatives=_round_robin(relaxed, filters.limit),
                multiple_blocking=size > 1,
            )
    return SearchOutcome(matches=[])  # nothing with data in the catalog


def _round_robin(
    relaxed: dict[Relaxation, list[Candidate]], limit: int
) -> list[tuple[Candidate, Relaxation]]:
    """Take the best of each relaxation in turn, so every blocking set shows an alternative."""
    queues = [[(c, skip) for c in found] for skip, found in relaxed.items()]
    picked: list[tuple[Candidate, Relaxation]] = []
    depth = 0
    while len(picked) < limit and any(depth < len(q) for q in queues):
        for queue in queues:
            if depth < len(queue) and len(picked) < limit:
                picked.append(queue[depth])
        depth += 1
    return picked


def _at_most(value: int | None, limit: int) -> bool:
    return value is not None and value <= limit


def _at_least(value: int | None, minimum: int) -> bool:
    return value is not None and value >= minimum
