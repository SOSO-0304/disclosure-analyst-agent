#!/usr/bin/env python3
"""Profile 2025 annual-report revenue candidates from persisted generic facts."""

from __future__ import annotations

import argparse
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from disclosure_agent.storage.database import get_engine, session_scope
from disclosure_agent.storage.db_models import SourceCompanyRow, SourceFilingRow
from disclosure_agent.storage.generic_fact_models import GenericFactRow

REVENUE_TERMS = ("매출액", "영업수익")
CONSOLIDATED_TERMS = ("연결", "연결재무제표", "연결감사보고서")
SEPARATE_TERMS = ("별도", "개별", "별도재무제표")
STATEMENT_TERMS = ("손익계산서", "포괄손익계산서")
PRIMARY_CONSOLIDATED_TERMS = ("연결손익계산서", "연결포괄손익계산서")


@dataclass(frozen=True, slots=True)
class Candidate:
    """One scored generic fact that may represent annual consolidated revenue."""

    score: int
    company_name: str
    filing_id: str
    receipt_date: str
    report_name: str
    fact_id: str
    label_text: str
    header_text: str
    path_text: str
    value_text: str
    numeric_value: Decimal
    unit_raw: str | None
    currency: str | None
    table_id: str
    row_index: int
    column_index: int
    signals: tuple[str, ...]


def _normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", " ", text).strip()


def _compact(value: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", _normalize(value))


def _contains_compact(text: str, terms: tuple[str, ...]) -> bool:
    compact = _compact(text)
    return any(_compact(term) in compact for term in terms)


def _score_fact(fact: GenericFactRow, year: int) -> tuple[int, tuple[str, ...]]:
    label = _normalize(fact.label_text)
    header = _normalize(fact.header_text)
    path = _normalize(fact.path_text)
    combined = f"{label} {header} {path}"
    score = 0
    signals: list[str] = []

    compact_label = _compact(label)
    if compact_label == "매출액":
        score += 120
        signals.append("exact_revenue_label")
    elif compact_label in {"영업수익", "수익매출액"}:
        score += 100
        signals.append("revenue_equivalent_label")
    elif "매출액" in label:
        score += 70
        signals.append("revenue_label")
    elif "영업수익" in label:
        score += 60
        signals.append("operating_revenue_label")

    if compact_label.startswith("내부매출액"):
        score -= 120
        signals.append("internal_revenue_label")
    if compact_label.startswith("순매출액"):
        score -= 80
        signals.append("net_revenue_label")

    if _contains_compact(path, PRIMARY_CONSOLIDATED_TERMS):
        score += 160
        signals.append("primary_consolidated_statement")
    elif _contains_compact(path, STATEMENT_TERMS):
        score += 80
        signals.append("income_statement_context")

    if any(term in combined for term in CONSOLIDATED_TERMS):
        score += 30
        signals.append("consolidated_context")
    if any(term in combined for term in SEPARATE_TERMS):
        score -= 120
        signals.append("separate_context")
    if str(year) in header:
        score += 30
        signals.append("target_year_header")
    if "당기" in header:
        score += 20
        signals.append("current_period_header")
    if "주석" in path:
        score -= 180
        signals.append("notes_context")

    return score, tuple(signals)


def _read_candidates(
    session: Session,
    *,
    year: int,
    companies: tuple[str, ...],
) -> tuple[Candidate, ...]:
    text_filter = or_(
        *[
            column.ilike(f"%{term}%")
            for column in (
                GenericFactRow.label_text,
                GenericFactRow.header_text,
                GenericFactRow.path_text,
            )
            for term in REVENUE_TERMS
        ]
    )
    statement = (
        select(GenericFactRow, SourceFilingRow, SourceCompanyRow)
        .join(SourceFilingRow, SourceFilingRow.filing_id == GenericFactRow.filing_id)
        .join(SourceCompanyRow, SourceCompanyRow.corp_code == SourceFilingRow.corp_code)
        .where(
            SourceFilingRow.document_group == "periodic",
            SourceFilingRow.report_name.ilike("%사업보고서%"),
            SourceFilingRow.report_name.ilike(f"%{year}%"),
            GenericFactRow.numeric_value.is_not(None),
            text_filter,
        )
        .order_by(
            SourceCompanyRow.listed_name,
            SourceFilingRow.receipt_date,
            GenericFactRow.table_id,
            GenericFactRow.row_index,
            GenericFactRow.column_index,
        )
    )
    if companies:
        statement = statement.where(
            or_(
                SourceCompanyRow.listed_name.in_(companies),
                SourceCompanyRow.corp_name.in_(companies),
            )
        )

    candidates = []
    for fact, filing, company in session.execute(statement):
        score, signals = _score_fact(fact, year)
        candidates.append(
            Candidate(
                score=score,
                company_name=company.listed_name,
                filing_id=filing.filing_id,
                receipt_date=filing.receipt_date.isoformat(),
                report_name=filing.report_name,
                fact_id=fact.fact_id,
                label_text=_normalize(fact.label_text),
                header_text=_normalize(fact.header_text),
                path_text=_normalize(fact.path_text),
                value_text=fact.value_text,
                numeric_value=fact.numeric_value,
                unit_raw=fact.unit_raw,
                currency=fact.currency,
                table_id=fact.table_id,
                row_index=fact.row_index,
                column_index=fact.column_index,
                signals=signals,
            )
        )
    return tuple(candidates)


def _print_candidate(candidate: Candidate) -> None:
    print(
        f"score={candidate.score:>4} company={candidate.company_name} "
        f"value={candidate.numeric_value} unit={candidate.unit_raw!r}"
    )
    print(
        f"  filing={candidate.filing_id} receipt={candidate.receipt_date} "
        f"report={candidate.report_name!r}"
    )
    print(f"  label={candidate.label_text!r}")
    print(f"  header={candidate.header_text!r}")
    print(f"  path={candidate.path_text[:500]!r}")
    print(
        f"  table={candidate.table_id} row={candidate.row_index} "
        f"col={candidate.column_index} fact={candidate.fact_id}"
    )
    print(f"  signals={','.join(candidate.signals) or '-'}")


def _is_primary_statement(candidate: Candidate) -> bool:
    return (
        "primary_consolidated_statement" in candidate.signals
        and "notes_context" not in candidate.signals
        and "separate_context" not in candidate.signals
    )


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
        candidates = _read_candidates(
            session,
            year=args.year,
            companies=tuple(args.company),
        )

    ranked = sorted(
        candidates,
        key=lambda item: (
            -item.score,
            item.company_name,
            item.receipt_date,
            item.filing_id,
            item.table_id,
            item.row_index,
            item.column_index,
        ),
    )
    primary = [candidate for candidate in ranked if _is_primary_statement(candidate)]

    print("=== revenue candidate profile ===")
    print(f"year                            {args.year}")
    print(f"companies                       {len(set(item.company_name for item in candidates))}")
    print(f"candidate facts                 {len(candidates)}")
    print(f"primary statement candidates    {len(primary)}")
    print("\n=== primary statement candidates ===")
    for candidate in primary[: args.limit]:
        _print_candidate(candidate)

    if not primary:
        print("\n=== fallback top candidates ===")
        for candidate in ranked[: args.limit]:
            _print_candidate(candidate)


if __name__ == "__main__":
    main()
