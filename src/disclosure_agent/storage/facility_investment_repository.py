"""Repository for typed facility-investment events projected from generic facts."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from disclosure_agent.domain.events import EventType, FacilityInvestmentEvent
from disclosure_agent.facts.generic import FactKind, GenericFact
from disclosure_agent.storage.db_models import SourceCompanyRow, SourceFilingRow
from disclosure_agent.storage.generic_fact_models import GenericFactRow
from disclosure_agent.storage.source_event_models import (
    FacilityInvestmentEventRow,
    SourceEventEvidenceRow,
    SourceEventRow,
)

FACILITY_INVESTMENT_SUBTYPE = "신규시설투자등"


@dataclass(frozen=True, slots=True)
class FacilityInvestmentCandidate:
    """Source filing metadata and its persisted generic facts."""

    filing_id: str
    receipt_number: str
    receipt_date: date
    corp_code: str
    company_name: str
    stock_code: str
    is_correction: bool
    facts: tuple[GenericFact, ...]


class FacilityInvestmentRepository:
    """Read facility candidates and atomically replace their typed projections."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def read_candidates(self) -> tuple[FacilityInvestmentCandidate, ...]:
        """Load only the 43 Exchange facility filings and their indexed generic facts."""

        filing_rows = self.session.execute(
            select(SourceFilingRow, SourceCompanyRow)
            .join(SourceCompanyRow, SourceCompanyRow.corp_code == SourceFilingRow.corp_code)
            .where(
                SourceFilingRow.document_group == "exchange",
                SourceFilingRow.document_subtype == FACILITY_INVESTMENT_SUBTYPE,
            )
            .order_by(SourceFilingRow.receipt_date, SourceFilingRow.filing_id)
        ).all()
        if not filing_rows:
            return ()

        filing_ids = [filing.filing_id for filing, _company in filing_rows]
        grouped: dict[str, list[GenericFact]] = defaultdict(list)
        fact_rows = self.session.scalars(
            select(GenericFactRow)
            .where(GenericFactRow.filing_id.in_(filing_ids))
            .order_by(
                GenericFactRow.filing_id,
                GenericFactRow.table_id,
                GenericFactRow.row_index,
                GenericFactRow.column_index,
            )
        ).all()
        for row in fact_rows:
            grouped[row.filing_id].append(_to_domain_fact(row))

        return tuple(
            FacilityInvestmentCandidate(
                filing_id=filing.filing_id,
                receipt_number=filing.receipt_number,
                receipt_date=filing.receipt_date,
                corp_code=filing.corp_code,
                company_name=company.listed_name,
                stock_code=company.stock_code or "",
                is_correction=filing.is_correction,
                facts=tuple(grouped.get(filing.filing_id, ())),
            )
            for filing, company in filing_rows
        )

    def replace_events(
        self,
        *,
        projections: list[tuple[FacilityInvestmentCandidate, FacilityInvestmentEvent, dict[str, GenericFact]]],
    ) -> tuple[int, int]:
        """Upsert current facility events/evidence and prune stale facility projections."""

        event_type = EventType.FACILITY_INVESTMENT.value
        event_ids = [f"{event_type}:{candidate.filing_id}" for candidate, _event, _evidence in projections]
        filing_ids = [candidate.filing_id for candidate, _event, _evidence in projections]

        existing_event_ids = self.session.scalars(
            select(SourceEventRow.event_id).where(SourceEventRow.event_type == event_type)
        ).all()
        stale_event_ids = set(existing_event_ids) - set(event_ids)
        if stale_event_ids:
            self.session.execute(delete(SourceEventRow).where(SourceEventRow.event_id.in_(stale_event_ids)))

        for candidate, event, evidence in projections:
            event_id = f"{event_type}:{candidate.filing_id}"
            envelope = {
                "event_id": event_id,
                "filing_id": candidate.filing_id,
                "corp_code": candidate.corp_code,
                "event_type": event_type,
                "event_date": event.decision_date or candidate.receipt_date,
            }
            _upsert(self.session, SourceEventRow, envelope, ("event_id",))

            typed = {
                "filing_id": candidate.filing_id,
                "event_id": event_id,
                "investment_type": event.investment_type,
                "investment_subject": event.investment_subject,
                "investment_amount_krw": event.investment_amount_krw,
                "equity_krw": event.equity_krw,
                "equity_ratio": event.equity_ratio,
                "purpose": event.purpose,
                "investment_start_date": event.investment_start_date,
                "investment_end_date": event.investment_end_date,
                "decision_date": event.decision_date,
                "defer_reason": event.defer_reason,
                "defer_until": event.defer_until,
                "notes": event.notes,
            }
            _upsert(self.session, FacilityInvestmentEventRow, typed, ("filing_id",))

            self.session.execute(
                delete(SourceEventEvidenceRow).where(SourceEventEvidenceRow.event_id == event_id)
            )
            if evidence:
                self.session.execute(
                    insert(SourceEventEvidenceRow),
                    [
                        {
                            "event_id": event_id,
                            "attribute": attribute,
                            "fact_id": fact.fact_id,
                        }
                        for attribute, fact in sorted(evidence.items())
                    ],
                )

        if filing_ids:
            self.session.execute(
                delete(FacilityInvestmentEventRow).where(
                    FacilityInvestmentEventRow.filing_id.not_in(filing_ids)
                )
            )
        else:
            self.session.execute(delete(FacilityInvestmentEventRow))

        evidence_count = sum(len(evidence) for _candidate, _event, evidence in projections)
        return len(projections), evidence_count


def _to_domain_fact(row: GenericFactRow) -> GenericFact:
    return GenericFact(
        fact_id=row.fact_id,
        fact_kind=FactKind(row.fact_kind),
        filing_id=row.filing_id,
        document_id=row.document_id,
        section_id=row.section_id,
        block_id=row.block_id,
        table_id=row.table_id,
        row_index=row.row_index,
        column_index=row.column_index,
        label_text=row.label_text,
        header_text=row.header_text,
        path_text=row.path_text,
        value_text=row.value_text,
        raw_value=row.raw_value,
        numeric_value=row.numeric_value,
        unit_raw=row.unit_raw,
        currency=row.currency,
        concept_code=row.concept_code,
        context_ref=row.context_ref,
        source_locator=row.source_locator,
    )


def _upsert(
    session: Session,
    model: type[Any],
    values: dict[str, Any],
    conflict_columns: tuple[str, ...],
) -> None:
    statement = insert(model).values(values)
    update_values = {
        key: getattr(statement.excluded, key) for key in values if key not in conflict_columns
    }
    session.execute(
        statement.on_conflict_do_update(
            index_elements=list(conflict_columns),
            set_=update_values,
        )
    )
