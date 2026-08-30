"""Persistence models for canonical fundraising events and source occurrences."""

from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from disclosure_agent.storage.db_models import Base


class FundraisingEventRow(Base):
    """One canonical real-world fundraising event collapsed across periodic reports."""

    __tablename__ = "fundraising_events"
    __table_args__ = (
        Index("ix_fundraising_events_corp_date", "corp_code", "issue_date"),
        Index("ix_fundraising_events_type_date", "instrument_type", "issue_date"),
        Index("ix_fundraising_events_amount", "amount_krw"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    corp_code: Mapped[str] = mapped_column(
        ForeignKey("source_companies.corp_code", ondelete="RESTRICT"), nullable=False
    )
    instrument_type: Mapped[str] = mapped_column(String(32), nullable=False)
    issuer_name: Mapped[str] = mapped_column(Text, nullable=False)
    issue_date: Mapped[date | None] = mapped_column(Date)
    security_name: Mapped[str | None] = mapped_column(Text)
    series: Mapped[str | None] = mapped_column(Text)
    issuance_method: Mapped[str | None] = mapped_column(Text)
    stock_kind: Mapped[str | None] = mapped_column(Text)
    share_quantity: Mapped[int | None] = mapped_column(BigInteger)
    issue_price_krw: Mapped[int | None] = mapped_column(BigInteger)
    amount_krw: Mapped[int | None] = mapped_column(BigInteger)
    amount_raw: Mapped[str | None] = mapped_column(Text)
    amount_unit: Mapped[str | None] = mapped_column(String(32))
    representative_filing_id: Mapped[str] = mapped_column(
        ForeignKey("source_filings.filing_id", ondelete="CASCADE"), nullable=False
    )
    representative_table_id: Mapped[str] = mapped_column(
        ForeignKey("source_tables.table_id", ondelete="CASCADE"), nullable=False
    )
    representative_row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False)


class FundraisingEventSourceRow(Base):
    """One observed source occurrence supporting a canonical fundraising event."""

    __tablename__ = "fundraising_event_sources"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "filing_id",
            "table_id",
            "row_index",
            name="uq_fundraising_event_sources_occurrence",
        ),
        Index("ix_fundraising_event_sources_event", "event_id"),
        Index("ix_fundraising_event_sources_filing", "filing_id"),
    )

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("fundraising_events.event_id", ondelete="CASCADE"), nullable=False
    )
    filing_id: Mapped[str] = mapped_column(
        ForeignKey("source_filings.filing_id", ondelete="CASCADE"), nullable=False
    )
    table_id: Mapped[str] = mapped_column(
        ForeignKey("source_tables.table_id", ondelete="CASCADE"), nullable=False
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    issuer_name: Mapped[str] = mapped_column(Text, nullable=False)
    issue_date: Mapped[date | None] = mapped_column(Date)
    security_name: Mapped[str | None] = mapped_column(Text)
    series: Mapped[str | None] = mapped_column(Text)
    issuance_method: Mapped[str | None] = mapped_column(Text)
    stock_kind: Mapped[str | None] = mapped_column(Text)
    share_quantity: Mapped[int | None] = mapped_column(BigInteger)
    issue_price_krw: Mapped[int | None] = mapped_column(BigInteger)
    amount_krw: Mapped[int | None] = mapped_column(BigInteger)
    amount_raw: Mapped[str | None] = mapped_column(Text)
    amount_unit: Mapped[str | None] = mapped_column(String(32))
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
