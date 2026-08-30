#!/usr/bin/env python3
"""Inspect typed facility-investment values and their fact-backed evidence."""

from __future__ import annotations

import argparse

from sqlalchemy import desc, select

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import SourceCompanyRow, SourceFilingRow
from disclosure_agent.storage.generic_fact_models import GenericFactRow
from disclosure_agent.storage.source_event_models import (
    FacilityInvestmentEventRow,
    SourceEventEvidenceRow,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        rows = session.execute(
            select(
                SourceCompanyRow.listed_name,
                SourceFilingRow.receipt_date,
                SourceFilingRow.report_name,
                SourceFilingRow.is_correction,
                FacilityInvestmentEventRow,
            )
            .join(SourceFilingRow, SourceFilingRow.corp_code == SourceCompanyRow.corp_code)
            .join(
                FacilityInvestmentEventRow,
                FacilityInvestmentEventRow.filing_id == SourceFilingRow.filing_id,
            )
            .order_by(desc(FacilityInvestmentEventRow.investment_amount_krw).nulls_last())
            .limit(args.limit)
        ).all()

        print("=== facility investment top amounts ===")
        for company, receipt_date, report_name, is_correction, event in rows:
            amount = (
                f"{event.investment_amount_krw:,}"
                if event.investment_amount_krw is not None
                else "-"
            )
            ratio = str(event.equity_ratio) if event.equity_ratio is not None else "-"
            correction = " correction" if is_correction else ""
            print(
                f"{receipt_date} {company}{correction} amount={amount} ratio={ratio}% "
                f"period={event.investment_start_date}~{event.investment_end_date}"
            )
            print(f"  report={report_name}")
            print(f"  type={event.investment_type!r} subject={event.investment_subject!r}")
            print(f"  purpose={event.purpose!r}")

        evidence_rows = session.execute(
            select(
                SourceCompanyRow.listed_name,
                SourceFilingRow.receipt_number,
                SourceEventEvidenceRow.attribute,
                GenericFactRow.label_text,
                GenericFactRow.value_text,
            )
            .join(SourceFilingRow, SourceFilingRow.corp_code == SourceCompanyRow.corp_code)
            .join(
                FacilityInvestmentEventRow,
                FacilityInvestmentEventRow.filing_id == SourceFilingRow.filing_id,
            )
            .join(
                SourceEventEvidenceRow,
                SourceEventEvidenceRow.event_id == FacilityInvestmentEventRow.event_id,
            )
            .join(GenericFactRow, GenericFactRow.fact_id == SourceEventEvidenceRow.fact_id)
            .order_by(SourceFilingRow.receipt_date.desc(), SourceEventEvidenceRow.attribute)
            .limit(args.limit * 3)
        ).all()

        print("\n=== recent evidence samples ===")
        for company, receipt_number, attribute, label, value in evidence_rows:
            rendered = value if len(value) <= 160 else value[:157] + "..."
            print(
                f"{company} receipt={receipt_number} attribute={attribute} "
                f"label={label!r} value={rendered!r}"
            )


if __name__ == "__main__":
    main()
