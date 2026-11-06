# ADR 0003: Search logic in Python over loaded rows

Date: 2026-11-05 · Status: accepted

## Context

`search_actuators` needs voltage tolerance, torque in the user's unit, ranking by margin and,
when nothing matches, relaxing one filter at a time to report which one blocked. The catalog has
68 part numbers (64 with data). The LLM must not filter, rank or compute.

## Decision

- The repository (`app/db/catalog.py`) only fetches rows with their ratings, through parameterized
  SQLAlchemy queries: exact `part_key`, prefix (`LIKE` with escaping) and `pg_trgm` similarity.
- Filtering, ranking and relaxation are pure functions in `app/tools/matching.py` over the loaded
  rows. Relaxing a filter is re-filtering in memory, and the logic is unit-tested without Postgres.
- The unit of search is an (actuator, service type) pair, because torque depends on the service.
- Rules:
  - Voltage: the catalog voltage within ±10 % (table note) of the requested one;
    `tolerance_applied` when they differ. Nominal voltages come from the database.
  - Current: an `AC/DC` supply serves both `AC` and `DC` requests.
  - Service: `on_off`/`modulating` never match the 3-phase `unspecified` rows; those rows always
    carry the `service_type_unspecified` warning.
  - Torque: compared in the column of the user's unit; never converted.
  - Speed: at `frequency_hz`, or the slowest published speed when no frequency is given;
    `speed_frequency_hz` says which. `frequency_hz` only picks the speed row; it filters nothing.
  - Torque requests may be fractional (`Decimal`), so the LLM never rounds the user's number.
  - Ranking: smallest torque margin, then speed, then part number. Without a torque request,
    smallest in-lb torque first.
  - `get_actuator` returns `found` for a unique prefix match too, with `matched_by = "prefix"`.
  - Relaxation order: torque, speed, duty, size, service, current, phase, voltage, CSA,
    enclosure. Closest alternatives are taken round-robin from each blocking filter, so each one
    shows an option; with torque relaxed, the smallest shortfall comes first.
- `search_actuators` gains `series_size` (needed for "size 7 with CSA").
- The `v_actuator_specs` view announced in ADR 0001 is not created: the ORM loads the three tables
  directly.

## Consequences

- Searching loads every row with data on each call. Fine for 68 rows; at thousands of rows the hard
  filters (enclosure, voltage, phase) would move into the SQL `WHERE` and relaxation would re-query.
- Two filters blocking together are reported as `multiple_filters_blocking` with no alternatives.
