#!/usr/bin/env python3
"""Extract and deduplicate fundraising events from PostgreSQL source-table grids."""

from __future__ import annotations

import argparse
from collections import Counter

from disclosure_agent.extractors.fundraising import (
    FundraisingEvent,
    FundraisingInstrument,
    canonicalize_fundraising_occurrences,
    extract_fundraising_occurrences,
)
from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.fundraising_repository import FundraisingRepository


def _event_sort_key(event: FundraisingEvent):
    item = event.occurrence
    return (
        item.issue_date is None,
        item.issue_date,
        item.company_name,
        item.instrument_type.value,
        event.event_id,
    )


def _print_event(event: FundraisingEvent) -> None:
    item = event.occurrence
    amount = f"{item.amount_krw:,}" if item.amount_krw is not None else "unknown"
    issue_date = item.issue_date.isoformat() if item.issue_date is not None else "unknown"
    details = []
    if item.series:
        details.append(f"series={item.series!r}")
    if item.issuance_method:
        details.append(f"method={item.issuance_method!r}")
    if item.stock_kind:
        details.append(f"stock={item.stock_kind!r}")
    if item.share_quantity is not None:
        details.append(f"shares={item.share_quantity:,}")
    if item.issue_price_krw is not None:
        details.append(f"price={item.issue_price_krw:,}")

    print(
        f"{issue_date} {item.company_name} type={item.instrument_type.value} "
        f"amount_krw={amount} sources={event.source_count}"
    )
    if item.issuer_name != item.company_name:
        print(f"  issuer={item.issuer_name!r}")
    if item.security_name:
        print(f"  security={item.security_name!r}")
    if details:
        print(f"  {' '.join(details)}")
    print(f"  source={item.filing_id} table={item.table_id} row={item.row_index}")
    print(f"  evidence={item.evidence_text[:500]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--company", action="append", default=[])
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        candidates = FundraisingRepository(session).read_candidates(
            companies=tuple(args.company),
        )
        occurrences = []
        for table in candidates.tables:
            occurrences.extend(
                extract_fundraising_occurrences(
                    filing_id=table.filing_id,
                    corp_code=table.corp_code,
                    company_name=table.company_name,
                    receipt_date=table.receipt_date,
                    table_id=table.table_id,
                    grid=table.grid,
                    normalized_text=table.context_text,
                )
            )

    events = canonicalize_fundraising_occurrences(occurrences)
    raw_counts = Counter(item.instrument_type.value for item in occurrences)
    event_counts = Counter(event.occurrence.instrument_type.value for event in events)
    year_events = [
        event
        for event in events
        if event.occurrence.issue_date is not None and event.occurrence.issue_date.year == args.year
    ]

    print("=== fundraising extraction ===")
    print(f"target sections                 {candidates.target_section_count}")
    print(f"candidate tables                {len(candidates.tables)}")
    print(f"source occurrences              {len(occurrences)}")
    print(f"canonical events                {len(events)}")
    print(f"duplicates collapsed            {len(occurrences) - len(events)}")
    print(
        "issue-date coverage             "
        f"{sum(item.issue_date is not None for item in occurrences)}/{len(occurrences)}"
    )
    print(
        "amount coverage                 "
        f"{sum(item.amount_krw is not None for item in occurrences)}/{len(occurrences)}"
    )

    print("\n=== event counts by type ===")
    for instrument in FundraisingInstrument:
        name = instrument.value
        print(f"{name:<28} raw={raw_counts[name]:>4} canonical={event_counts[name]:>4}")

    print(f"\n=== {args.year} canonical events ===")
    print(f"events                          {len(year_events)}")
    for event in sorted(year_events, key=_event_sort_key)[: args.limit]:
        _print_event(event)

    undated = [event for event in events if event.occurrence.issue_date is None]
    if undated:
        print("\n=== undated canonical events ===")
        print(f"events                          {len(undated)}")
        for event in sorted(undated, key=_event_sort_key)[: min(args.limit, 20)]:
            _print_event(event)


if __name__ == "__main__":
    main()
