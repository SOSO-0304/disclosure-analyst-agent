"""Extract repeatable fundraising events from canonical source-table grids."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class FundraisingInstrument(StrEnum):
    """Fundraising instrument types used by structured retrieval."""

    RIGHTS_ISSUE = "rights_issue"
    CONVERTIBLE_BOND = "convertible_bond"
    BOND_WITH_WARRANTS = "bond_with_warrants"
    EXCHANGEABLE_BOND = "exchangeable_bond"


@dataclass(frozen=True, slots=True)
class FundraisingOccurrence:
    """One source-table occurrence of a fundraising event."""

    filing_id: str
    corp_code: str
    company_name: str
    receipt_date: date
    table_id: str
    row_index: int
    instrument_type: FundraisingInstrument
    issuer_name: str
    issue_date: date | None
    security_name: str | None
    series: str | None
    issuance_method: str | None
    stock_kind: str | None
    share_quantity: int | None
    issue_price_krw: int | None
    amount_krw: int | None
    amount_raw: str | None
    amount_unit: str | None
    evidence_text: str

    @property
    def dedupe_key(self) -> str:
        """Stable identity for collapsing repeated periodic-report disclosures."""

        if self.instrument_type is FundraisingInstrument.RIGHTS_ISSUE:
            identity = (
                self.corp_code,
                _compact(self.issuer_name),
                self.instrument_type.value,
                self.issue_date.isoformat() if self.issue_date else "",
                _compact(self.issuance_method),
                _compact(self.stock_kind),
                str(self.share_quantity or ""),
                str(self.issue_price_krw or ""),
            )
        else:
            series_key = _series_key(self.series, self.security_name)
            identity = (
                self.corp_code,
                _compact(self.issuer_name),
                self.instrument_type.value,
                self.issue_date.isoformat() if self.issue_date else "",
                series_key,
            )
            if not self.issue_date and not series_key:
                if self.amount_krw is not None:
                    amount_key = str(self.amount_krw)
                else:
                    amount_key = self.amount_raw or ""
                identity = (*identity, _compact(amount_key))
        digest = hashlib.sha256("\x1f".join(identity).encode()).hexdigest()[:32]
        return f"fundraising:{digest}"


@dataclass(frozen=True, slots=True)
class FundraisingEvent:
    """Canonical fundraising event with repeated source occurrences collapsed."""

    event_id: str
    occurrence: FundraisingOccurrence
    source_count: int
    source_filing_ids: tuple[str, ...]


DATE_PATTERN = re.compile(
    r"(?P<year>20\d{2})\s*(?:년|[./-])\s*(?P<month>\d{1,2})\s*"
    r"(?:월|[./-])\s*(?P<day>\d{1,2})"
)
INTEGER_PATTERN = re.compile(r"[-+]?\d[\d,]*")
UNIT_MULTIPLIERS = (
    ("억원", 100_000_000),
    ("백만원", 1_000_000),
    ("천원", 1_000),
    ("원", 1),
)
INSTRUMENT_TERMS = (
    (FundraisingInstrument.BOND_WITH_WARRANTS, "신주인수권부사채"),
    (FundraisingInstrument.CONVERTIBLE_BOND, "전환사채"),
    (FundraisingInstrument.EXCHANGEABLE_BOND, "교환사채"),
)


def extract_fundraising_occurrences(
    *,
    filing_id: str,
    corp_code: str,
    company_name: str,
    receipt_date: date,
    table_id: str,
    grid: dict[str, Any],
    normalized_text: str,
) -> tuple[FundraisingOccurrence, ...]:
    """Extract fundraising occurrences from one source-table grid."""

    cells = _cells(grid)
    header_rows = _header_rows(grid)
    logical_rows = _logical_rows(cells)
    grid_text = _all_cell_text(cells)
    context_text = f"{normalized_text} {grid_text}".strip()
    occurrences: list[FundraisingOccurrence] = []

    if _is_share_issuance_matrix(cells, header_rows):
        occurrences.extend(
            _extract_rights_issue_rows(
                filing_id=filing_id,
                corp_code=corp_code,
                company_name=company_name,
                receipt_date=receipt_date,
                table_id=table_id,
                cells=cells,
                header_rows=header_rows,
                logical_rows=logical_rows,
            )
        )

    if _is_bond_issuance_matrix(cells, header_rows):
        occurrences.extend(
            _extract_bond_matrix_rows(
                filing_id=filing_id,
                corp_code=corp_code,
                company_name=company_name,
                receipt_date=receipt_date,
                table_id=table_id,
                cells=cells,
                header_rows=header_rows,
                logical_rows=logical_rows,
                context_text=context_text,
            )
        )
    else:
        vertical = _extract_vertical_bond(
            filing_id=filing_id,
            corp_code=corp_code,
            company_name=company_name,
            receipt_date=receipt_date,
            table_id=table_id,
            logical_rows=logical_rows,
            context_text=context_text,
        )
        if vertical is not None:
            occurrences.append(vertical)

    return tuple(occurrences)


def canonicalize_fundraising_occurrences(
    occurrences: Iterable[FundraisingOccurrence],
) -> tuple[FundraisingEvent, ...]:
    """Collapse the same economic event repeated across periodic reports."""

    grouped: dict[str, list[FundraisingOccurrence]] = {}
    for occurrence in occurrences:
        grouped.setdefault(occurrence.dedupe_key, []).append(occurrence)

    events = []
    for event_id, sources in grouped.items():
        representative = max(sources, key=lambda item: (item.receipt_date, item.filing_id))
        filing_ids = tuple(sorted({item.filing_id for item in sources}))
        events.append(
            FundraisingEvent(
                event_id=event_id,
                occurrence=representative,
                source_count=len(sources),
                source_filing_ids=filing_ids,
            )
        )
    return tuple(
        sorted(
            events,
            key=lambda event: (
                event.occurrence.company_name,
                event.occurrence.issue_date or date.min,
                event.occurrence.instrument_type.value,
                event.event_id,
            ),
        )
    )


def _extract_rights_issue_rows(
    *,
    filing_id: str,
    corp_code: str,
    company_name: str,
    receipt_date: date,
    table_id: str,
    cells: tuple[dict[str, Any], ...],
    header_rows: frozenset[int],
    logical_rows: dict[int, tuple[dict[str, Any], ...]],
) -> tuple[FundraisingOccurrence, ...]:
    occurrences = []
    for row_index, row in logical_rows.items():
        method_cell = _cell_for_header(row, cells, header_rows, required=("발행감소형태",))
        method = _cell_text(method_cell)
        if "유상증자" not in method:
            continue

        date_cell = _cell_for_header(row, cells, header_rows, required=("주식발행감소일자",))
        kind_cell = _cell_for_header(
            row,
            cells,
            header_rows,
            required=("발행감소한주식의내용", "종류"),
        )
        quantity_cell = _cell_for_header(row, cells, header_rows, required=("수량",))
        price_cell = _cell_for_header(
            row,
            cells,
            header_rows,
            required=("주당발행감소가액",),
        )
        issuer_cell = _cell_for_header(row, cells, header_rows, required=("발행회사",))
        issue_date = _parse_date(_cell_text(date_cell))
        quantity = _int_value(quantity_cell)
        price = _int_value(price_cell)
        amount = quantity * price if quantity is not None and price is not None else None
        evidence = _row_text(row)
        occurrences.append(
            FundraisingOccurrence(
                filing_id=filing_id,
                corp_code=corp_code,
                company_name=company_name,
                receipt_date=receipt_date,
                table_id=table_id,
                row_index=row_index,
                instrument_type=FundraisingInstrument.RIGHTS_ISSUE,
                issuer_name=_cell_text(issuer_cell) or company_name,
                issue_date=issue_date,
                security_name=None,
                series=None,
                issuance_method=method or None,
                stock_kind=_cell_text(kind_cell) or None,
                share_quantity=quantity,
                issue_price_krw=price,
                amount_krw=amount,
                amount_raw=_cell_text(price_cell) or None,
                amount_unit="원" if amount is not None else None,
                evidence_text=evidence,
            )
        )
    return tuple(occurrences)


def _extract_bond_matrix_rows(
    *,
    filing_id: str,
    corp_code: str,
    company_name: str,
    receipt_date: date,
    table_id: str,
    cells: tuple[dict[str, Any], ...],
    header_rows: frozenset[int],
    logical_rows: dict[int, tuple[dict[str, Any], ...]],
    context_text: str,
) -> tuple[FundraisingOccurrence, ...]:
    occurrences = []
    for row_index, row in logical_rows.items():
        security_cell = _cell_for_header(row, cells, header_rows, required=("종류구분",))
        security_name = _cell_text(security_cell)
        instrument = _instrument_from_text(security_name)
        if instrument is None:
            continue

        issue_cell = _cell_for_header(row, cells, header_rows, required=("발행일",))
        amount_cell = _bond_original_amount_cell(row, cells, header_rows)
        series_cell = _cell_for_header(row, cells, header_rows, required=("회차",))
        issuer_cell = _cell_for_header(row, cells, header_rows, required=("발행회사",))
        method_cell = _cell_for_header(row, cells, header_rows, required=("발행방법",))
        note_cell = _cell_for_header(row, cells, header_rows, required=("비고",))
        amount, unit = _amount_krw(amount_cell, context_text)
        method = _cell_text(method_cell)
        note = _cell_text(note_cell)
        if not method and ("사모" in note or "공모" in note):
            method = note
        issuer_name = _cell_text(issuer_cell) or company_name
        occurrences.append(
            FundraisingOccurrence(
                filing_id=filing_id,
                corp_code=corp_code,
                company_name=company_name,
                receipt_date=receipt_date,
                table_id=table_id,
                row_index=row_index,
                instrument_type=instrument,
                issuer_name=issuer_name,
                issue_date=_parse_date(_cell_text(issue_cell)),
                security_name=security_name or None,
                series=_cell_text(series_cell) or None,
                issuance_method=method or None,
                stock_kind=None,
                share_quantity=None,
                issue_price_krw=None,
                amount_krw=amount,
                amount_raw=_cell_text(amount_cell) or None,
                amount_unit=unit,
                evidence_text=_row_text(row),
            )
        )
    return tuple(occurrences)


def _extract_vertical_bond(
    *,
    filing_id: str,
    corp_code: str,
    company_name: str,
    receipt_date: date,
    table_id: str,
    logical_rows: dict[int, tuple[dict[str, Any], ...]],
    context_text: str,
) -> FundraisingOccurrence | None:
    field_rows: dict[str, tuple[int, tuple[dict[str, Any], ...]]] = {}
    security_name = None
    instrument = None

    for row_index, row in logical_rows.items():
        values = [_cell_text(cell) for cell in row if _cell_text(cell)]
        if not values:
            continue
        if instrument is None:
            row_text = " ".join(values)
            instrument = _instrument_from_text(row_text)
            if instrument is not None and len(values) >= 2 and len(row_text) <= 160:
                security_name = values[-1]
        field_rows[_compact(values[0])] = (row_index, row)

    if instrument is None:
        return None

    issue_row = _find_field_row(field_rows, ("발행일",))
    amount_row = _find_field_row(field_rows, ("발행금액", "사채의권면총액", "권면총액"))
    method_row = _find_field_row(field_rows, ("발행방법",))
    if amount_row is None or len(amount_row[1]) < 2:
        return None

    amount_cell = _last_value_cell(amount_row[1])
    amount, unit = _amount_krw(amount_cell, context_text)
    issue_date = None
    if issue_row is not None:
        issue_date = _parse_date(_cell_text(_last_value_cell(issue_row[1])))
    method = None
    if method_row is not None:
        method = _cell_text(_last_value_cell(method_row[1])) or None
    evidence_rows = [amount_row]
    if issue_row is not None:
        evidence_rows.append(issue_row)
    evidence_rows.sort(key=lambda item: item[0])
    evidence = " || ".join(_row_text(row) for _index, row in evidence_rows)

    return FundraisingOccurrence(
        filing_id=filing_id,
        corp_code=corp_code,
        company_name=company_name,
        receipt_date=receipt_date,
        table_id=table_id,
        row_index=amount_row[0],
        instrument_type=instrument,
        issuer_name=company_name,
        issue_date=issue_date,
        security_name=security_name,
        series=None,
        issuance_method=method,
        stock_kind=None,
        share_quantity=None,
        issue_price_krw=None,
        amount_krw=amount,
        amount_raw=_cell_text(amount_cell) or None,
        amount_unit=unit,
        evidence_text=evidence,
    )


def _is_share_issuance_matrix(
    cells: tuple[dict[str, Any], ...],
    header_rows: frozenset[int],
) -> bool:
    headers = {_compact(_cell_text(cell)) for cell in cells if _is_header(cell, header_rows)}
    joined = "".join(headers)
    return "주식발행감소일자" in joined and "주당발행감소가액" in joined


def _is_bond_issuance_matrix(
    cells: tuple[dict[str, Any], ...],
    header_rows: frozenset[int],
) -> bool:
    headers = {_compact(_cell_text(cell)) for cell in cells if _is_header(cell, header_rows)}
    joined = "".join(headers)
    has_amount = "권면전자등록총액" in joined or "권면총액" in joined
    return "발행일" in joined and "종류구분" in joined and has_amount


def _bond_original_amount_cell(
    row: tuple[dict[str, Any], ...],
    cells: tuple[dict[str, Any], ...],
    header_rows: frozenset[int],
) -> dict[str, Any] | None:
    candidates = []
    for cell in row:
        header = _header_compact(cell, cells, header_rows)
        has_amount = "권면전자등록총액" in header or "권면총액" in header
        if has_amount and "미상환" not in header and "미행사" not in header:
            candidates.append(cell)
    return candidates[0] if candidates else None


def _cell_for_header(
    row: tuple[dict[str, Any], ...],
    cells: tuple[dict[str, Any], ...],
    header_rows: frozenset[int],
    *,
    required: tuple[str, ...],
) -> dict[str, Any] | None:
    for cell in row:
        header = _header_compact(cell, cells, header_rows)
        if all(token in header for token in required):
            return cell
    return None


def _header_compact(
    value_cell: dict[str, Any],
    cells: tuple[dict[str, Any], ...],
    header_rows: frozenset[int],
) -> str:
    column_index = _int_field(value_cell, "column_index")
    row_index = _int_field(value_cell, "row_index")
    labels = []
    for cell in cells:
        if not _is_header(cell, header_rows):
            continue
        if _int_field(cell, "row_index") >= row_index:
            continue
        start = _int_field(cell, "column_index")
        stop = start + max(_int_field(cell, "column_span", 1), 1)
        if start <= column_index < stop:
            labels.append(_cell_text(cell))
    return _compact(" ".join(labels))


def _logical_rows(
    cells: tuple[dict[str, Any], ...],
) -> dict[int, tuple[dict[str, Any], ...]]:
    rows: dict[int, list[dict[str, Any]]] = {}
    for cell in cells:
        start = _int_field(cell, "row_index")
        row_span = max(_int_field(cell, "row_span", 1), 1)
        for row_index in range(start, start + row_span):
            rows.setdefault(row_index, []).append(cell)
    return {
        row_index: tuple(sorted(row, key=lambda item: _int_field(item, "column_index")))
        for row_index, row in rows.items()
    }


def _cells(grid: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = grid.get("cells", [])
    if not isinstance(raw, list):
        return ()
    return tuple(cell for cell in raw if isinstance(cell, dict))


def _header_rows(grid: dict[str, Any]) -> frozenset[int]:
    raw = grid.get("header_row_indices", [])
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(int(value) for value in raw)


def _is_header(cell: dict[str, Any], header_rows: frozenset[int]) -> bool:
    return bool(cell.get("is_header")) or _int_field(cell, "row_index") in header_rows


def _cell_text(cell: dict[str, Any] | None) -> str:
    if cell is None:
        return ""
    value = cell.get("text_normalized") or cell.get("text_raw") or ""
    return str(value).strip()


def _row_text(row: tuple[dict[str, Any], ...]) -> str:
    return " | ".join(value for value in (_cell_text(cell) for cell in row) if value)


def _all_cell_text(cells: tuple[dict[str, Any], ...]) -> str:
    return " ".join(value for value in (_cell_text(cell) for cell in cells) if value)


def _int_field(cell: dict[str, Any], key: str, default: int = 0) -> int:
    value = cell.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _int_value(cell: dict[str, Any] | None) -> int | None:
    if cell is None:
        return None
    numeric = cell.get("numeric_value")
    if numeric is not None:
        try:
            return int(Decimal(str(numeric)))
        except (InvalidOperation, ValueError):
            pass
    match = INTEGER_PATTERN.search(_cell_text(cell))
    if match is None:
        return None
    try:
        return int(match.group().replace(",", ""))
    except ValueError:
        return None


def _amount_krw(
    cell: dict[str, Any] | None,
    context_text: str,
) -> tuple[int | None, str | None]:
    value = _int_value(cell)
    if value is None:
        return None, None

    cell_text = _cell_text(cell)
    direct_unit = _numeric_suffix_unit(cell_text)
    if direct_unit is not None:
        unit, multiplier = direct_unit
        return value * multiplier, unit

    unit_raw = str(cell.get("unit_raw") or "") if cell is not None else ""
    declared_unit = _declared_unit(unit_raw)
    if declared_unit is not None:
        unit, multiplier = declared_unit
        return value * multiplier, unit

    table_unit = _table_context_unit(context_text)
    if table_unit is not None:
        unit, multiplier = table_unit
        return value * multiplier, unit

    if abs(value) >= 1_000_000_000:
        return value, "원(inferred)"
    return None, None


def _numeric_suffix_unit(text: str) -> tuple[str, int] | None:
    normalized = unicodedata.normalize("NFKC", text)
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?\s*(억원|백만원|천원|원)", normalized)
    if match is None:
        return None
    return _unit_pair(match.group(1))


def _declared_unit(text: str) -> tuple[str, int] | None:
    compact = _compact(text)
    for unit, multiplier in UNIT_MULTIPLIERS:
        if compact == unit:
            return unit, multiplier
    return None


def _table_context_unit(text: str) -> tuple[str, int] | None:
    normalized = unicodedata.normalize("NFKC", text)
    match = re.search(
        r"(?:\(|\[)?\s*단위\s*[:：]?\s*(억원|백만원|천원|원)",
        normalized,
    )
    if match is None:
        return None
    return _unit_pair(match.group(1))


def _unit_pair(unit: str) -> tuple[str, int]:
    for known, multiplier in UNIT_MULTIPLIERS:
        if known == unit:
            return known, multiplier
    raise ValueError(f"unsupported monetary unit: {unit}")


def _parse_date(text: str) -> date | None:
    match = DATE_PATTERN.search(unicodedata.normalize("NFKC", text))
    if match is None:
        return None
    try:
        return date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return None


def _instrument_from_text(text: str) -> FundraisingInstrument | None:
    for instrument, term in INSTRUMENT_TERMS:
        if term in text:
            return instrument
    return None


def _series_key(series: str | None, security_name: str | None) -> str:
    for value in (series, security_name):
        match = re.search(r"(?:제\s*)?(\d+)\s*(?:회|회차)?", value or "")
        if match is not None:
            return match.group(1)
    return _compact(security_name)


def _find_field_row(
    field_rows: dict[str, tuple[int, tuple[dict[str, Any], ...]]],
    labels: tuple[str, ...],
) -> tuple[int, tuple[dict[str, Any], ...]] | None:
    for label, row in field_rows.items():
        if any(token in label for token in labels):
            return row
    return None


def _last_value_cell(row: tuple[dict[str, Any], ...]) -> dict[str, Any] | None:
    for cell in reversed(row):
        if _cell_text(cell):
            return cell
    return None


def _compact(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text)
