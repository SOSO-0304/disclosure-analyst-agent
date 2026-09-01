"""Domain types for deterministic fundraising category aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from disclosure_agent.extractors.fundraising import FundraisingInstrument


@dataclass(frozen=True, slots=True)
class FundraisingEventObservation:
    """One canonical fundraising event used by deterministic aggregation."""

    event_id: str
    instrument_type: FundraisingInstrument
    issue_date: date
    amount_krw: int | None
    issuer_name: str
    security_name: str | None
    series: str | None
    issuance_method: str | None
    stock_kind: str | None
    share_quantity: int | None
    issue_price_krw: int | None
    source_count: int
    representative_filing_id: str
    representative_table_id: str
    representative_row_index: int


@dataclass(frozen=True, slots=True)
class FundraisingCategorySummary:
    """Deterministic summary for one fundraising instrument category."""

    instrument_type: FundraisingInstrument
    status: str
    event_count: int
    known_amount_count: int
    missing_amount_count: int
    total_amount_krw: int | None
    known_amount_sum_krw: int
    events: tuple[FundraisingEventObservation, ...]


@dataclass(frozen=True, slots=True)
class FundraisingAnalysisResult:
    """Type-by-type fundraising summary for one company and calendar year."""

    company_name: str
    year: int
    status: str
    event_count: int
    known_amount_count: int
    missing_amount_count: int
    total_amount_krw: int | None
    known_amount_sum_krw: int
    categories: tuple[FundraisingCategorySummary, ...]
