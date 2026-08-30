"""Typed event models built on the generic source and fact layers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from disclosure_agent.storage.db_models import Base
from disclosure_agent.storage.generic_fact_models import GenericFactRow


class SourceEventRow(Base):
    """Generic event envelope for typed events projected from the full source corpus."""

    __tablename__ = "source_events"
    __table_args__ = (
        UniqueConstraint("filing_id", "event_type", name="uq_source_events_filing_type"),
        Index("ix_source_events_corp_type", "corp_code", "event_type"),
        Index("ix_source_events_type_date", "event_type", "event_date"),
    )

    event_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    filing_id: Mapped[str] = mapped_column(
        ForeignKey("source_filings.filing_id", ondelete="CASCADE"), nullable=False
    )
    corp_code: Mapped[str] = mapped_column(
        ForeignKey("source_companies.corp_code", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_date: Mapped[date | None] = mapped_column(Date)


class SourceEventEvidenceRow(Base):
    """Attribute-level evidence from a typed source event to one generic fact."""

    __tablename__ = "source_event_evidence"
    __table_args__ = (
        UniqueConstraint("event_id", "attribute", name="uq_source_event_evidence_attribute"),
        Index("ix_source_event_evidence_fact", "fact_id"),
    )

    event_id: Mapped[str] = mapped_column(
        ForeignKey("source_events.event_id", ondelete="CASCADE"), primary_key=True
    )
    attribute: Mapped[str] = mapped_column(String(64), primary_key=True)
    fact_id: Mapped[str] = mapped_column(
        ForeignKey(GenericFactRow.fact_id, ondelete="CASCADE"), nullable=False
    )


class FacilityInvestmentEventRow(Base):
    """Typed values extracted from Exchange 신규시설투자등 disclosures."""

    __tablename__ = "facility_investment_events"
    __table_args__ = (
        Index("ix_facility_investment_amount", "investment_amount_krw"),
        Index("ix_facility_investment_decision_date", "decision_date"),
    )

    filing_id: Mapped[str] = mapped_column(
        ForeignKey("source_filings.filing_id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("source_events.event_id", ondelete="CASCADE"), nullable=False, unique=True
    )
    investment_type: Mapped[str | None] = mapped_column(Text)
    investment_subject: Mapped[str | None] = mapped_column(Text)
    investment_amount_krw: Mapped[int | None] = mapped_column(Numeric(30, 0))
    equity_krw: Mapped[int | None] = mapped_column(Numeric(30, 0))
    equity_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    purpose: Mapped[str | None] = mapped_column(Text)
    investment_start_date: Mapped[date | None] = mapped_column(Date)
    investment_end_date: Mapped[date | None] = mapped_column(Date)
    decision_date: Mapped[date | None] = mapped_column(Date)
    defer_reason: Mapped[str | None] = mapped_column(Text)
    defer_until: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
