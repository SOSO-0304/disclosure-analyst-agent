"""Repository for fundraising source tables, canonical events, and SQL retrieval."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from disclosure_agent.extractors.fundraising import FundraisingEvent, FundraisingOccurrence
from disclosure_agent.storage.db_models import (
    SourceBlockRow,
    SourceCompanyRow,
    SourceFilingRow,
    SourceSectionRow,
    SourceTableRow,
)
from disclosure_agent.storage.fundraising_models import (
    FundraisingEventRow,
    FundraisingEventSourceRow,
)

TARGET_TOKEN = "증권의발행을통한자금조달실적"
SEARCH_TERMS = ("유상증자", "전환사채", "신주인수권부사채", "교환사채")


@dataclass(frozen=True, slots=True)
class FundraisingSourceTable:
    """One candidate source table with the local context needed for unit parsing."""

    filing_id: str
    corp_code: str
    company_name: str
    receipt_date: date
    table_id: str
    grid: dict[str, object]
    context_text: str


@dataclass(frozen=True, slots=True)
class FundraisingCandidateSet:
    """Deterministic section and source-table candidates for fundraising extraction."""

    target_section_count: int
    tables: tuple[FundraisingSourceTable, ...]


@dataclass(frozen=True, slots=True)
class FundraisingQueryResult:
    """One canonical fundraising event returned by deterministic SQL retrieval."""

    event_id: str
    company_name: str
    instrument_type: str
    issue_date: date | None
    amount_krw: int | None
    issuer_name: str
    security_name: str | None
    series: str | None
    issuance_method: str | None
    stock_kind: str | None
    share_quantity: int | None
    issue_price_krw: int | None
    source_count: int
    representative_filing_id: str
    representative_table_id: str
    representative_row_index: int


class FundraisingRepository:
    """Read source tables, replace typed projections, and query canonical events."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def read_candidates(self, *, companies: tuple[str, ...] = ()) -> FundraisingCandidateSet:
        """Load periodic-report 7-1 tables that mention one supported fundraising type."""

        section_ids = self._target_section_ids()
        term_filters = [SourceTableRow.normalized_text.ilike(f"%{term}%") for term in SEARCH_TERMS]
        statement = (
            select(SourceTableRow, SourceBlockRow, SourceFilingRow, SourceCompanyRow)
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

        rows = self.session.execute(statement).all()
        tables = tuple(
            FundraisingSourceTable(
                filing_id=filing.filing_id,
                corp_code=filing.corp_code,
                company_name=company.listed_name,
                receipt_date=filing.receipt_date,
                table_id=table.table_id,
                grid=table.grid,
                context_text=self._table_context_text(table, block),
            )
            for table, block, filing, company in rows
        )
        return FundraisingCandidateSet(
            target_section_count=len(section_ids),
            tables=tables,
        )

    def replace_events(
        self,
        *,
        events: tuple[FundraisingEvent, ...],
        occurrences: tuple[FundraisingOccurrence, ...],
    ) -> tuple[int, int]:
        """Atomically replace the small typed fundraising projection and its evidence rows."""

        grouped: dict[str, list[FundraisingOccurrence]] = {}
        for occurrence in occurrences:
            grouped.setdefault(occurrence.dedupe_key, []).append(occurrence)

        self.session.execute(delete(FundraisingEventSourceRow))
        self.session.execute(delete(FundraisingEventRow))

        event_rows = []
        source_rows = []
        for event in events:
            representative = event.occurrence
            sources = grouped.get(event.event_id, [])
            event_rows.append(
                {
                    "event_id": event.event_id,
                    "corp_code": representative.corp_code,
                    "instrument_type": representative.instrument_type.value,
                    "issuer_name": representative.issuer_name,
                    "issue_date": representative.issue_date,
                    "security_name": representative.security_name,
                    "series": representative.series,
                    "issuance_method": representative.issuance_method,
                    "stock_kind": representative.stock_kind,
                    "share_quantity": representative.share_quantity,
                    "issue_price_krw": representative.issue_price_krw,
                    "amount_krw": representative.amount_krw,
                    "amount_raw": representative.amount_raw,
                    "amount_unit": representative.amount_unit,
                    "representative_filing_id": representative.filing_id,
                    "representative_table_id": representative.table_id,
                    "representative_row_index": representative.row_index,
                    "source_count": len(sources),
                }
            )
            for source in sources:
                source_rows.append(self._source_row(event.event_id, source))

        if event_rows:
            self.session.execute(insert(FundraisingEventRow), event_rows)
        if source_rows:
            self.session.execute(insert(FundraisingEventSourceRow), source_rows)
        return len(event_rows), len(source_rows)

    def query_events(
        self,
        *,
        company_name: str,
        year: int,
        instrument_type: str | None = None,
    ) -> tuple[FundraisingQueryResult, ...]:
        """Return dated canonical events for one company and calendar year."""

        start = date(year, 1, 1)
        end = date(year + 1, 1, 1)
        statement = (
            select(FundraisingEventRow, SourceCompanyRow)
            .join(SourceCompanyRow, SourceCompanyRow.corp_code == FundraisingEventRow.corp_code)
            .where(
                or_(
                    SourceCompanyRow.listed_name == company_name,
                    SourceCompanyRow.corp_name == company_name,
                ),
                FundraisingEventRow.issue_date >= start,
                FundraisingEventRow.issue_date < end,
            )
            .order_by(
                FundraisingEventRow.issue_date,
                FundraisingEventRow.instrument_type,
                FundraisingEventRow.event_id,
            )
        )
        if instrument_type is not None:
            statement = statement.where(FundraisingEventRow.instrument_type == instrument_type)

        rows = self.session.execute(statement).all()
        return tuple(
            FundraisingQueryResult(
                event_id=event.event_id,
                company_name=company.listed_name,
                instrument_type=event.instrument_type,
                issue_date=event.issue_date,
                amount_krw=event.amount_krw,
                issuer_name=event.issuer_name,
                security_name=event.security_name,
                series=event.series,
                issuance_method=event.issuance_method,
                stock_kind=event.stock_kind,
                share_quantity=event.share_quantity,
                issue_price_krw=event.issue_price_krw,
                source_count=event.source_count,
                representative_filing_id=event.representative_filing_id,
                representative_table_id=event.representative_table_id,
                representative_row_index=event.representative_row_index,
            )
            for event, company in rows
        )

    def _target_section_ids(self) -> tuple[str, ...]:
        rows = self.session.execute(
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

    def _table_context_text(self, table: SourceTableRow, block: SourceBlockRow) -> str:
        previous_blocks = self.session.scalars(
            select(SourceBlockRow)
            .where(
                SourceBlockRow.document_id == block.document_id,
                SourceBlockRow.section_id == block.section_id,
                SourceBlockRow.block_order < block.block_order,
            )
            .order_by(SourceBlockRow.block_order.desc())
            .limit(12)
        ).all()

        preceding_text = []
        for previous in previous_blocks:
            if previous.block_type == "table":
                unit_text = self._adjacent_unit_table_text(previous)
                if unit_text:
                    preceding_text.append(unit_text)
                break
            text = previous.text_normalized or previous.text_raw or ""
            if text.strip():
                preceding_text.append(text.strip())
        preceding_text.reverse()

        caption = table.caption_normalized or table.caption_raw or ""
        parts = [*preceding_text, caption, table.normalized_text]
        return " ".join(part for part in parts if part)

    def _adjacent_unit_table_text(self, block: SourceBlockRow) -> str:
        if not block.table_id:
            return ""
        previous_table = self.session.scalar(
            select(SourceTableRow).where(SourceTableRow.table_id == block.table_id)
        )
        if previous_table is None or previous_table.row_count > 2:
            return ""
        text = previous_table.normalized_text.strip()
        if "단위" not in text:
            return ""
        return text

    @staticmethod
    def _source_row(event_id: str, source: FundraisingOccurrence) -> dict[str, object]:
        return {
            "source_id": _source_id(event_id, source),
            "event_id": event_id,
            "filing_id": source.filing_id,
            "table_id": source.table_id,
            "row_index": source.row_index,
            "receipt_date": source.receipt_date,
            "issuer_name": source.issuer_name,
            "issue_date": source.issue_date,
            "security_name": source.security_name,
            "series": source.series,
            "issuance_method": source.issuance_method,
            "stock_kind": source.stock_kind,
            "share_quantity": source.share_quantity,
            "issue_price_krw": source.issue_price_krw,
            "amount_krw": source.amount_krw,
            "amount_raw": source.amount_raw,
            "amount_unit": source.amount_unit,
            "evidence_text": source.evidence_text,
        }


def _source_id(event_id: str, source: FundraisingOccurrence) -> str:
    identity = "\x1f".join(
        (
            event_id,
            source.filing_id,
            source.table_id,
            str(source.row_index),
        )
    ).encode()
    digest = hashlib.sha256(identity).hexdigest()[:32]
    return f"fundraising-source:{digest}"


def _compact(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text)
