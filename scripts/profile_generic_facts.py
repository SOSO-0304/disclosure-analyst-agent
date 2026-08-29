#!/usr/bin/env python3
"""Profile promoted generic facts before designing higher-level typed extractors."""

from __future__ import annotations

import argparse
from collections.abc import Iterable

from sqlalchemy import func, or_, select

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import SourceFilingRow
from disclosure_agent.storage.generic_fact_models import GenericFactRow


def _print_pairs(title: str, rows: Iterable[tuple[object, int]]) -> None:
    print(f"\n=== {title} ===")
    for key, count in rows:
        rendered = "<NULL>" if key is None else str(key)
        print(f"{count:>10}  {rendered}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument(
        "--contains",
        action="append",
        default=[],
        help="Case-insensitive substring to sample from label/header/path/value; may be repeated.",
    )
    parser.add_argument("--sample-limit", type=int, default=20)
    args = parser.parse_args()
    if args.top < 1:
        parser.error("--top must be at least 1")
    if args.sample_limit < 1:
        parser.error("--sample-limit must be at least 1")

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        total = session.scalar(select(func.count()).select_from(GenericFactRow)) or 0
        numeric = (
            session.scalar(
                select(func.count())
                .select_from(GenericFactRow)
                .where(GenericFactRow.numeric_value.is_not(None))
            )
            or 0
        )
        concepts = (
            session.scalar(
                select(func.count())
                .select_from(GenericFactRow)
                .where(GenericFactRow.concept_code.is_not(None))
            )
            or 0
        )

        kind_rows = session.execute(
            select(GenericFactRow.fact_kind, func.count())
            .group_by(GenericFactRow.fact_kind)
            .order_by(func.count().desc(), GenericFactRow.fact_kind)
        ).all()
        group_rows = session.execute(
            select(SourceFilingRow.document_group, func.count())
            .join(GenericFactRow, GenericFactRow.filing_id == SourceFilingRow.filing_id)
            .group_by(SourceFilingRow.document_group)
            .order_by(func.count().desc(), SourceFilingRow.document_group)
        ).all()
        top_concepts = session.execute(
            select(GenericFactRow.concept_code, func.count())
            .where(GenericFactRow.concept_code.is_not(None))
            .group_by(GenericFactRow.concept_code)
            .order_by(func.count().desc(), GenericFactRow.concept_code)
            .limit(args.top)
        ).all()
        top_labels = session.execute(
            select(GenericFactRow.label_text, func.count())
            .where(GenericFactRow.label_text.is_not(None))
            .group_by(GenericFactRow.label_text)
            .order_by(func.count().desc(), GenericFactRow.label_text)
            .limit(args.top)
        ).all()

        print("=== generic fact profile ===")
        print(f"total facts                    {total}")
        print(f"numeric facts                  {numeric}")
        print(f"concept-coded facts            {concepts}")
        print(f"non-numeric facts              {total - numeric}")

        _print_pairs("fact kind", kind_rows)
        _print_pairs("document group", group_rows)
        _print_pairs(f"top {args.top} concept codes", top_concepts)
        _print_pairs(f"top {args.top} labels", top_labels)

        for needle in args.contains:
            pattern = f"%{needle}%"
            matches = session.execute(
                select(
                    SourceFilingRow.corp_code,
                    SourceFilingRow.report_name,
                    GenericFactRow.fact_kind,
                    GenericFactRow.label_text,
                    GenericFactRow.header_text,
                    GenericFactRow.value_text,
                    GenericFactRow.path_text,
                    GenericFactRow.filing_id,
                    GenericFactRow.table_id,
                    GenericFactRow.row_index,
                    GenericFactRow.column_index,
                )
                .join(GenericFactRow, GenericFactRow.filing_id == SourceFilingRow.filing_id)
                .where(
                    or_(
                        GenericFactRow.label_text.ilike(pattern),
                        GenericFactRow.header_text.ilike(pattern),
                        GenericFactRow.path_text.ilike(pattern),
                        GenericFactRow.value_text.ilike(pattern),
                    )
                )
                .order_by(SourceFilingRow.receipt_date.desc(), GenericFactRow.fact_id)
                .limit(args.sample_limit)
            ).all()

            print(f"\n=== sample contains: {needle!r} ({len(matches)}) ===")
            for row in matches:
                print(
                    f"corp={row.corp_code} report={row.report_name} kind={row.fact_kind} "
                    f"label={row.label_text!r} header={row.header_text!r} "
                    f"value={row.value_text!r}"
                )
                print(
                    f"  filing={row.filing_id} table={row.table_id} "
                    f"cell=({row.row_index},{row.column_index}) path={row.path_text!r}"
                )


if __name__ == "__main__":
    main()
