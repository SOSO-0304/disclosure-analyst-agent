"""Read latest effective facility-investment states for structured retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, desc, select
from sqlalchemy.orm import Session

from disclosure_agent.storage.db_models import SourceCompanyRow, SourceFilingRow
from disclosure_agent.storage.source_event_models import (
    FacilityInvestmentEventRow,
    FacilityInvestmentLifecycleRow,
)


@dataclass(frozen=True, slots=True)
class LatestFacilityInvestment:
    """One latest correction-aware facility-investment state."""

    root_filing_id: str
    latest_filing_id: str
    corp_code: str
    company_name: str
    stock_code: str | None
    latest_receipt_date: date
    report_name: str
    correction_count: int
    lineage_complete: bool
    lineage_status: str
    investment_type: str | None
    investment_subject: str | None
    investment_amount_krw: int | None
    equity_krw: int | None
    equity_ratio: Decimal | None
    purpose: str | None
    investment_start_date: date | None
    investment_end_date: date | None
    decision_date: date | None
    notes: str | None


class FacilityInvestmentQueryRepository:
    """Structured SQL retrieval over latest facility-investment states."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_latest(
        self,
        *,
        company_names: tuple[str, ...] = (),
        corp_codes: tuple[str, ...] = (),
        min_amount_krw: int | None = None,
        decision_date_from: date | None = None,
        decision_date_to: date | None = None,
        require_complete_lineage: bool = False,
        limit: int = 100,
    ) -> tuple[LatestFacilityInvestment, ...]:
        if limit < 1:
            raise ValueError("limit must be at least 1")

        statement = self._base_statement()
        if company_names:
            statement = statement.where(SourceCompanyRow.listed_name.in_(company_names))
        if corp_codes:
            statement = statement.where(FacilityInvestmentLifecycleRow.corp_code.in_(corp_codes))
        if min_amount_krw is not None:
            statement = statement.where(
                FacilityInvestmentEventRow.investment_amount_krw >= min_amount_krw
            )
        if decision_date_from is not None:
            statement = statement.where(
                FacilityInvestmentEventRow.decision_date >= decision_date_from
            )
        if decision_date_to is not None:
            statement = statement.where(
                FacilityInvestmentEventRow.decision_date <= decision_date_to
            )
        if require_complete_lineage:
            statement = statement.where(FacilityInvestmentLifecycleRow.lineage_complete.is_(True))

        rows = self.session.execute(
            statement.order_by(
                desc(FacilityInvestmentEventRow.investment_amount_krw).nulls_last(),
                desc(FacilityInvestmentLifecycleRow.latest_receipt_date),
            ).limit(limit)
        ).all()
        return tuple(_project(row) for row in rows)

    @staticmethod
    def _base_statement() -> Select[tuple[object, ...]]:
        return (
            select(
                FacilityInvestmentLifecycleRow,
                FacilityInvestmentEventRow,
                SourceFilingRow,
                SourceCompanyRow,
            )
            .join(
                FacilityInvestmentEventRow,
                FacilityInvestmentEventRow.filing_id
                == FacilityInvestmentLifecycleRow.latest_filing_id,
            )
            .join(
                SourceFilingRow,
                SourceFilingRow.filing_id == FacilityInvestmentLifecycleRow.latest_filing_id,
            )
            .join(
                SourceCompanyRow,
                SourceCompanyRow.corp_code == FacilityInvestmentLifecycleRow.corp_code,
            )
        )


def _project(row: tuple[object, ...]) -> LatestFacilityInvestment:
    lifecycle, event, filing, company = row
    assert isinstance(lifecycle, FacilityInvestmentLifecycleRow)
    assert isinstance(event, FacilityInvestmentEventRow)
    assert isinstance(filing, SourceFilingRow)
    assert isinstance(company, SourceCompanyRow)
    return LatestFacilityInvestment(
        root_filing_id=lifecycle.root_filing_id,
        latest_filing_id=lifecycle.latest_filing_id,
        corp_code=lifecycle.corp_code,
        company_name=company.listed_name,
        stock_code=company.stock_code,
        latest_receipt_date=lifecycle.latest_receipt_date,
        report_name=filing.report_name,
        correction_count=lifecycle.correction_count,
        lineage_complete=lifecycle.lineage_complete,
        lineage_status=lifecycle.status,
        investment_type=event.investment_type,
        investment_subject=event.investment_subject,
        investment_amount_krw=event.investment_amount_krw,
        equity_krw=event.equity_krw,
        equity_ratio=event.equity_ratio,
        purpose=event.purpose,
        investment_start_date=event.investment_start_date,
        investment_end_date=event.investment_end_date,
        decision_date=event.decision_date,
        notes=event.notes,
    )
