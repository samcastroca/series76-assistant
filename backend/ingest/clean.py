"""Pure cell cleaners. Each one takes a string as printed in the PDF and returns a typed value or
raises CellError; none of them guesses a value."""

import re
from decimal import Decimal

NA = "N/A"

_WS = re.compile(r"\s+")
_INT = re.compile(r"\d+")
_DOT_THOUSANDS = re.compile(r"\d{1,3}(\.\d{3})+")
_DECIMAL = re.compile(r"\d+(\.\d+)?")


class CellError(ValueError):
    pass


def is_blank(raw: str | None) -> bool:
    return raw is None or raw.strip() == ""


def is_na(raw: str | None) -> bool:
    return raw is not None and _WS.sub("", raw).upper() == NA


def clean_part_number(raw: str, markers: set[str]) -> tuple[str, set[str]]:
    """Joins bold letters split by spaces (`761 A 00-...`) and strips footnote markers.
    Returns the part number and the markers found next to it."""
    text = _WS.sub("", raw)
    found = set()
    for marker in sorted(markers, key=len, reverse=True):
        if marker in text:
            found.add(marker)
            text = text.replace(marker, "")
    if not text:
        raise CellError(f"empty part number in {raw!r}")
    return text.upper(), found


def parse_int(raw: str) -> int:
    """`1,330` and `1.330` (dot as thousands separator) are 1330; anything else non-digit fails."""
    text = _WS.sub("", raw)
    if _DOT_THOUSANDS.fullmatch(text):
        text = text.replace(".", "")
    text = text.replace(",", "")
    if not _INT.fullmatch(text):
        raise CellError(f"not an integer: {raw!r}")
    return int(text)


def parse_decimal(raw: str) -> Decimal:
    text = _WS.sub("", raw)
    if not _DECIMAL.fullmatch(text):
        raise CellError(f"not a decimal: {raw!r}")
    return Decimal(text)


def part_key(part_number: str) -> str:
    """Normalized lookup key: no dashes, spaces or slashes, upper case."""
    return re.sub(r"[-\s/]", "", part_number).upper()
