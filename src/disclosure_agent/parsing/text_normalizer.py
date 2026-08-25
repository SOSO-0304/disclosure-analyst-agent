"""Text normalization utilities."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

_WS = re.compile(r"\s+")


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = _WS.sub(" ", value).strip()
    return value or None


def optional_str(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    if not value or value.lower() == "nan":
        return None
    return value


def optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_date(value: str | date | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            pass
    return None
