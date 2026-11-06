"""Numeric validator, pure logic. Values are synthetic: they test notation handling, not the
catalog."""

from decimal import Decimal

import pytest

from app.agent.validator import extract_numbers, numbers_in, strip_numbers, unsupported_numbers

RESULT = {
    "actuator": {
        "part_number": "763B00-11320000/A",
        "motor_power_w": 1234,
        "service_ratings": [{"torque_inlb": 4321, "torque_nm": 488}],
        "electrical_ratings": [{"frequency_hz": 60, "fla_a": "2.30", "speed_s": 17}],
    },
    "applied_filters": {"voltage_v": 230, "csa_required": True},
}


def _texts(answer: str) -> list[str]:
    return [t.text for t in unsupported_numbers(answer, [RESULT])]


@pytest.mark.parametrize(
    "answer",
    [
        "Torque 4,321 in-lb (488 Nm).",  # English thousands separator
        "Torque de 4.321 in-lb y 488 Nm.",  # Spanish thousands separator
        "Consume 1234 W y 2,3 A a 60 Hz.",  # Spanish decimal comma; "2.30" in the result
        "FLA 2.3 A, recorre en 17 s.",
        "230 V está dentro del ±10 % de la tabla; frecuencia ±5 %.",  # allowlist + applied filter
        "El tamaño 6 y el 7 no tienen CSA.",  # series sizes
        "El 763B00-11320000/A de la Serie 76 cumple.",  # identifiers are not values
        "Las opciones 763C y 765Y30-11300000/A.",
    ],
)
def test_backed_numbers_pass(answer) -> None:
    assert _texts(answer) == []


def test_unbacked_numbers_are_reported() -> None:
    assert _texts("Da 9,999 in-lb y unos 3.7 A, en 8 s.") == ["9,999", "3.7", "8"]


def test_session_results_count_too() -> None:
    earlier = {"actuator": {"motor_power_w": 999}}
    assert unsupported_numbers("Consume 999 W.", [RESULT, earlier]) == []


def test_numbers_in_reads_decimal_strings_and_skips_booleans_and_text() -> None:
    found = numbers_in([{"a": "3.30", "b": True, "c": "763B00-11320000/A", "d": [7, 1.5]}])
    assert found == {Decimal("3.3"), Decimal(7), Decimal("1.5")}


def test_ambiguous_notation_yields_both_readings() -> None:
    (token,) = extract_numbers("1,500")
    assert token.readings == {Decimal(1500), Decimal("1.5")}


def test_strip_removes_number_with_its_unit() -> None:
    answer = "Da 9,999 in-lb y 488 Nm."
    stripped = strip_numbers(answer, unsupported_numbers(answer, [RESULT]))
    assert stripped == "Da — y 488 Nm."
    assert unsupported_numbers(stripped, [RESULT]) == []
