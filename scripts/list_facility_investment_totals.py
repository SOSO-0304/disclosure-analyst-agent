#!/usr/bin/env python3
"""List annual facility-investment totals available for metric comparison tests."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date

from disclosure_agent.rendering.money import format_krw
from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.facility_investment_query_repository import (
    FacilityInvestmentQueryRepository,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        rows = FacilityInvestmentQueryRepository(session).list_latest(
            decision_date_from=date(args.year, 1, 1),
            decision_date_to=date(args.year, 12, 31),
            limit=args.limit,
        )

    grouped = defaultdict(list)
    for row in rows:
        grouped[row.company_name].append(row)

    summaries = []
    for company_name, company_rows in grouped.items():
        complete = all(
            row.lineage_complete and row.investment_amount_krw is not None
            for row in company_rows
        )
        total = (
            sum(row.investment_amount_krw or 0 for row in company_rows)
            if complete
            else None
        )
        summaries.append((company_name, company_rows, complete, total))

    summaries.sort(
        key=lambda item: (
            item[3] is None,
            -(item[3] or 0),
            item[0],
        )
    )

    print(f"=== FACILITY INVESTMENT TOTALS {args.year} ===")
    print(f"events                          {len(rows)}")
    print(f"companies                       {len(summaries)}")
    for index, (company_name, company_rows, complete, total) in enumerate(
        summaries,
        start=1,
    ):
        status = "ANSWERABLE" if complete else "PARTIAL"
        print(
            f"{index:>2}. {company_name:<20} "
            f"events={len(company_rows):<3} status={status:<10} total={format_krw(total)}"
        )


if __name__ == "__main__":
    main()
