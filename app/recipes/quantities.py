"""Lokale Mengen aus Rezepttext; Dezimalzahlen, deutsche Tausender und Brüche."""
from __future__ import annotations

import re
from fractions import Fraction

FRACTIONS = {"¼": .25, "½": .5, "¾": .75, "⅓": 1 / 3, "⅔": 2 / 3,
             "⅛": .125, "⅜": .375, "⅝": .625, "⅞": .875}
NUMBER_PATTERN = (
    r"(?:\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d*\s*[¼½¾⅓⅔⅛⅜⅝⅞]|"
    r"[1-9]\d{0,2}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)"
)
AMOUNT_PATTERN = rf"{NUMBER_PATTERN}(?:\s*[-–]\s*{NUMBER_PATTERN})?"


def parse_amount(raw: str | None) -> float | None:
    if not raw:
        return None
    value = re.split(r"[-–]", raw.strip(), maxsplit=1)[0].strip()
    if not re.fullmatch(NUMBER_PATTERN, value):
        return None
    try:
        if value[-1] in FRACTIONS:
            return float(value[:-1].strip() or 0) + FRACTIONS[value[-1]]
        if "/" in value:
            compact = re.sub(r"\s*/\s*", "/", value)
            parts = compact.split()
            return float(Fraction(parts[-1])) + (int(parts[0]) if len(parts) == 2 else 0)
        if re.fullmatch(r"[1-9]\d{0,2}(?:\.\d{3})+(?:,\d+)?", value):
            value = value.replace(".", "")
        return float(value.replace(",", "."))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
