"""Persistence for facility-investment correction lineage and latest state."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from disclosure_agent.domain.facility_investment_lineage import (
    FacilityInvestmentSnapshot,
    resolve_facility_investment_lineage,
)
from disclosure_agent.storage.db_models import SourceFilingRow
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
