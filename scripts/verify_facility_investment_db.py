#!/usr/bin/env python3
"""Verify facility-investment typed events against the accepted source corpus."""

from __future__ import annotations

import argparse

from sqlalchemy import func, select

from disclosure_agent.domain.events import EventType
from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import SourceFilingRow
from disclosure_agent.storage.source_event_models import (
    FacilityInvestmentCorrectionLinkRow,
    FacilityInvestmentEventRow,
    FacilityInvestmentLifecycleRow,
    SourceEventEvidenceRow,
    SourceEventRow,
)

EXPECTED_FILINGS = 43
EXPECTED_CORRECTIONS = 15


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        candidate_count = (
            session.scalar(
                select(func.count())
                .select_from(SourceFilingRow)
                .where(
                    SourceFilingRow.document_group == "exchange",
                    SourceFilingRow.document_subtype == "신규시설투자등",
                )
            )
            or 0
        )
        event_count = (
            session.scalar(select(func.count()).select_from(FacilityInvestmentEventRow)) or 0
        )
        envelope_count = (
            session.scalar(
                select(func.count())
                .select_from(SourceEventRow)
                .where(SourceEventRow.event_type == EventType.FACILITY_INVESTMENT.value)
            )
            or 0
        )
        evidence_count = (
            session.scalar(
                select(func.count())
                .select_from(SourceEventEvidenceRow)
                .join(SourceEventRow, SourceEventRow.event_id == SourceEventEvidenceRow.event_id)
                .where(SourceEventRow.event_type == EventType.FACILITY_INVESTMENT.value)
            )
            or 0
        )
        orphan_typed = (
            session.scalar(
                select(func.count())
                .select_from(FacilityInvestmentEventRow)
                .outerjoin(
                    SourceEventRow,
                    SourceEventRow.event_id == FacilityInvestmentEventRow.event_id,
                )
                .where(SourceEventRow.event_id.is_(None))
            )
            or 0
        )
        correction_count = (
            session.scalar(select(func.count()).select_from(FacilityInvestmentCorrectionLinkRow))
            or 0
        )
        lifecycle_count = (
            session.scalar(select(func.count()).select_from(FacilityInvestmentLifecycleRow)) or 0
        )
        lifecycle_corrections = (
            session.scalar(select(func.sum(FacilityInvestmentLifecycleRow.correction_count))) or 0
        )
        incomplete_lifecycles = (
            session.scalar(
                select(func.count())
                .select_from(FacilityInvestmentLifecycleRow)
                .where(FacilityInvestmentLifecycleRow.lineage_complete.is_(False))
            )
            or 0
        )
        lineage_statuses = dict(
            session.execute(
                select(
                    FacilityInvestmentCorrectionLinkRow.status,
                    func.count(),
                ).group_by(FacilityInvestmentCorrectionLinkRow.status)
            ).all()
        )

        coverage = {
            "investment_type": session.scalar(
                select(func.count(FacilityInvestmentEventRow.investment_type))
            )
            or 0,
            "investment_subject": session.scalar(
                select(func.count(FacilityInvestmentEventRow.investment_subject))
            )
            or 0,
            "investment_amount_krw": session.scalar(
                select(func.count(FacilityInvestmentEventRow.investment_amount_krw))
            )
            or 0,
            "equity_krw": session.scalar(select(func.count(FacilityInvestmentEventRow.equity_krw)))
            or 0,
            "equity_ratio": session.scalar(
                select(func.count(FacilityInvestmentEventRow.equity_ratio))
            )
            or 0,
            "purpose": session.scalar(select(func.count(FacilityInvestmentEventRow.purpose))) or 0,
            "investment_start_date": session.scalar(
                select(func.count(FacilityInvestmentEventRow.investment_start_date))
            )
            or 0,
            "investment_end_date": session.scalar(
                select(func.count(FacilityInvestmentEventRow.investment_end_date))
            )
            or 0,
            "decision_date": session.scalar(
                select(func.count(FacilityInvestmentEventRow.decision_date))
            )
            or 0,
            "notes": session.scalar(select(func.count(FacilityInvestmentEventRow.notes))) or 0,
        }

    print("=== facility investment database verification ===")
    print(f"candidate filings             {candidate_count:>5}  expected={EXPECTED_FILINGS}")
    print(f"typed events                 {event_count:>5}  expected={EXPECTED_FILINGS}")
    print(f"source event envelopes       {envelope_count:>5}  expected={EXPECTED_FILINGS}")
    print(f"evidence links               {evidence_count:>5}")
    print(f"orphan typed rows            {orphan_typed:>5}")
    print(f"correction links             {correction_count:>5}  expected={EXPECTED_CORRECTIONS}")
    print(f"lifecycle rows               {lifecycle_count:>5}")
    print(f"lifecycle correction sum     {lifecycle_corrections:>5}")
    print(f"incomplete lifecycles        {incomplete_lifecycles:>5}")
    print("\n=== correction status ===")
    for status, count in sorted(lineage_statuses.items()):
        print(f"{status:<30} {count:>4}/{correction_count}")
    print("\n=== typed field coverage ===")
    for field, count in coverage.items():
        print(f"{field:<30} {count:>4}/{event_count}")

    failures: list[str] = []
    if candidate_count != EXPECTED_FILINGS:
        failures.append(f"candidate filings={candidate_count}")
    if event_count != EXPECTED_FILINGS:
        failures.append(f"typed events={event_count}")
    if envelope_count != EXPECTED_FILINGS:
        failures.append(f"source event envelopes={envelope_count}")
    if evidence_count <= 0:
        failures.append("no evidence links")
    if orphan_typed:
        failures.append(f"orphan typed rows={orphan_typed}")
    if correction_count != EXPECTED_CORRECTIONS:
        failures.append(f"correction links={correction_count}")
    if lifecycle_count <= 0 or lifecycle_count > event_count:
        failures.append(f"invalid lifecycle rows={lifecycle_count}")
    if lifecycle_corrections != EXPECTED_CORRECTIONS:
        failures.append(f"lifecycle correction sum={lifecycle_corrections}")
    if coverage["investment_amount_krw"] != EXPECTED_FILINGS:
        failures.append("investment amount coverage is not complete")
    if coverage["purpose"] != EXPECTED_FILINGS:
        failures.append("purpose coverage is not complete")

    if failures:
        raise SystemExit("Facility investment verification failed: " + "; ".join(failures))
    print("status                       verified")


if __name__ == "__main__":
    main()
