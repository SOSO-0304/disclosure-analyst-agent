#!/usr/bin/env python3
"""Query canonical fundraising events by company, year, and optional instrument type."""

from __future__ import annotations

import argparse

from disclosure_agent.extractors.fundraising import FundraisingInstrument
from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.fundraising_repository import FundraisingRepository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--company", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument(
        "--instrument",
        choices=[instrument.value for instrument in FundraisingInstrument],
    )
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        rows = FundraisingRepository(session).query_events(
            company_name=args.company,
            year=args.year,
            instrument_type=args.instrument,
        )

    print("=== fundraising query ===")
    print(f"company                         {args.company}")
    print(f"year                            {args.year}")
    print(f"instrument                      {args.instrument or 'all'}")
    print(f"events                          {len(rows)}")

    for row in rows:
        issue_date = row.issue_date.isoformat() if row.issue_date is not None else "unknown"
        amount = f"{row.amount_krw:,}" if row.amount_krw is not None else "unknown"
        print(
            f"{issue_date} type={row.instrument_type} amount_krw={amount} "
            f"sources={row.source_count}"
        )
        if row.series:
            print(f"  series={row.series!r}")
        if row.issuance_method:
            print(f"  method={row.issuance_method!r}")
        print(
            f"  source={row.representative_filing_id} "
            f"table={row.representative_table_id} row={row.representative_row_index}"
        )


if __name__ == "__main__":
    main()
