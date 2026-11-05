"""Cross-checks over the whole built catalog. They catch typical extraction misreads (a dropped
or extra row, a wrong digit, a factor-10 error) that per-record validation cannot see."""

from collections import Counter

from ingest.records import ActuatorRecord
from ingest.spec import Spec

INLB_PER_NM = 8.85
# Printed Nm values are rounded; some pairs deviate 2-4 % from in-lb / 8.85.
NM_TOLERANCE = 0.05


def torque_consistent(torque_inlb: int, torque_nm: int) -> bool:
    expected = torque_inlb / INLB_PER_NM
    return abs(torque_nm - expected) <= NM_TOLERANCE * expected


def check_catalog(spec: Spec, records: list[ActuatorRecord]) -> list[str]:
    """Returns every failed check as a message naming table, part number and field."""
    errors: list[str] = []

    counts = Counter(r.part_number for r in records)
    errors += [f"duplicated part number {pn} ({n} rows)" for pn, n in counts.items() if n > 1]
    expected_total = sum(t.expected_rows for t in spec.tables)
    if len(counts) != expected_total:
        errors.append(f"{len(counts)} unique part numbers, expected {expected_total}")

    by_table = Counter(r.source_table for r in records)
    na_by_table = Counter(r.source_table for r in records if not r.data_available)
    for table in spec.tables:
        if by_table[table.id] != table.expected_rows:
            errors.append(f"{table.id}: {by_table[table.id]} rows, expected {table.expected_rows}")
        if na_by_table[table.id] != table.expected_na_rows:
            errors.append(
                f"{table.id}: {na_by_table[table.id]} N/A rows, expected {table.expected_na_rows}"
            )

    for r in records:
        for s in r.service_ratings:
            if s.torque_inlb is not None and s.torque_nm is not None:
                if not torque_consistent(s.torque_inlb, s.torque_nm):
                    errors.append(
                        f"{r.source_table} {r.part_number} {s.service_type}: torque "
                        f"{s.torque_inlb} in-lb vs {s.torque_nm} Nm outside "
                        f"{NM_TOLERANCE:.0%} of factor {INLB_PER_NM}"
                    )
        for e in r.electrical_ratings:
            if e.fla_a is not None and e.lra_a is not None and e.fla_a > e.lra_a:
                errors.append(
                    f"{r.source_table} {r.part_number} {e.frequency_hz or 'single'} Hz: "
                    f"FLA {e.fla_a} > LRA {e.lra_a}"
                )
    return errors
