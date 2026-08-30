#!/usr/bin/env python3
"""Profile row-shaped fundraising events inside periodic report section 7-1."""

from __future__ import annotations

import argparse
import re
import unicodedata

from sqlalchemy import or_, select

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import (
    SourceCompanyRow,
    SourceFilingRow,
    SourceSectionRow,
)
from disclosure_agent.storage.generic_fact_models import GenericFactRow

TARGET_TOKEN = "증권의발행을통한자금조달실적"
INSTRUMENT_TERMS = {
    "rights_issue": "유상증자",
    "convertible_bond": "전환사채",
    "bond_with_warrants": "신주인수권부사채",
    "exchangeable_bond": "교환사채",
}
BOND_AMOUNT_HEADERS = (
    "권면총액",
    "권면전자등록총액",
    "발행액",
    "발행금액",
)
RIGHTS_SHAPE_HEADERS = (
    "발행감소한주식의내용",
    "주당발행감소가액",
)
DATE_PATTERN = re.compile(
    r"20\d{2}\s*(?:년|[./-])\s*\d{1,2}\s*(?:월|[./-])\s*\d{1,2}",
)


def _compact(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text)


def _target_section_ids(session) -> tuple[str, ...]:
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


def _candidate_row_keys(session, section_ids: tuple[str, ...], term: str):
    pattern = f"%{term}%"
    return session.execute(
        select(
            GenericFactRow.filing_id,
            GenericFactRow.table_id,
            GenericFactRow.row_index,
        )
        .where(
            GenericFactRow.section_id.in_(section_ids),
            or_(
                GenericFactRow.label_text.ilike(pattern),
                GenericFactRow.value_text.ilike(pattern),
            ),
        )
        .distinct()
        .order_by(
            GenericFactRow.filing_id,
            GenericFactRow.table_id,
            GenericFactRow.row_index,
        )
    ).all()


def _row_facts(session, table_id: str, row_index: int):
    return tuple(
        session.scalars(
            select(GenericFactRow)
            .where(
                GenericFactRow.table_id == table_id,
                GenericFactRow.row_index == row_index,
            )
            .order_by(GenericFactRow.column_index)
        ).all()
    )


def _row_text(facts) -> str:
    parts = []
    for fact in facts:
        if fact.label_text:
            parts.append(fact.label_text)
        parts.append(fact.value_text)
    return " | ".join(parts)


def _has_date(facts) -> bool:
    return DATE_PATTERN.search(_row_text(facts)) is not None


def _has_bond_amount(facts) -> bool:
    for fact in facts:
        if fact.numeric_value is None:
            continue
        header = _compact(fact.header_text)
        if any(token in header for token in BOND_AMOUNT_HEADERS):
            return True
    return False


def _has_rights_shape(facts) -> bool:
    headers = [_compact(fact.header_text) for fact in facts]
    return any(token in header for header in headers for token in RIGHTS_SHAPE_HEADERS)


def _is_structured_candidate(instrument: str, term: str, facts) -> bool:
    if term not in _row_text(facts):
        return False
    if not _has_date(facts):
        return False
    if instrument == "rights_issue":
        return _has_rights_shape(facts)
    return _has_bond_amount(facts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--sample-limit", type=int, default=12)
    args = parser.parse_args()
    if args.sample_limit < 1:
        parser.error("--sample-limit must be at least 1")

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        section_ids = _target_section_ids(session)
        profiles = {}
        filing_ids = set()

        for instrument, term in INSTRUMENT_TERMS.items():
            raw_rows = _candidate_row_keys(session, section_ids, term)
            accepted = []
            rejected = []
            for row in raw_rows:
                facts = _row_facts(session, row.table_id, row.row_index)
                item = (row, facts)
                if _is_structured_candidate(instrument, term, facts):
                    accepted.append(item)
                    filing_ids.add(row.filing_id)
                else:
                    rejected.append(item)
            profiles[instrument] = {
                "term": term,
                "raw": raw_rows,
                "accepted": accepted,
                "rejected": rejected,
            }

        filing_meta = {}
        if filing_ids:
            rows = session.execute(
                select(
                    SourceFilingRow.filing_id,
                    SourceFilingRow.receipt_date,
                    SourceFilingRow.report_name,
                    SourceCompanyRow.listed_name,
                )
                .join(
                    SourceCompanyRow,
                    SourceCompanyRow.corp_code == SourceFilingRow.corp_code,
                )
                .where(SourceFilingRow.filing_id.in_(filing_ids))
            ).all()
            filing_meta = {row.filing_id: row for row in rows}

    print("=== fundraising structured-row profile ===")
    print(f"target sections                 {len(section_ids)}")

    for instrument, profile in profiles.items():
        raw_count = len(profile["raw"])
        accepted = profile["accepted"]
        rejected = profile["rejected"]
        print(f"\n=== {instrument} term={profile['term']!r} ===")
        print(f"raw matching rows               {raw_count}")
        print(f"structured candidate rows       {len(accepted)}")
        print(f"rejected keyword rows           {len(rejected)}")

        for row, facts in accepted[: args.sample_limit]:
            meta = filing_meta[row.filing_id]
            prefix = f"{meta.receipt_date} {meta.listed_name} report={meta.report_name}"
            print(f"  {prefix}")
            print(f"    filing={row.filing_id}")
            print(f"    table={row.table_id} row={row.row_index}")
            for fact in facts:
                header = fact.header_text or "-"
                label = fact.label_text or "-"
                value = fact.value_text
                print(f"    c{fact.column_index} header={header!r}")
                print(f"      label={label!r}")
                print(f"      value={value!r} numeric={fact.numeric_value}")


if __name__ == "__main__":
    main()
