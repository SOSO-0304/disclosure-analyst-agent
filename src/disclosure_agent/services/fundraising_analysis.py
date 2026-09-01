"""Deterministic type-by-type fundraising aggregation."""

from __future__ import annotations

from sqlalchemy.orm import Session

from disclosure_agent.domain.fundraising_analysis import (
    FundraisingAnalysisResult,
    FundraisingCategorySummary,
    FundraisingEventObservation,
)
from disclosure_agent.extractors.fundraising import FundraisingInstrument
from disclosure_agent.retrieval.company_resolver import resolve_company
from disclosure_agent.storage.fundraising_repository import (
    FundraisingQueryResult,
    FundraisingRepository,
)

FUNDRAISING_INSTRUMENT_ORDER = (
    FundraisingInstrument.RIGHTS_ISSUE,
    FundraisingInstrument.CONVERTIBLE_BOND,
    FundraisingInstrument.BOND_WITH_WARRANTS,
    FundraisingInstrument.EXCHANGEABLE_BOND,
)


def _to_observation(event: FundraisingQueryResult) -> FundraisingEventObservation:
    if event.issue_date is None:
        raise ValueError("year-scoped fundraising analysis requires a dated event")
    return FundraisingEventObservation(
        event_id=event.event_id,
        instrument_type=FundraisingInstrument(event.instrument_type),
        issue_date=event.issue_date,
        amount_krw=event.amount_krw,
        issuer_name=event.issuer_name,
        security_name=event.security_name,
        series=event.series,
        issuance_method=event.issuance_method,
        stock_kind=event.stock_kind,
        share_quantity=event.share_quantity,
        issue_price_krw=event.issue_price_krw,
        source_count=event.source_count,
        representative_filing_id=event.representative_filing_id,
        representative_table_id=event.representative_table_id,
        representative_row_index=event.representative_row_index,
    )


def summarize_fundraising_events(
    *,
    company_name: str,
    year: int,
    events: tuple[FundraisingQueryResult, ...],
) -> FundraisingAnalysisResult:
    """Aggregate canonical events without treating missing amounts as zero."""

    observations = tuple(_to_observation(event) for event in events)
    categories = []

    for instrument in FUNDRAISING_INSTRUMENT_ORDER:
        category_events = tuple(
            event for event in observations if event.instrument_type is instrument
        )
        if not category_events:
            categories.append(
                FundraisingCategorySummary(
                    instrument_type=instrument,
                    status="NO_MATCH",
                    event_count=0,
                    known_amount_count=0,
                    missing_amount_count=0,
                    total_amount_krw=None,
                    known_amount_sum_krw=0,
                    events=(),
                )
            )
            continue

        known_amounts = tuple(
            event.amount_krw for event in category_events if event.amount_krw is not None
        )
        missing_amount_count = len(category_events) - len(known_amounts)
        known_amount_sum = sum(known_amounts)
        status = "ANSWERABLE" if missing_amount_count == 0 else "PARTIAL"
        categories.append(
            FundraisingCategorySummary(
                instrument_type=instrument,
                status=status,
                event_count=len(category_events),
                known_amount_count=len(known_amounts),
                missing_amount_count=missing_amount_count,
                total_amount_krw=known_amount_sum if status == "ANSWERABLE" else None,
                known_amount_sum_krw=known_amount_sum,
                events=category_events,
            )
        )

    event_count = len(observations)
    known_amount_count = sum(
        1 for event in observations if event.amount_krw is not None
    )
    missing_amount_count = event_count - known_amount_count
    known_amount_sum = sum(
        event.amount_krw or 0 for event in observations if event.amount_krw is not None
    )

    if event_count == 0:
        status = "NO_MATCH"
        total_amount_krw = None
    elif missing_amount_count:
        status = "PARTIAL"
        total_amount_krw = None
    else:
        status = "ANSWERABLE"
        total_amount_krw = known_amount_sum

    return FundraisingAnalysisResult(
        company_name=company_name,
        year=year,
        status=status,
        event_count=event_count,
        known_amount_count=known_amount_count,
        missing_amount_count=missing_amount_count,
        total_amount_krw=total_amount_krw,
        known_amount_sum_krw=known_amount_sum,
        categories=tuple(categories),
    )


class FundraisingAnalysisService:
    """Resolve one company and aggregate its dated canonical fundraising events."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = FundraisingRepository(session)

    def analyze(self, *, company_name: str, year: int) -> FundraisingAnalysisResult:
        company = resolve_company(self.session, company_name)
        if company is None:
            raise ValueError(f"unknown company: {company_name}")

        events = self.repository.query_events(
            company_name=company.listed_name,
            year=year,
        )
        return summarize_fundraising_events(
            company_name=company.listed_name,
            year=year,
            events=events,
        )
