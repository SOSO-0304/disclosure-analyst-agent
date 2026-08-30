#!/usr/bin/env python3
"""Profile periodic-report fundraising sections from persisted source and fact tables."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict

from sqlalchemy import func, select

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import SourceCompanyRow, SourceFilingRow, SourceSectionRow
from disclosure_agent.storage.generic_fact_models import GenericFactRow

TARGET_TOKEN = "증권의발행을통한자금조달"
BROAD_TOKEN = "자금조달"


def _compact_title(value: str) -> str:
    return "".join(value.split())


def _collect_descendants(
    root_ids: tuple[str, ...],
    children: dict[str | None, list[str]],
) -> tuple[str, ...]:
    selected = set(root_ids)
    pending = list(root_ids)
    while pending:
        section_id = pending.pop()
        for child_id in children.get(section_id, ()):
            if child_id in selected:
                continue
            selected.add(child_id)
            pending.append(child_id)
    return tuple(sorted(selected))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--sample-limit", type=int, default=30)
    args = parser.parse_args()
    if args.top < 1:
        parser.error("--top must be at least 1")
    if args.sample_limit < 1:
        parser.error("--sample-limit must be at least 1")

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        periodic_filings = (
            session.scalar(
                select(func.count())
                .select_from(SourceFilingRow)
                .where(SourceFilingRow.document_group == "periodic")
            )
            or 0
        )
        section_rows = session.execute(
            select(
                SourceSectionRow.section_id,
                SourceSectionRow.parent_section_id,
                SourceSectionRow.filing_id,
                SourceSectionRow.title_raw,
                SourceSectionRow.title_normalized,
            )
            .join(SourceFilingRow, SourceFilingRow.filing_id == SourceSectionRow.filing_id)
            .where(SourceFilingRow.document_group == "periodic")
            .order_by(SourceSectionRow.filing_id, SourceSectionRow.section_order)
        ).all()

        broad_titles: Counter[str] = Counter()
        children: dict[str | None, list[str]] = defaultdict(list)
        filing_by_section: dict[str, str] = {}
        target_root_ids: list[str] = []

        for row in section_rows:
            children[row.parent_section_id].append(row.section_id)
            filing_by_section[row.section_id] = row.filing_id
            title = row.title_normalized or row.title_raw or ""
            compact = _compact_title(title)
            if BROAD_TOKEN in compact:
                broad_titles[title] += 1
            if TARGET_TOKEN in compact:
                target_root_ids.append(row.section_id)

        root_ids = tuple(sorted(target_root_ids))
        target_section_ids = _collect_descendants(root_ids, children)
        target_filing_ids = tuple(
            sorted({filing_by_section[section_id] for section_id in root_ids})
        )

        target_company_count = 0
        target_fact_count = 0
        target_numeric_count = 0
        top_labels: list[tuple[object, int]] = []
        top_headers: list[tuple[object, int]] = []
        samples: list[object] = []

        if target_filing_ids:
            target_company_count = (
                session.scalar(
                    select(func.count(func.distinct(SourceFilingRow.corp_code))).where(
                        SourceFilingRow.filing_id.in_(target_filing_ids)
                    )
                )
                or 0
            )

        if target_section_ids:
            target_fact_count = (
                session.scalar(
                    select(func.count())
                    .select_from(GenericFactRow)
                    .where(GenericFactRow.section_id.in_(target_section_ids))
                )
                or 0
            )
            target_numeric_count = (
                session.scalar(
                    select(func.count())
                    .select_from(GenericFactRow)
                    .where(
                        GenericFactRow.section_id.in_(target_section_ids),
                        GenericFactRow.numeric_value.is_not(None),
                    )
                )
                or 0
            )
            top_labels = session.execute(
                select(GenericFactRow.label_text, func.count())
                .where(
                    GenericFactRow.section_id.in_(target_section_ids),
                    GenericFactRow.label_text.is_not(None),
                )
                .group_by(GenericFactRow.label_text)
                .order_by(func.count().desc(), GenericFactRow.label_text)
                .limit(args.top)
            ).all()
            top_headers = session.execute(
                select(GenericFactRow.header_text, func.count())
                .where(
                    GenericFactRow.section_id.in_(target_section_ids),
                    GenericFactRow.header_text.is_not(None),
                )
                .group_by(GenericFactRow.header_text)
                .order_by(func.count().desc(), GenericFactRow.header_text)
                .limit(args.top)
            ).all()
            samples = session.execute(
                select(
                    SourceCompanyRow.listed_name,
                    SourceFilingRow.report_name,
                    SourceFilingRow.receipt_date,
                    GenericFactRow.label_text,
                    GenericFactRow.header_text,
                    GenericFactRow.value_text,
                    GenericFactRow.numeric_value,
                    GenericFactRow.path_text,
                    GenericFactRow.filing_id,
                    GenericFactRow.table_id,
                    GenericFactRow.row_index,
                    GenericFactRow.column_index,
                )
                .join(SourceFilingRow, SourceFilingRow.filing_id == GenericFactRow.filing_id)
                .join(SourceCompanyRow, SourceCompanyRow.corp_code == SourceFilingRow.corp_code)
                .where(GenericFactRow.section_id.in_(target_section_ids))
                .order_by(SourceFilingRow.receipt_date.desc(), GenericFactRow.fact_id)
                .limit(args.sample_limit)
            ).all()

    print("=== fundraising source profile ===")
    print(f"periodic filings                {periodic_filings}")
    print(f"target root sections            {len(root_ids)}")
    print(f"target sections + descendants   {len(target_section_ids)}")
    print(f"target filings                  {len(target_filing_ids)}")
    print(f"target companies                {target_company_count}")
    print(f"target facts                    {target_fact_count}")
    print(f"target numeric facts            {target_numeric_count}")

    print("\n=== periodic section titles containing 자금조달 ===")
    for title, count in broad_titles.most_common(args.top):
        print(f"{count:>6}  {title}")

    print("\n=== top labels in target sections ===")
    for label, count in top_labels:
        print(f"{count:>6}  {label}")

    print("\n=== top headers in target sections ===")
    for header, count in top_headers:
        print(f"{count:>6}  {header}")

    print("\n=== target fact samples ===")
    for row in samples:
        print(
            f"{row.receipt_date} {row.listed_name} report={row.report_name} "
            f"label={row.label_text!r} header={row.header_text!r} value={row.value_text!r}"
        )
        print(
            f"  numeric={row.numeric_value} filing={row.filing_id} table={row.table_id} "
            f"cell=({row.row_index},{row.column_index})"
        )
        print(f"  path={row.path_text!r}")


if __name__ == "__main__":
    main()
