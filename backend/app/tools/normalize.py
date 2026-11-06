"""Part number normalization shared by every lookup."""

import re

_NOT_ALNUM = re.compile(r"[^0-9A-Za-z]")


def normalize_part_key(text: str) -> str:
    """'761a00 11300000 a' and '761A00-11300000/A*' both become '761A0011300000A', the form
    stored in `actuators.part_key`."""
    return _NOT_ALNUM.sub("", text).upper()
