"""Persistence for facility-investment correction lineage and latest state."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from disclosure_agent.domain.facility_investment_lineage import (
    FacilityInvestmentSnapshot,
    resolve_facility_investment_lineage,
)
from disclosure_agent.storage.db_models import SourceFilingRow
from disclosure_agent.storage.generic_fact_models import GenericFactRow
from disclosure_agent.storage.source_event_models import (
    FacilityInvestmentCorrectionLinkRow,
    FacilityInvestmentEventRow,
    FacilityInvestmentLifecycleRow,
)


@dataclass(frozen=True, slots=True)
class FacilityInvestmentLineagePersistenceResult:
    """Materialized lineage counts and status distribution."""

    correction_links: int
    lifecycle_rows: int
    status_counts: dict[str, int]


class FacilityInvestmentLineageRepository:
    """Build correction chains from already-materialized typed facility events."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def replace_lineage(self) -> FacilityInvestmentLineagePersistenceResult:
        rows = self.session.execute(
            select(SourceFilingRow, FacilityInvestmentEventRow)
            .join(
                FacilityInvestmentEventRow,
                FacilityInvestmentEventRow.filing_id == SourceFilingRow.filing_id,
            )
            .order_by(SourceFilingRow.receipt_date, SourceFilingRow.filing_id)
        ).all()

        correction_ids = [filing.filing_id for filing, _event in rows if filing.is_correction]
        related_dates = self._read_related_filing_dates(correction_ids)
        snapshots = [
            FacilityInvestmentSnapshot(
                filing_id=filing.filing_id,
                corp_code=filing.corp_code,
                receipt_date=filing.receipt_date,
                is_correction=filing.is_correction,
                decision_date=event.decision_date,
                investment_subject=event.investment_subject,
                investment_type=event.investment_type,
                purpose=event.purpose,
                investment_amount_krw=event.investment_amount_krw,
                related_filing_date=related_dates.get(filing.filing_id),
            )
            for filing, event in rows
        ]
        result = resolve_facility_investment_lineage(snapshots)

        self.session.execute(delete(FacilityInvestmentCorrectionLinkRow))
        self.session.execute(delete(FacilityInvestmentLifecycleRow))

        if result.corrections:
            self.session.execute(
                insert(FacilityInvestmentCorrectionLinkRow),
                [
                    {
                        "correction_filing_id": row.correction_filing_id,
                        "predecessor_filing_id": row.predecessor_filing_id,
                        "root_filing_id": row.root_filing_id,
                        "status": row.status,
                        "candidate_filing_ids": list(row.candidate_filing_ids),
                        "match_score": row.match_score,
                    }
                    for row in result.corrections
                ],
            )
        if result.lifecycles:
            self.session.execute(
                insert(FacilityInvestmentLifecycleRow),
                [
                    {
                        "root_filing_id": row.root_filing_id,
                        "corp_code": row.corp_code,
                        "latest_filing_id": row.latest_filing_id,
                        "latest_receipt_date": row.latest_receipt_date,
                        "correction_count": row.correction_count,
                        "lineage_complete": row.lineage_complete,
                        "status": row.status,
                    }
                    for row in result.lifecycles
                ],
            )

        status_counts = Counter(row.status for row in result.corrections)
        return FacilityInvestmentLineagePersistenceResult(
            correction_links=len(result.corrections),
            lifecycle_rows=len(result.lifecycles),
            status_counts=dict(sorted(status_counts.items())),
        )

    def _read_related_filing_dates(self, filing_ids: list[str]) -> dict[str, date]:
        if not filing_ids:
            return {}

        rows = self.session.scalars(
            select(GenericFactRow)
            .where(GenericFactRow.filing_id.in_(filing_ids))
            .order_by(
                GenericFactRow.filing_id,
                GenericFactRow.table_id,
                GenericFactRow.row_index,
                GenericFactRow.column_index,
            )
        ).all()
        facts_by_filing: dict[str, list[GenericFactRow]] = defaultdict(list)
        for row in rows:
            facts_by_filing[row.filing_id].append(row)

        related_dates: dict[str, date] = {}
        for filing_id, facts in facts_by_filing.items():
            for fact in facts:
                if not _is_related_filing_date_label(fact.label_text):
                    continue
                parsed = _parse_date(fact.value_text)
                if parsed is not None:
                    related_dates[filing_id] = parsed
                    break
        return related_dates


def _is_related_filing_date_label(label: str | None) -> bool:
    if not label:
        return False
    compact = "".join(label.split())
    return "정정관련공시서류제출일" in compact


def _parse_date(value: str) -> date | None:
    normalized = value.strip().replace(".", "-").replace("/", "-")
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None
