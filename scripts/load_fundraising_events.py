#!/usr/bin/env python3
"""Materialise canonical fundraising events and all supporting source occurrences."""

from __future__ import annotations

import argparse

from disclosure_agent.services.fundraising_ingestion import ingest_fundraising_events
from disclosure_agent.storage.database import get_engine, session_scope


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument(
        "--no-strict-corpus-count",
        action="store_true",
        help="Do not require the accepted corpus count of 720 fundraising candidate tables.",
    )
    args = parser.parse_args()

    expected = None if args.no_strict_corpus_count else 720
    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        result = ingest_fundraising_events(
            session=session,
            expected_candidate_tables=expected,
        )

    print("=== fundraising load ===")
    print(f"target sections                 {result.target_sections}")
    print(f"candidate tables                {result.candidate_tables}")
    print(f"source occurrences              {result.source_occurrences}")
    print(f"canonical events                {result.canonical_events}")
    print(f"persisted source rows           {result.source_rows}")
    print(
        "issue-date coverage             "
        f"{result.issue_date_coverage}/{result.source_occurrences}"
    )
    print(
        "amount coverage                 "
        f"{result.amount_coverage}/{result.source_occurrences}"
    )
    print("\n=== canonical event counts by type ===")
    for instrument, count in result.type_counts.items():
        print(f"{instrument:<28} {count:>4}")


if __name__ == "__main__":
    main()
