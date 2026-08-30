#!/usr/bin/env python3
"""Extract and deduplicate fundraising events from PostgreSQL source-table grids."""

from __future__ import annotations

import argparse
import re
import unicodedata
from collections import Counter

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from disclosure_agent.extractors.fundraising import (
    FundraisingEvent,
    FundraisingInstrument,
    canonicalize_fundraising_occurrences,
    extract_fundraising_occurrences,
)
from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import (
    SourceBlockRow,
    SourceCompanyRow,
    SourceFilingRow,
    SourceSectionRow,
    SourceTableRow,
)

TARGET_TOKEN = "증권의발행을통한자금조달실적"
SEARCH_TERMS = ("유상증자", "전환사채", "신주인수권부사채", "교환사채")


def _compact(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text)


def _target_section_ids(session: Session) -> tuple[str, ...]:
    rows = session.execute(
        select(
            SourceSectionRow.section_id,
            SourceSectionRow.title_raw,
            SourceSectionRow.title_normalized,
        )
        .join(SourceFilingRow, SourceFilingRow.filing_id == SourceSectionRow.filing_id)
        .where(SourceFilingRow.document_group == "periodic")
    ).all()
    return tuple(
        row.section_id
        for row in rows
        if TARGET_TOKEN in _compact(row.title_normalized or row.title_raw)
    )


def _candidate_tables(session: Session, section_ids: tuple[str, ...], companies: list[str]):
    term_filters = [SourceTableRow.normalized_text.ilike(f"%{term}%") for term in SEARCH_TERMS]
    statement = (
        select(SourceTableRow, SourceFilingRow, SourceCompanyRow)
        .join(SourceBlockRow, SourceBlockRow.block_id == SourceTableRow.block_id)
        .join(SourceFilingRow, SourceFilingRow.filing_id == SourceTableRow.filing_id)
        .join(SourceCompanyRow, SourceCompanyRow.corp_code == SourceFilingRow.corp_code)
        .where(SourceBlockRow.section_id.in_(section_ids), or_(*term_filters))
        .order_by(SourceFilingRow.receipt_date, SourceTableRow.table_id)
    )
    if companies:
        statement = statement.where(
            or_(
                SourceCompanyRow.listed_name.in_(companies),
                SourceCompanyRow.corp_name.in_(companies),
            )
        )
    return session.execute(statement).all()


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
        section_ids = _target_section_ids(session)
        tables = _candidate_tables(session, section_ids, args.company)
        occurrences = []
        for table, filing, company in tables:
            occurrences.extend(
                extract_fundraising_occurrences(
                    filing_id=filing.filing_id,
                    corp_code=filing.corp_code,
                    company_name=company.listed_name,
                    receipt_date=filing.receipt_date,
                    table_id=table.table_id,
                    grid=table.grid,
                    normalized_text=table.normalized_text,
                )
            )

    events = canonicalize_fundraising_occurrences(occurrences)
    raw_counts = Counter(item.instrument_type.value for item in occurrences)
    event_counts = Counter(event.occurrence.instrument_type.value for event in events)
    year_events = [
        event
        for event in events
        if event.occurrence.issue_date is not None
        and event.occurrence.issue_date.year == args.year
    ]

    print("=== fundraising extraction ===")
    print(f"target sections                 {len(section_ids)}")
    print(f"candidate tables                {len(tables)}")
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
