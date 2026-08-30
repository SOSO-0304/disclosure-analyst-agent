"""Typed event models built on the generic source and fact layers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from disclosure_agent.storage.db_models import JSON_DOCUMENT, Base
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
    investment_amount_krw: Mapped[int | None] = mapped_column(BigInteger)
    equity_krw: Mapped[int | None] = mapped_column(BigInteger)
    equity_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    purpose: Mapped[str | None] = mapped_column(Text)
    investment_start_date: Mapped[date | None] = mapped_column(Date)
    investment_end_date: Mapped[date | None] = mapped_column(Date)
    decision_date: Mapped[date | None] = mapped_column(Date)
    defer_reason: Mapped[str | None] = mapped_column(Text)
    defer_until: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)


class FacilityInvestmentCorrectionLinkRow(Base):
    """Correction filing to predecessor/root resolution for facility investments."""

    __tablename__ = "facility_investment_correction_links"
    __table_args__ = (
        Index("ix_facility_investment_correction_root", "root_filing_id"),
        Index("ix_facility_investment_correction_status", "status"),
    )

    correction_filing_id: Mapped[str] = mapped_column(
        ForeignKey("source_filings.filing_id", ondelete="CASCADE"), primary_key=True
    )
    predecessor_filing_id: Mapped[str | None] = mapped_column(String(128))
    root_filing_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_filing_ids: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    match_score: Mapped[int | None] = mapped_column(Integer)


class FacilityInvestmentLifecycleRow(Base):
    """Latest effective filing for one facility-investment correction chain."""

    __tablename__ = "facility_investment_lifecycle"
    __table_args__ = (
        Index("ix_facility_investment_lifecycle_corp", "corp_code"),
        Index("ix_facility_investment_lifecycle_latest", "latest_receipt_date"),
        Index("ix_facility_investment_lifecycle_status", "status"),
    )

    root_filing_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    corp_code: Mapped[str] = mapped_column(
        ForeignKey("source_companies.corp_code", ondelete="RESTRICT"), nullable=False
    )
    latest_filing_id: Mapped[str] = mapped_column(
        ForeignKey("source_filings.filing_id", ondelete="CASCADE"), nullable=False
    )
    latest_receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    correction_count: Mapped[int] = mapped_column(Integer, nullable=False)
    lineage_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
