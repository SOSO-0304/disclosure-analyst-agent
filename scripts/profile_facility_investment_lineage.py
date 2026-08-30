#!/usr/bin/env python3
"""Inspect unresolved and out-of-corpus facility-investment correction lineage."""

from __future__ import annotations

import argparse

from sqlalchemy import select

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import SourceCompanyRow, SourceFilingRow
from disclosure_agent.storage.source_event_models import (
    FacilityInvestmentCorrectionLinkRow,
    FacilityInvestmentEventRow,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument(
        "--status",
        action="append",
        default=["unresolved", "out_of_corpus_predecessor", "ambiguous"],
    )
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        rows = session.execute(
            select(
                FacilityInvestmentCorrectionLinkRow,
                SourceFilingRow,
                SourceCompanyRow,
                FacilityInvestmentEventRow,
            )
            .join(
                SourceFilingRow,
                SourceFilingRow.filing_id
                == FacilityInvestmentCorrectionLinkRow.correction_filing_id,
            )
            .join(
                SourceCompanyRow,
                SourceCompanyRow.corp_code == SourceFilingRow.corp_code,
            )
            .join(
                FacilityInvestmentEventRow,
                FacilityInvestmentEventRow.filing_id == SourceFilingRow.filing_id,
            )
            .where(FacilityInvestmentCorrectionLinkRow.status.in_(tuple(args.status)))
            .order_by(SourceFilingRow.receipt_date, SourceFilingRow.filing_id)
        ).all()

    print("=== facility investment lineage diagnostics ===")
    print(f"rows                            {len(rows)}")
    for link, filing, company, event in rows:
        if event.investment_amount_krw is None:
            amount = "-"
        else:
            amount = f"{event.investment_amount_krw:,}"
        print(
            f"{filing.receipt_date} {company.listed_name} status={link.status} "
            f"score={link.match_score}"
        )
        print(
            f"  correction={link.correction_filing_id} "
            f"predecessor={link.predecessor_filing_id} root={link.root_filing_id}"
        )
        print(f"  candidates={link.candidate_filing_ids}")
        print(
            f"  decision={event.decision_date} amount={amount} "
            f"type={event.investment_type!r}"
        )
        print(f"  subject={event.investment_subject!r}")
        print(f"  purpose={event.purpose!r}")


if __name__ == "__main__":
    main()
