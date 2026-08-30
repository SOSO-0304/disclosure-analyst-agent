#!/usr/bin/env python3
"""Query correction-aware latest facility-investment states."""

from __future__ import annotations

import argparse
from datetime import date

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.facility_investment_query_repository import (
    FacilityInvestmentQueryRepository,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--company", action="append", default=[])
    parser.add_argument("--min-amount-krw", type=int)
    parser.add_argument("--decision-from", type=date.fromisoformat)
    parser.add_argument("--decision-to", type=date.fromisoformat)
    parser.add_argument("--require-complete-lineage", action="store_true")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        rows = FacilityInvestmentQueryRepository(session).list_latest(
            company_names=tuple(args.company),
            min_amount_krw=args.min_amount_krw,
            decision_date_from=args.decision_from,
            decision_date_to=args.decision_to,
            require_complete_lineage=args.require_complete_lineage,
            limit=args.limit,
        )

    print("=== latest facility investments ===")
    print(f"rows {len(rows)}")
    for row in rows:
        amount = f"{row.investment_amount_krw:,}" if row.investment_amount_krw is not None else "-"
        ratio = f"{row.equity_ratio}%" if row.equity_ratio is not None else "-"
        print(
            f"{row.company_name} amount={amount} ratio={ratio} "
            f"decision={row.decision_date} latest={row.latest_receipt_date}"
        )
        print(
            f"  lineage={row.lineage_status} complete={row.lineage_complete} "
            f"corrections={row.correction_count}"
        )
        print(f"  subject={row.investment_subject!r}")
        print(f"  purpose={row.purpose!r}")
        print(f"  filing={row.latest_filing_id}")


if __name__ == "__main__":
    main()
