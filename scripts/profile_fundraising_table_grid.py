#!/usr/bin/env python3
"""Profile fundraising instruments from full source-table grids."""

from __future__ import annotations

import argparse
import re
import unicodedata
from collections import Counter
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import (
    SourceBlockRow,
    SourceCompanyRow,
    SourceFilingRow,
    SourceSectionRow,
    SourceTableRow,
)

TARGET_TOKEN = "증권의발행을통한자금조달실적"
INSTRUMENT_TERMS = {
    "rights_issue": "유상증자",
    "convertible_bond": "전환사채",
    "bond_with_warrants": "신주인수권부사채",
    "exchangeable_bond": "교환사채",
}
SHARE_HEADER_TOKENS = (
    "주식발행감소일자",
    "발행감소형태",
    "주당발행감소가액",
)
BOND_AMOUNT_TOKENS = (
    "권면전자등록총액",
    "권면총액",
    "사채의권면총액",
    "발행금액",
)
DATE_PATTERN = re.compile(
    r"20\d{2}\s*(?:년|[./-])\s*\d{1,2}\s*(?:월|[./-])\s*\d{1,2}"
)


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


def _cell_text(cell: dict[str, Any]) -> str:
    return str(cell.get("text_normalized") or cell.get("text_raw") or "").strip()


def _logical_rows(table: SourceTableRow) -> tuple[tuple[dict[str, Any], ...], ...]:
    rows: list[list[dict[str, Any]]] = [[] for _ in range(table.row_count)]
    grid = table.grid or {}
    cells = grid.get("cells", [])
    if not isinstance(cells, list):
        return tuple(tuple(row) for row in rows)

    for cell in cells:
        if not isinstance(cell, dict):
            continue
        row_index = int(cell.get("row_index", 0))
        row_span = max(int(cell.get("row_span", 1)), 1)
        stop = min(row_index + row_span, table.row_count)
        for logical_row in range(row_index, stop):
            rows[logical_row].append(cell)

    return tuple(
        tuple(sorted(row, key=lambda item: int(item.get("column_index", 0))))
        for row in rows
    )


def _row_text(row: tuple[dict[str, Any], ...]) -> str:
    values = [_cell_text(cell) for cell in row]
    return " | ".join(value for value in values if value)


def _header_text(table: SourceTableRow) -> str:
    logical_rows = _logical_rows(table)
    values = []
    for row_index in table.header_row_indices:
        if 0 <= row_index < len(logical_rows):
            values.append(_row_text(logical_rows[row_index]))
    return " | ".join(value for value in values if value)


def _shape(table: SourceTableRow, instrument: str) -> str:
    header = _compact(_header_text(table))
    text = _compact(table.normalized_text)

    if instrument == "rights_issue":
        matches = sum(token in header for token in SHARE_HEADER_TOKENS)
        if matches >= 2:
            return "share_issuance_matrix"
        return "reference_only"

    has_amount = any(token in header for token in BOND_AMOUNT_TOKENS)
    if "발행일" in header and has_amount:
        return "bond_issuance_matrix"

    has_detail_fields = all(token in text for token in ("발행일", "발행금액", "발행방법"))
    if has_detail_fields:
        return "bond_detail"
    return "reference_only"


def _interesting_rows(
    table: SourceTableRow,
    term: str,
) -> tuple[tuple[int, str], ...]:
    rows = []
    for row_index, row in enumerate(_logical_rows(table)):
        text = _row_text(row)
        if not text:
            continue
        compact = _compact(text)
        has_term = term in text
        has_date = DATE_PATTERN.search(text) is not None
        has_amount = any(token in compact for token in BOND_AMOUNT_TOKENS)
        has_share_form = "유상증자" in text
        if has_term or (has_date and (has_amount or has_share_form)):
            rows.append((row_index, text))
    return tuple(rows)


def _matching_tables(
    session: Session,
    section_ids: tuple[str, ...],
    term: str,
) -> tuple[tuple[SourceTableRow, SourceFilingRow, SourceCompanyRow], ...]:
    pattern = f"%{term}%"
    rows = session.execute(
        select(SourceTableRow, SourceFilingRow, SourceCompanyRow)
        .join(SourceBlockRow, SourceBlockRow.block_id == SourceTableRow.block_id)
        .join(SourceFilingRow, SourceFilingRow.filing_id == SourceTableRow.filing_id)
        .join(SourceCompanyRow, SourceCompanyRow.corp_code == SourceFilingRow.corp_code)
        .where(
            SourceBlockRow.section_id.in_(section_ids),
            or_(
                SourceTableRow.normalized_text.ilike(pattern),
                SourceTableRow.caption_normalized.ilike(pattern),
                SourceTableRow.caption_raw.ilike(pattern),
            ),
        )
        .order_by(SourceFilingRow.receipt_date, SourceTableRow.table_id)
    ).all()
    return tuple(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--sample-limit", type=int, default=10)
    parser.add_argument("--row-limit", type=int, default=8)
    args = parser.parse_args()
    if args.sample_limit < 1:
        parser.error("--sample-limit must be at least 1")
    if args.row_limit < 1:
        parser.error("--row-limit must be at least 1")

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        section_ids = _target_section_ids(session)
        profiles = {}
        for instrument, term in INSTRUMENT_TERMS.items():
            tables = _matching_tables(session, section_ids, term)
            shapes = Counter(_shape(table, instrument) for table, _filing, _company in tables)
            profiles[instrument] = {
                "term": term,
                "tables": tables,
                "shapes": shapes,
            }

    print("=== fundraising source-table grid profile ===")
    print(f"target sections                 {len(section_ids)}")

    for instrument, profile in profiles.items():
        tables = profile["tables"]
        print(f"\n=== {instrument} term={profile['term']!r} ===")
        print(f"matching full-source tables      {len(tables)}")
        for shape, count in sorted(profile["shapes"].items()):
            print(f"{shape:<30} {count}")

        for table, filing, company in tables[: args.sample_limit]:
            shape = _shape(table, instrument)
            summary = f"{filing.receipt_date} {company.listed_name}"
            print(f"  {summary} report={filing.report_name}")
            print(f"    table={table.table_id} shape={shape}")
            header = _header_text(table)
            if header:
                print(f"    headers={header[:600]!r}")
            rows = _interesting_rows(table, profile["term"])
            for row_index, text in rows[: args.row_limit]:
                print(f"    row={row_index} {text[:900]}")


if __name__ == "__main__":
    main()
