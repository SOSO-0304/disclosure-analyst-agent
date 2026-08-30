#!/usr/bin/env python3
"""Profile periodic-report fundraising sections from persisted source and fact tables."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict

from sqlalchemy import func, or_, select

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import (
    SourceCompanyRow,
    SourceFilingRow,
    SourceSectionRow,
    SourceTableRow,
)
from disclosure_agent.storage.generic_fact_models import GenericFactRow

TARGET_TOKEN = "증권의발행을통한자금조달실적"
BROAD_TOKEN = "자금조달"
INSTRUMENT_TERMS: dict[str, tuple[str, ...]] = {
    "rights_issue": ("유상증자",),
    "convertible_bond": ("전환사채",),
    "bond_with_warrants": ("신주인수권부사채",),
    "exchangeable_bond": ("교환사채",),
}


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


def _instrument_match_condition(terms: tuple[str, ...]):
    predicates = []
    for term in terms:
        pattern = f"%{term}%"
        predicates.extend(
            [
                GenericFactRow.label_text.ilike(pattern),
                GenericFactRow.header_text.ilike(pattern),
                GenericFactRow.value_text.ilike(pattern),
                GenericFactRow.path_text.ilike(pattern),
            ]
        )
    return or_(*predicates)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--sample-limit", type=int, default=30)
    parser.add_argument("--instrument-sample-limit", type=int, default=6)
    args = parser.parse_args()
    if args.top < 1:
        parser.error("--top must be at least 1")
    if args.sample_limit < 1:
        parser.error("--sample-limit must be at least 1")
    if args.instrument_sample_limit < 1:
        parser.error("--instrument-sample-limit must be at least 1")

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
        instrument_profiles: dict[str, dict[str, object]] = {}
        missing_targets: list[object] = []

        if target_filing_ids:
            target_company_count = (
                session.scalar(
                    select(func.count(func.distinct(SourceFilingRow.corp_code))).where(
                        SourceFilingRow.filing_id.in_(target_filing_ids)
                    )
                )
                or 0
            )
            missing_targets = session.execute(
                select(
                    SourceCompanyRow.listed_name,
                    SourceFilingRow.report_name,
                    SourceFilingRow.receipt_date,
                    SourceFilingRow.filing_id,
                )
                .join(
                    SourceCompanyRow,
                    SourceCompanyRow.corp_code == SourceFilingRow.corp_code,
                )
                .where(
                    SourceFilingRow.document_group == "periodic",
                    SourceFilingRow.filing_id.not_in(target_filing_ids),
                )
                .order_by(SourceFilingRow.receipt_date, SourceFilingRow.filing_id)
            ).all()

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

            for instrument, terms in INSTRUMENT_TERMS.items():
                match = _instrument_match_condition(terms)
                counts = session.execute(
                    select(
                        func.count(func.distinct(GenericFactRow.filing_id)),
                        func.count(func.distinct(SourceFilingRow.corp_code)),
                        func.count(func.distinct(GenericFactRow.table_id)),
                        func.count(),
                    )
                    .select_from(GenericFactRow)
                    .join(
                        SourceFilingRow,
                        SourceFilingRow.filing_id == GenericFactRow.filing_id,
                    )
                    .where(
                        GenericFactRow.section_id.in_(target_section_ids),
                        match,
                    )
                ).one()
                table_ids = tuple(
                    session.scalars(
                        select(GenericFactRow.table_id)
                        .where(
                            GenericFactRow.section_id.in_(target_section_ids),
                            match,
                        )
                        .distinct()
                        .order_by(GenericFactRow.table_id)
                        .limit(args.instrument_sample_limit)
                    ).all()
                )
                table_samples = []
                if table_ids:
                    table_samples = session.execute(
                        select(
                            SourceCompanyRow.listed_name,
                            SourceFilingRow.report_name,
                            SourceFilingRow.receipt_date,
                            SourceTableRow.table_id,
                            SourceTableRow.normalized_text,
                        )
                        .join(
                            SourceFilingRow,
                            SourceFilingRow.filing_id == SourceTableRow.filing_id,
                        )
                        .join(
                            SourceCompanyRow,
                            SourceCompanyRow.corp_code == SourceFilingRow.corp_code,
                        )
                        .where(SourceTableRow.table_id.in_(table_ids))
                        .order_by(SourceFilingRow.receipt_date.desc(), SourceTableRow.table_id)
                    ).all()
                instrument_profiles[instrument] = {
                    "terms": terms,
                    "filings": counts[0],
                    "companies": counts[1],
                    "tables": counts[2],
                    "facts": counts[3],
                    "samples": table_samples,
                }

    print("=== fundraising source profile ===")
    print(f"periodic filings                {periodic_filings}")
    print(f"target root sections            {len(root_ids)}")
    print(f"target sections + descendants   {len(target_section_ids)}")
    print(f"target filings                  {len(target_filing_ids)}")
    print(f"target companies                {target_company_count}")
    print(f"target facts                    {target_fact_count}")
    print(f"target numeric facts            {target_numeric_count}")

    print("\n=== periodic filings missing target section ===")
    if not missing_targets:
        print("none")
    for row in missing_targets:
        summary = f"{row.receipt_date} {row.listed_name} report={row.report_name}"
        print(f"{summary} filing={row.filing_id}")

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

    print("\n=== target instrument coverage ===")
    for instrument, profile in instrument_profiles.items():
        print(
            f"{instrument:<22} filings={profile['filings']} companies={profile['companies']} "
            f"tables={profile['tables']} facts={profile['facts']} terms={profile['terms']}"
        )
        for row in profile["samples"]:
            text = " ".join((row.normalized_text or "").split())
            if len(text) > 900:
                text = f"{text[:900]}..."
            print(
                f"  {row.receipt_date} {row.listed_name} report={row.report_name} "
                f"table={row.table_id}"
            )
            print(f"    {text}")


if __name__ == "__main__":
    main()
