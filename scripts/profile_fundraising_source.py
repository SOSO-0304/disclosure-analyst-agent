#!/usr/bin/env python3
"""Profile periodic-report fundraising sections from persisted source and fact tables."""

from __future__ import annotations

import argparse

from sqlalchemy import func, select

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import SourceCompanyRow, SourceFilingRow, SourceSectionRow
from disclosure_agent.storage.generic_fact_models import GenericFactRow

TARGET_NEEDLE = "증권의 발행을 통한 자금조달"
BROAD_NEEDLE = "자금조달"


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
        title_text = func.coalesce(
            SourceSectionRow.title_normalized,
            SourceSectionRow.title_raw,
            "",
        )
        periodic_filings = (
            session.scalar(
                select(func.count())
                .select_from(SourceFilingRow)
                .where(SourceFilingRow.document_group == "periodic")
            )
            or 0
        )
        broad_titles = session.execute(
            select(title_text.label("title"), func.count())
            .join(SourceFilingRow, SourceFilingRow.filing_id == SourceSectionRow.filing_id)
            .where(
                SourceFilingRow.document_group == "periodic",
                title_text.ilike(f"%{BROAD_NEEDLE}%"),
            )
            .group_by(title_text)
            .order_by(func.count().desc(), title_text)
            .limit(args.top)
        ).all()
        target_section_ids = tuple(
            session.scalars(
                select(SourceSectionRow.section_id)
                .join(SourceFilingRow, SourceFilingRow.filing_id == SourceSectionRow.filing_id)
                .where(
                    SourceFilingRow.document_group == "periodic",
                    title_text.ilike(f"%{TARGET_NEEDLE}%"),
                )
            ).all()
        )
        target_filing_ids = tuple(
            session.scalars(
                select(SourceSectionRow.filing_id)
                .join(SourceFilingRow, SourceFilingRow.filing_id == SourceSectionRow.filing_id)
                .where(
                    SourceFilingRow.document_group == "periodic",
                    title_text.ilike(f"%{TARGET_NEEDLE}%"),
                )
                .distinct()
            ).all()
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
    print(f"target sections                 {len(target_section_ids)}")
    print(f"target filings                  {len(target_filing_ids)}")
    print(f"target companies                {target_company_count}")
    print(f"target facts                    {target_fact_count}")
    print(f"target numeric facts            {target_numeric_count}")

    print("\n=== periodic section titles containing 자금조달 ===")
    for title, count in broad_titles:
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
