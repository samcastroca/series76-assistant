"""Builds what the app renders from this turn's tool results: `products`, `response_type`,
`clarification`, `highlight_fields`, `suggestions` and `warnings`. Deterministic; the LLM's text
is never parsed for specs. Only the answer text itself is reused, as the clarification question."""

from dataclasses import dataclass, field

from app.agent.recorder import RecordedCall
from app.schemas.catalog import (
    ActuatorSpec,
    CatalogOptions,
    FilterName,
    GetActuatorResult,
    Match,
    SearchResult,
)
from app.schemas.conversation import (
    Clarification,
    HighlightField,
    PowerSupply,
    Product,
    ProductElectrical,
    ProductService,
    ResponseType,
    Source,
    Torque,
    TurnWarning,
)

_HIGHLIGHT: dict[FilterName, HighlightField] = {
    "min_torque": "torque",
    "max_speed_s": "speed_s",
    "min_duty_cycle_pct": "duty_cycle_pct",
    "voltage_v": "power_supply",
    "phase": "power_supply",
    "current_type": "power_supply",
    "csa_required": "certifications",
    "enclosure": "enclosure",
    "series_size": "series_size",
    "service_type": "service",
}


@dataclass
class TurnView:
    response_type: ResponseType
    products: list[Product] = field(default_factory=list)
    highlight_fields: list[HighlightField] = field(default_factory=list)
    clarification: Clarification | None = None
    suggestions: list[str] = field(default_factory=list)
    warnings: list[TurnWarning] = field(default_factory=list)


def to_product(spec: ActuatorSpec, match: Match | None = None) -> Product:
    return Product(
        part_number=spec.part_number,
        series_size=spec.series_size,
        variant=spec.variant,
        enclosure=spec.enclosure,
        power_supply=PowerSupply(
            voltage_v=spec.voltage_v, phase=spec.phase, current_type=spec.current_type
        ),
        certifications=spec.certifications,
        csa_certified=spec.csa_certified,
        hazardous_locations=spec.hazardous_locations,
        data_available=spec.data_available,
        service=[
            ProductService(
                type=s.service_type,
                torque=Torque(in_lb=s.torque_inlb, nm=s.torque_nm),
                duty_cycle_pct=s.duty_cycle_pct,
                cycles_per_hour=s.cycles_per_hour,
                starts_per_hour=s.starts_per_hour,
            )
            for s in spec.service_ratings
        ],
        electrical=[
            ProductElectrical(
                frequency_hz=e.frequency_hz,
                speed_s=e.speed_s,
                fla_a=None if e.fla_a is None else float(e.fla_a),
                lra_a=None if e.lra_a is None else float(e.lra_a),
            )
            for e in spec.electrical_ratings
        ],
        motor_power_w=spec.motor_power_w,
        notes=list(spec.warnings),
        source=Source(table=spec.source_table, page=spec.source_page),
        matched_service=None if match is None else match.service_type,
        meets_requirements=True if match is None else match.meets_requirements,
        unmet=[] if match is None else list(match.unmet),
    )


def build_view(calls: list[RecordedCall], answer: str) -> TurnView:
    ok = [c for c in calls if c.result is not None]
    searches = [c for c in ok if isinstance(c.result, SearchResult)]
    lookups = [c.result for c in ok if isinstance(c.result, GetActuatorResult)]
    options = [c.result for c in ok if isinstance(c.result, CatalogOptions)]

    if searches:
        view = _search_view(searches[-1])
    elif lookups:
        view = _lookup_view(lookups, answer)
    elif options:
        view = TurnView("clarification", clarification=_options_clarification(options[-1], answer))
    else:
        view = TurnView("error" if calls else "clarification")

    if any(c.result is None for c in calls):
        view.warnings.append("tool_error")
    for warning in dict.fromkeys(w for p in view.products for w in p.notes):
        if warning in ("service_type_unspecified", "not_csa_certified", "no_published_data"):
            view.warnings.append(warning)
    return view


def _search_view(call: RecordedCall) -> TurnView:
    result: SearchResult = call.result  # type: ignore[assignment]
    specs = {s.part_number: s for s in call.specs}
    found = bool(result.matches)
    shown = result.matches if found else result.closest_alternatives
    products = _dedupe(to_product(specs[m.part_number], m) for m in shown if m.part_number in specs)
    applied = result.applied_filters.model_dump(exclude_defaults=True)
    view = TurnView(
        "recommendation" if found else "no_match",
        products=products,
        highlight_fields=_highlights(k for k in applied if k in _HIGHLIGHT),
    )
    if not found:
        view.suggestions = [p.part_number for p in products]
        blocking = [f for b in result.blocking_filters for f in b.filters]
        view.highlight_fields = _highlights(blocking) or view.highlight_fields
    if result.tolerance_applied:
        view.warnings.append("tolerance_applied")
    if result.multiple_filters_blocking:
        view.warnings.append("multiple_filters_blocking")
    return view


def _lookup_view(results: list[GetActuatorResult], answer: str) -> TurnView:
    found = [r.actuator for r in results if r.status == "found" and r.actuator]
    if found:
        products = _dedupe(to_product(a) for a in found)
        return TurnView("comparison" if len(products) > 1 else "detail", products=products)
    last = results[-1]
    if last.status == "partial_match":
        options = [c.part_number for c in last.candidates]
        clarification = Clarification(question=answer, options=options)
        return TurnView("clarification", clarification=clarification)
    if last.status == "no_data" and last.actuator:
        return TurnView("no_data", products=[to_product(last.actuator)])
    return TurnView("not_found", suggestions=[s.part_number for s in last.suggestions])


def _options_clarification(options: CatalogOptions, answer: str) -> Clarification:
    labels: list[str] = []
    if options.power_supplies:
        for p in options.power_supplies:
            phase = "" if p.phase is None else f" {p.phase}ph"
            labels.append(f"{p.voltage_v} V{phase} {p.current_type} ({p.enclosure})")
    for values in (options.enclosures, options.service_types):
        labels.extend(values or [])
    return Clarification(question=answer, options=list(dict.fromkeys(labels)))


def _dedupe(products) -> list[Product]:
    seen: dict[str, Product] = {}
    for p in products:
        seen.setdefault(p.part_number, p)
    return list(seen.values())


def _highlights(filters) -> list[HighlightField]:
    return list(dict.fromkeys(_HIGHLIGHT[f] for f in filters))
