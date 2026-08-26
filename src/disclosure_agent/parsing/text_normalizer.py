"""Text conversion helpers that never overwrite the raw source value."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

_WHITESPACE = re.compile(r"\s+")
_PLAIN_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


def normalize_text(value: str | None) -> str | None:
    """Return NFC text with whitespace collapsed, leaving raw text untouched."""

    if value is None:
        return None
    normalized = unicodedata.normalize("NFC", _WHITESPACE.sub(" ", value).strip())
    return normalized or None


def optional_str(value: Any) -> str | None:
    """Convert a manifest value into a meaningful optional string."""

    if value is None:
        return None
    result = normalize_text(str(value))
    if result is None or result.lower() == "nan":
        return None
    return result


def optional_int(value: Any) -> int | None:
    """Convert a manifest value into an integer without raising."""

    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_date(value: str | date | None) -> date | None:
    """Parse the date representations used by the corpus."""

    if value is None or isinstance(value, date):
        return value
    digits = re.sub(r"\D", "", str(value))
    if len(digits) != 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
    except ValueError:
        return None


def parse_bool_attribute(value: str | None) -> bool | None:
    """Parse common DART boolean attribute representations."""

    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1", "y", "yes"}:
        return True
    if normalized in {"false", "0", "n", "no"}:
        return False
    return None


def parse_decimal(value: str | None, *, negated: bool | None = None) -> Decimal | None:
    """Conservatively parse a cell containing only one numeric value."""

    normalized = normalize_text(value)
    if normalized is None or normalized in {"-", "–", "—"}:
        return None

    candidate = normalized.replace(",", "").replace(" ", "")
    parenthesized = candidate.startswith("(") and candidate.endswith(")")
    if parenthesized:
        candidate = candidate[1:-1]
    if not _PLAIN_NUMBER.fullmatch(candidate):
        return None

    try:
        number = Decimal(candidate)
    except InvalidOperation:
        return None
    if parenthesized or negated is True:
        number = -abs(number)
    return number
