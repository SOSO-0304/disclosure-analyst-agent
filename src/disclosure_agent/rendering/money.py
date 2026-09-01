"""Deterministic display formatting for Korean won amounts."""

from __future__ import annotations

_KOREAN_LARGE_UNITS = ("", "만", "억", "조", "경")


def format_krw(amount: int | None) -> str:
    """Format an integer KRW amount with Korean 10,000-based large units."""

    if amount is None:
        return "확인되지 않음"
    if amount == 0:
        return "0원"

    sign = "-" if amount < 0 else ""
    value = abs(amount)
    parts: list[str] = []
    unit_index = 0

    while value:
        group = value % 10_000
        if group:
            unit = _KOREAN_LARGE_UNITS[unit_index]
            parts.append(f"{group:,}{unit}")
        value //= 10_000
        unit_index += 1
        if value and unit_index >= len(_KOREAN_LARGE_UNITS):
            return f"{amount:,}원"

    return f"{sign}{' '.join(reversed(parts))} 원"
