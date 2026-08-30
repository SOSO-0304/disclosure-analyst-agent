#!/usr/bin/env python3
"""Query one company's annual consolidated revenue from generic facts."""

from __future__ import annotations

import argparse

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.revenue_repository import RevenueRepository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--company", required=True)
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        result = RevenueRepository(session).query_annual_consolidated_revenue(
            company_name=args.company,
            year=args.year,
        )

    print("=== revenue query ===")
    print(f"company                         {args.company}")
    print(f"year                            {args.year}")
    print(f"status                          {result.status}")

    if result.candidate is None:
        print(f"alternatives                    {len(result.alternatives)}")
        for candidate in result.alternatives[:5]:
            print(
                f"  value={candidate.numeric_value} score={candidate.score} "
                f"fact={candidate.fact_id}"
            )
        return

    candidate = result.candidate
    amount = f"{result.amount_krw:,}" if result.amount_krw is not None else "unknown"
    print(f"amount_krw                      {amount}")
    print(f"raw_value                       {candidate.raw_value}")
    print(f"resolved_unit                   {result.resolved_unit or 'unknown'}")
    print(f"label                           {candidate.label_text!r}")
    print(f"header                          {candidate.header_text!r}")
    print(f"filing                          {candidate.filing_id}")
    print(
        f"source                          table={candidate.table_id} "
        f"row={candidate.row_index} col={candidate.column_index}"
    )
    print(f"fact                            {candidate.fact_id}")
    print(f"path                            {candidate.path_text}")


if __name__ == "__main__":
    main()
