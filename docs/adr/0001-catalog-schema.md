# ADR 0001: Catalog schema

Date: 2026-11-04 · Status: accepted

## Context

The data PDF has 7 tables. Torque depends on the service type (on/off vs modulating; one
`unspecified` torque in the 3-phase table), and speed, FLA and LRA depend on the frequency (60/50
Hz on AC tables, a single value on 24 V tables). Four 3-phase rows are printed as N/A.

## Decision

- Three normalized tables: `actuators` (one row per part number), `service_ratings`
  (PK `part_number, service_type`) and `electrical_ratings`.
- `electrical_ratings` gets a surrogate `id` and `UNIQUE NULLS NOT DISTINCT (part_number,
  frequency_hz)`. `frequency_hz` is `NULL` on 24 V tables, so it cannot be part of a primary key,
  and storing `0` would break the "NULL means not published" rule. Requires PostgreSQL 15+.
- Closed sets (enclosure, variant, voltage, phase, current type, service type, frequency) are
  `CHECK` constraints, not Postgres `ENUM` types: they are easier to change in a migration.
- N/A rows exist only in `actuators` (`data_available = false`, `motor_power_w = NULL`), with no
  child rows. A `LEFT JOIN` yields all specs as `NULL`.
- Hazardous-location classes of the explosionproof tables live in `actuators.hazardous_locations`.
- `pg_trgm` and a GIN trigram index on `part_key` are created in the first migration.

## Consequences

- Queries go through a view or joins; the flat `v_actuator_specs` view comes with the tools.
  (Amended by ADR 0003: no view; the ORM loads the three tables.)
- `alembic check` in the test suite keeps the models and the migrations in sync.
