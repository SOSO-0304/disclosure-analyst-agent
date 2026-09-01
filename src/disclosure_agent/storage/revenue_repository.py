"""Deterministic SQL retrieval for annual consolidated revenue."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from disclosure_agent.storage.db_models import (
    SourceBlockRow,
    SourceCompanyRow,
    SourceFilingRow,
    SourceTableRow,
)
from disclosure_agent.storage.generic_fact_models import GenericFactRow

REVENUE_TERMS = ("매출액", "영업수익")
PRIMARY_STATEMENT_TERMS = ("연결손익계산서", "연결포괄손익계산서")
UNIT_MULTIPLIERS = {
    "원": Decimal(1),
    "천원": Decimal(1_000),
    "백만원": Decimal(1_000_000),
    "억원": Decimal(100_000_000),
}
UNIT_PATTERN = re.compile(r"단위\s*[:：]?\s*(억원|백만원|천원|원)")
FISCAL_PERIOD_PATTERN = re.compile(r"제\s*(\d+)\s*(?:\([^)]*\)\s*)?기")


@dataclass(frozen=True, slots=True)
class RevenueCandidate:
    """One annual-report numeric fact that may represent consolidated revenue."""

    score: int
    company_name: str
    filing_id: str
    receipt_date: date
    report_name: str
    fact_id: str
    block_id: str
    table_id: str
    row_index: int
    column_index: int
    label_text: str
    header_text: str
    path_text: str
    raw_value: str
    numeric_value: Decimal
    unit_raw: str | None
    currency: str | None
    signals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RevenueQueryResult:
    """Deterministic answerability result for one annual consolidated revenue query."""

    status: str
    company_name: str
    year: int
    candidate: RevenueCandidate | None
    amount_krw: int | None
    resolved_unit: str | None
    alternatives: tuple[RevenueCandidate, ...]


def _normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", " ", text).strip()


def _compact(value: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", _normalize(value))


def score_revenue_fields(
    *,
    label_text: str | None,
    header_text: str | None,
    path_text: str,
    year: int,
) -> tuple[int, tuple[str, ...]]:
    """Score one generic fact using only grounded label, header, and path context."""

    label = _normalize(label_text)
    header = _normalize(header_text)
    path = _normalize(path_text)
    compact_label = _compact(label)
    compact_header = _compact(header)
    compact_path = _compact(path)
    score = 0
    signals: list[str] = []

    if compact_label in {"매출액", "영업수익"}:
        score += 120
        signals.append("exact_revenue_label")
    elif compact_label.startswith("I영업수익"):
        score += 110
        signals.append("statement_revenue_label")
    elif "매출액" in label or "영업수익" in label:
        score += 80
        signals.append("revenue_label")

    if compact_label.startswith("내부매출액"):
        score -= 160
        signals.append("internal_revenue_label")
    if compact_label.startswith("순매출액"):
        score -= 120
        signals.append("net_revenue_label")

    if any(_compact(term) in compact_path for term in PRIMARY_STATEMENT_TERMS):
        score += 180
        signals.append("primary_consolidated_statement")
    elif "연결감사보고서" in compact_path and "첨부연결재무제표" in compact_path:
        score += 160
        signals.append("audited_consolidated_financial_statements")

    if "연결" in compact_path:
        score += 30
        signals.append("consolidated_context")
    if "별도" in compact_path or "개별" in compact_path:
        score -= 200
        signals.append("separate_context")
    if str(year) in compact_header:
        score += 40
        signals.append("target_year_header")
    if "당기" in compact_header:
        score += 30
        signals.append("current_period_header")
    if "전기" in compact_header and "당기" not in compact_header:
        score -= 120
        signals.append("prior_period_header")
    if "주석" in compact_path:
        score -= 250
        signals.append("notes_context")
    if "요약재무정보" in compact_path:
        score += 20
        signals.append("summary_financial_context")

    return score, tuple(signals)


def is_primary_revenue_candidate(signals: tuple[str, ...]) -> bool:
    """Return whether the fact is a current-period primary consolidated statement value."""

    signal_set = set(signals)
    statement_signals = {
        "primary_consolidated_statement",
        "audited_consolidated_financial_statements",
    }
    period_signals = {
        "target_year_header",
        "current_period_header",
        "current_fiscal_period_header",
    }
    blocked_signals = {"notes_context", "separate_context", "prior_period_header"}
    has_statement = bool(signal_set & statement_signals)
    has_period = bool(signal_set & period_signals)
    has_blocker = bool(signal_set & blocked_signals)
    return has_statement and has_period and not has_blocker


def _fiscal_period_number(value: str | None) -> int | None:
    """Extract the numeric fiscal term from headers such as '제58기'."""

    match = FISCAL_PERIOD_PATTERN.search(_normalize(value))
    return int(match.group(1)) if match is not None else None


def _mark_current_fiscal_periods(
    candidates: tuple[RevenueCandidate, ...],
) -> tuple[RevenueCandidate, ...]:
    """Mark the highest fiscal term only within the same consolidated statement table."""

    statement_signals = {
        "primary_consolidated_statement",
        "audited_consolidated_financial_statements",
    }
    blocked_signals = {"notes_context", "separate_context", "prior_period_header"}
    explicit_period_signals = {"target_year_header", "current_period_header"}
    grouped: dict[tuple[str, str], list[tuple[str, int]]] = {}

    for candidate in candidates:
        signals = set(candidate.signals)
        if not signals & statement_signals:
            continue
        if signals & blocked_signals:
            continue
        if signals & explicit_period_signals:
            continue

        fiscal_period = _fiscal_period_number(candidate.header_text)
        if fiscal_period is None:
            continue
        grouped.setdefault((candidate.filing_id, candidate.table_id), []).append(
            (candidate.fact_id, fiscal_period)
        )

    current_fact_ids: set[str] = set()
    for rows in grouped.values():
        periods = {period for _, period in rows}
        if len(periods) < 2:
            continue
        current_period = max(periods)
        current_fact_ids.update(
            fact_id for fact_id, period in rows if period == current_period
        )

    return tuple(
        replace(
            candidate,
            score=candidate.score + 40,
            signals=(*candidate.signals, "current_fiscal_period_header"),
        )
        if candidate.fact_id in current_fact_ids
        else candidate
        for candidate in candidates
    )


def extract_monetary_unit(text: str | None) -> str | None:
    """Extract an explicit Korean monetary unit from nearby source text."""

    match = UNIT_PATTERN.search(_normalize(text))
    return match.group(1) if match is not None else None


def scale_to_krw(value: Decimal, unit: str | None) -> int | None:
    """Scale a numeric source value to KRW only when the monetary unit is explicit."""

    multiplier = UNIT_MULTIPLIERS.get(unit or "")
    if multiplier is None:
        return None
    scaled = value * multiplier
    if scaled != scaled.to_integral_value():
        return None
    return int(scaled)


class RevenueRepository:
    """Query annual consolidated revenue directly from persisted generic facts."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def query_annual_consolidated_revenue(
        self,
        *,
        company_name: str,
        year: int,
    ) -> RevenueQueryResult:
        """Resolve one annual consolidated revenue value conservatively."""

        candidates = self._read_candidates(company_name=company_name, year=year)
        primary = tuple(
            candidate for candidate in candidates if is_primary_revenue_candidate(candidate.signals)
        )
        ranked = tuple(
            sorted(
                primary,
                key=lambda item: (
                    -item.score,
                    -item.receipt_date.toordinal(),
                    item.filing_id,
                    item.table_id,
                    item.row_index,
                    item.column_index,
                ),
            )
        )
        if not ranked:
            return RevenueQueryResult(
                status="NO_MATCH",
                company_name=company_name,
                year=year,
                candidate=None,
                amount_krw=None,
                resolved_unit=None,
                alternatives=(),
            )

        top_score = ranked[0].score
        top = tuple(candidate for candidate in ranked if candidate.score == top_score)
        distinct_values = {candidate.numeric_value for candidate in top}
        if len(distinct_values) > 1:
            return RevenueQueryResult(
                status="AMBIGUOUS",
                company_name=company_name,
                year=year,
                candidate=None,
                amount_krw=None,
                resolved_unit=None,
                alternatives=top,
            )

        chosen = top[0]
        unit = self._resolve_unit(chosen)
        amount_krw = scale_to_krw(chosen.numeric_value, unit)
        status = "ANSWERABLE" if amount_krw is not None else "PARTIAL"
        return RevenueQueryResult(
            status=status,
            company_name=company_name,
            year=year,
            candidate=chosen,
            amount_krw=amount_krw,
            resolved_unit=unit,
            alternatives=top[1:],
        )

    def _read_candidates(self, *, company_name: str, year: int) -> tuple[RevenueCandidate, ...]:
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
                or_(
                    SourceCompanyRow.listed_name == company_name,
                    SourceCompanyRow.corp_name == company_name,
                ),
            )
            .order_by(
                SourceFilingRow.receipt_date.desc(),
                GenericFactRow.table_id,
                GenericFactRow.row_index,
                GenericFactRow.column_index,
            )
        )

        candidates = []
        for fact, filing, company in self.session.execute(statement):
            score, signals = score_revenue_fields(
                label_text=fact.label_text,
                header_text=fact.header_text,
                path_text=fact.path_text,
                year=year,
            )
            candidates.append(
                RevenueCandidate(
                    score=score,
                    company_name=company.listed_name,
                    filing_id=filing.filing_id,
                    receipt_date=filing.receipt_date,
                    report_name=filing.report_name,
                    fact_id=fact.fact_id,
                    block_id=fact.block_id,
                    table_id=fact.table_id,
                    row_index=fact.row_index,
                    column_index=fact.column_index,
                    label_text=_normalize(fact.label_text),
                    header_text=_normalize(fact.header_text),
                    path_text=_normalize(fact.path_text),
                    raw_value=fact.raw_value,
                    numeric_value=fact.numeric_value,
                    unit_raw=fact.unit_raw,
                    currency=fact.currency,
                    signals=signals,
                )
            )
        return _mark_current_fiscal_periods(tuple(candidates))

    def _resolve_unit(self, candidate: RevenueCandidate) -> str | None:
        direct_unit = extract_monetary_unit(candidate.unit_raw)
        if direct_unit is not None:
            return direct_unit

        block = self.session.get(SourceBlockRow, candidate.block_id)
        table = self.session.get(SourceTableRow, candidate.table_id)
        if block is None or table is None:
            return None

        table_texts = (
            table.caption_normalized or table.caption_raw or "",
            table.normalized_text,
        )
        for text in table_texts:
            unit = extract_monetary_unit(text)
            if unit is not None:
                return unit

        previous_unit = self._immediate_previous_table_unit(block)
        if previous_unit is not None:
            return previous_unit

        for nearby in self._nearby_blocks(block, same_section=True):
            unit = extract_monetary_unit(self._unit_text_from_block(nearby))
            if unit is not None:
                return unit

        for nearby in self._nearby_blocks(block, same_section=False):
            unit = extract_monetary_unit(self._unit_text_from_block(nearby))
            if unit is not None:
                return unit

        return None

    def _immediate_previous_table_unit(self, block: SourceBlockRow) -> str | None:
        previous = self.session.scalar(
            select(SourceBlockRow).where(
                SourceBlockRow.document_id == block.document_id,
                SourceBlockRow.section_id == block.section_id,
                SourceBlockRow.block_order == block.block_order - 1,
            )
        )
        if previous is None or previous.table_id is None:
            return None

        table = self.session.get(SourceTableRow, previous.table_id)
        if table is None:
            return None
        return extract_monetary_unit(table.normalized_text)

    def _nearby_blocks(
        self,
        block: SourceBlockRow,
        *,
        same_section: bool,
    ) -> tuple[SourceBlockRow, ...]:
        filters = [SourceBlockRow.document_id == block.document_id]
        if same_section:
            filters.append(SourceBlockRow.section_id == block.section_id)

        previous = self.session.scalars(
            select(SourceBlockRow)
            .where(*filters, SourceBlockRow.block_order < block.block_order)
            .order_by(SourceBlockRow.block_order.desc())
            .limit(24)
        ).all()
        following = self.session.scalars(
            select(SourceBlockRow)
            .where(*filters, SourceBlockRow.block_order > block.block_order)
            .order_by(SourceBlockRow.block_order)
            .limit(8)
        ).all()
        return tuple([*previous, *following])

    def _unit_text_from_block(self, block: SourceBlockRow) -> str:
        if block.block_type == "table":
            return self._adjacent_unit_table_text(block)
        text = block.text_normalized or block.text_raw or ""
        return text if "단위" in text else ""

    def _adjacent_unit_table_text(self, block: SourceBlockRow) -> str:
        if block.table_id is None:
            return ""
        table = self.session.get(SourceTableRow, block.table_id)
        if table is None or table.row_count > 2:
            return ""
        return table.normalized_text if "단위" in table.normalized_text else ""
