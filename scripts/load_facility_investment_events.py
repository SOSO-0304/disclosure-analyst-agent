#!/usr/bin/env python3
"""Materialise Exchange 신규시설투자등 filings from the generic fact layer."""

from __future__ import annotations

import argparse

from disclosure_agent.services.facility_investment_ingestion import (
    ingest_facility_investment_events,
)
from disclosure_agent.storage.database import get_engine, session_scope


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument(
        "--no-strict-corpus-count",
        action="store_true",
        help="Do not require the accepted corpus count of 43 facility-investment filings.",
    )
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    expected = None if args.no_strict_corpus_count else 43
    with session_scope(engine) as session:
        result = ingest_facility_investment_events(
            session=session,
            expected_candidate_count=expected,
        )

    print("=== facility investment load ===")
    print(f"candidate filings               {result.candidate_filings}")
    print(f"events                          {result.events}")
    print(f"corrections                     {result.corrections}")
    print(f"evidence links                  {result.evidence}")
    print("\n=== field coverage ===")
    for field, count in result.coverage.items():
        print(f"{field:<32} {count:>4}/{result.events}")


if __name__ == "__main__":
    main()
