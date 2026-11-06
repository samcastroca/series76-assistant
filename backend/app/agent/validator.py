"""Numeric validator: every number in the answer must appear in a tool result of this turn or of
the session, or in a small allowlist. Pure functions, no LLM.

A printed number can be read more than one way ("1,500" is 1500 in a catalog and 1.5 in Spanish
decimal notation), so each token yields its readings and is accepted when any reading is backed.
Part numbers ("763B00-11320000/A", "763C") and the product name "Series 76" are masked first:
they are identifiers, not values."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

# Table notes and fixed vocabulary: 50/60 Hz, ±10 % voltage, ±5 % frequency, series sizes 1-7.
ALLOWLIST: frozenset[Decimal] = frozenset(Decimal(v) for v in (50, 60, 10, 5, 1, 2, 3, 4, 5, 6, 7))

PART_NUMBER = re.compile(r"(?<![0-9A-Za-z])76[1-7][A-CXYa-cxy][0-9A-Za-z]*(?:[-/][0-9A-Za-z]+)*")
_PRODUCT_NAME = re.compile(r"\bseries?\s+76\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![0-9A-Za-z])\d+(?:[.,]\d+)*")
_UNIT = re.compile(
    r"\s?(?:in-lb|lb-in|in·lb|N·m|Nm|Hz|V|A|W|s|%|seg(?:undos)?|seconds?)(?![0-9A-Za-z])"
)


@dataclass(frozen=True)
class NumberToken:
    text: str
    start: int
    end: int  # includes an attached unit, so stripping removes "1,500 in-lb" as a whole
    readings: frozenset[Decimal]


def _mask(text: str) -> str:
    for pattern in (PART_NUMBER, _PRODUCT_NAME):
        text = pattern.sub(lambda m: " " * len(m.group()), text)
    return text


def _readings(token: str) -> frozenset[Decimal]:
    """Plausible values of a printed number in English or Spanish notation."""
    out: set[Decimal] = set()
    for thousands, decimal in ((",", "."), (".", ",")):
        parts = token.split(decimal)
        if len(parts) > 2:
            continue
        integer = parts[0]
        groups = integer.split(thousands)
        if len(groups) > 1 and not all(len(g) == 3 for g in groups[1:]):
            continue
        plain = "".join(groups) + ("." + parts[1] if len(parts) == 2 else "")
        try:
            out.add(Decimal(plain).normalize())
        except InvalidOperation:
            continue
    return frozenset(out)


def extract_numbers(text: str) -> list[NumberToken]:
    masked = _mask(text)
    tokens = []
    for m in _NUMBER.finditer(masked):
        unit = _UNIT.match(masked, m.end())
        end = unit.end() if unit else m.end()
        tokens.append(NumberToken(m.group(), m.start(), end, _readings(m.group())))
    return tokens


def numbers_in(results: Iterable[Any]) -> set[Decimal]:
    """Every numeric value inside tool results (JSON-mode dumps: Decimals arrive as strings)."""
    found: set[Decimal] = set()

    def walk(value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, int | float | Decimal):
            found.add(Decimal(str(value)).normalize())
        elif isinstance(value, str):
            try:
                found.add(Decimal(value).normalize())
            except InvalidOperation:
                return
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list | tuple):
            for v in value:
                walk(v)

    for result in results:
        walk(result)
    return found


def unsupported_numbers(answer: str, results: Iterable[Any]) -> list[NumberToken]:
    allowed = numbers_in(results) | ALLOWLIST
    return [t for t in extract_numbers(answer) if not (t.readings & allowed)]


def strip_numbers(answer: str, tokens: Iterable[NumberToken]) -> str:
    """Remove the given numbers (with their units) from the answer."""
    out = answer
    for t in sorted(tokens, key=lambda t: t.start, reverse=True):
        out = out[: t.start] + "—" + out[t.end :]
    return out
