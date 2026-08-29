"""SQLAlchemy models for canonical disclosures and Supply Contract domain data."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    JSON,
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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base shared by persistence models."""


class CompanyRow(Base):
    """Company master keyed by DART corporation code."""

    __tablename__ = "companies"

    corp_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    stock_code: Mapped[str | None] = mapped_column(String(16))
    corp_name: Mapped[str] = mapped_column(Text)
    listed_name: Mapped[str] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(Text)


class DisclosureRow(Base):
    """One canonical filing package."""

    __tablename__ = "disclosures"
    __table_args__ = (
        UniqueConstraint("receipt_number", name="uq_disclosures_receipt_number"),
        Index("ix_disclosures_corp_receipt_date", "corp_code", "receipt_date"),
        Index("ix_disclosures_group_subtype", "document_group", "document_subtype"),
    )

    filing_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    receipt_number: Mapped[str] = mapped_column(String(32), nullable=False)
    corp_code: Mapped[str] = mapped_column(
        ForeignKey("companies.corp_code", ondelete="RESTRICT"), nullable=False
    )
    document_group: Mapped[str] = mapped_column(String(32), nullable=False)
    document_subtype: Mapped[str] = mapped_column(Text, nullable=False)
    report_name: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    filer_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_correction: Mapped[bool] = mapped_column(Boolean, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)


class DisclosureEventRow(Base):
    """Generic event envelope used by query routing before typed-table access."""

    __tablename__ = "disclosure_events"
    __table_args__ = (
        UniqueConstraint("filing_id", "event_type", name="uq_disclosure_event_filing_type"),
        Index("ix_disclosure_events_type_date", "event_type", "event_date"),
        Index("ix_disclosure_events_corp_type", "corp_code", "event_type"),
    )

    event_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    filing_id: Mapped[str] = mapped_column(
        ForeignKey("disclosures.filing_id", ondelete="CASCADE"), nullable=False
    )
    corp_code: Mapped[str] = mapped_column(
        ForeignKey("companies.corp_code", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_date: Mapped[date | None] = mapped_column(Date)


class SupplyContractEventRow(Base):
    """Typed values extracted from a Supply Contract formation/correction filing."""

    __tablename__ = "supply_contract_events"
    __table_args__ = (
        Index("ix_supply_contract_events_root", "root_filing_id"),
        Index("ix_supply_contract_events_contract_date", "contract_date"),
        Index("ix_supply_contract_events_counterparty", "counterparty"),
    )

    filing_id: Mapped[str] = mapped_column(
        ForeignKey("disclosures.filing_id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("disclosure_events.event_id", ondelete="CASCADE"), nullable=False
    )
    root_filing_id: Mapped[str] = mapped_column(String(128), nullable=False)
    predecessor_filing_id: Mapped[str | None] = mapped_column(String(128))
    lineage_status: Mapped[str | None] = mapped_column(String(64))
    is_latest_for_root: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    contract_type: Mapped[str | None] = mapped_column(Text)
    contract_name: Mapped[str | None] = mapped_column(Text)
    contract_amount: Mapped[int | None] = mapped_column(BigInteger)
    recent_revenue: Mapped[int | None] = mapped_column(BigInteger)
    revenue_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    counterparty: Mapped[str | None] = mapped_column(Text)
    relationship: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    contract_start_date: Mapped[date | None] = mapped_column(Date)
    contract_end_date: Mapped[date | None] = mapped_column(Date)
    contract_date: Mapped[date | None] = mapped_column(Date)
    major_conditions: Mapped[str | None] = mapped_column(Text)


class SupplyContractCorrectionLinkRow(Base):
    """Correction filing -> predecessor resolution with conservative status."""

    __tablename__ = "supply_contract_correction_links"
    __table_args__ = (Index("ix_supply_contract_correction_links_status", "status"),)

    correction_filing_id: Mapped[str] = mapped_column(
        ForeignKey("disclosures.filing_id", ondelete="CASCADE"), primary_key=True
    )
    predecessor_filing_id: Mapped[str | None] = mapped_column(String(128))
    root_filing_id: Mapped[str] = mapped_column(String(128), nullable=False)
    related_filing_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_filing_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    fingerprint_score: Mapped[int | None] = mapped_column(Integer)
    fingerprint_compared: Mapped[int | None] = mapped_column(Integer)
    correction_table_score: Mapped[int | None] = mapped_column(Integer)
    correction_table_compared: Mapped[int | None] = mapped_column(Integer)


class SupplyContractTerminationEventRow(Base):
    """Typed values extracted from a Supply Contract termination filing."""

    __tablename__ = "supply_contract_termination_events"
    __table_args__ = (
        Index("ix_supply_contract_termination_date", "termination_date"),
        Index("ix_supply_contract_termination_counterparty", "counterparty"),
    )

    filing_id: Mapped[str] = mapped_column(
        ForeignKey("disclosures.filing_id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("disclosure_events.event_id", ondelete="CASCADE"), nullable=False
    )
    termination_type: Mapped[str | None] = mapped_column(Text)
    contract_name: Mapped[str | None] = mapped_column(Text)
    termination_amount: Mapped[int | None] = mapped_column(BigInteger)
    recent_revenue: Mapped[int | None] = mapped_column(BigInteger)
    revenue_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    counterparty: Mapped[str | None] = mapped_column(Text)
    relationship: Mapped[str | None] = mapped_column(Text)
    contract_start_date: Mapped[date | None] = mapped_column(Date)
    contract_end_date: Mapped[date | None] = mapped_column(Date)
    termination_reason: Mapped[str | None] = mapped_column(Text)
    termination_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    related_disclosures: Mapped[str | None] = mapped_column(Text)


class SupplyContractTerminationLinkRow(Base):
    """Termination filing -> formation chain resolution."""

    __tablename__ = "supply_contract_termination_links"
    __table_args__ = (
        Index("ix_supply_contract_termination_links_root", "root_filing_id"),
        Index("ix_supply_contract_termination_links_status", "status"),
    )

    termination_filing_id: Mapped[str] = mapped_column(
        ForeignKey("disclosures.filing_id", ondelete="CASCADE"), primary_key=True
    )
    matched_formation_filing_id: Mapped[str | None] = mapped_column(String(128))
    root_filing_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    related_formation_dates: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    candidate_filing_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    fingerprint_score: Mapped[int | None] = mapped_column(Integer)
    fingerprint_compared: Mapped[int | None] = mapped_column(Integer)


class SupplyContractSuccessionEventRow(Base):
    """Typed values from the special residual-contract succession filing."""

    __tablename__ = "supply_contract_succession_events"

    filing_id: Mapped[str] = mapped_column(
        ForeignKey("disclosures.filing_id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("disclosure_events.event_id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(Text)
    contract_type: Mapped[str | None] = mapped_column(Text)
    succession_amount_krw: Mapped[int | None] = mapped_column(BigInteger)
    succession_amount_usd: Mapped[int | None] = mapped_column(BigInteger)
    disclosed_exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    fulfilled_amount_usd: Mapped[int | None] = mapped_column(BigInteger)
    fulfillment_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    counterparty: Mapped[str | None] = mapped_column(Text)
    contract_start_date: Mapped[date | None] = mapped_column(Date)
    contract_end_date: Mapped[date | None] = mapped_column(Date)
    decision_date: Mapped[date | None] = mapped_column(Date)
    correction_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    related_disclosures: Mapped[str | None] = mapped_column(Text)
    source_contract_reference_dates: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class SupplyContractLifecycleRow(Base):
    """One effective in-corpus contract root and its latest state."""

    __tablename__ = "supply_contract_lifecycle"
    __table_args__ = (
        Index("ix_supply_contract_lifecycle_corp_status", "corp_code", "status"),
        Index("ix_supply_contract_lifecycle_latest_receipt_date", "latest_receipt_date"),
    )

    root_filing_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    corp_code: Mapped[str] = mapped_column(
        ForeignKey("companies.corp_code", ondelete="RESTRICT"), nullable=False
    )
    latest_formation_filing_id: Mapped[str] = mapped_column(String(128), nullable=False)
    latest_receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    correction_count: Mapped[int] = mapped_column(Integer, nullable=False)
    correction_lineage_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    termination_filing_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    latest_termination_date: Mapped[date | None] = mapped_column(Date)


class SupplyContractSuccessionLifecycleRow(Base):
    """Lifecycle state for a contract inherited from an external/in-corpus predecessor."""

    __tablename__ = "supply_contract_succession_lifecycle"

    succession_filing_id: Mapped[str] = mapped_column(
        ForeignKey("disclosures.filing_id", ondelete="CASCADE"), primary_key=True
    )
    corp_code: Mapped[str] = mapped_column(
        ForeignKey("companies.corp_code", ondelete="RESTRICT"), nullable=False
    )
    predecessor_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_contract_reference_dates: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    matched_source_root_filing_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    contract_end_date: Mapped[date | None] = mapped_column(Date)
    decision_date: Mapped[date | None] = mapped_column(Date)


class EventEvidenceRow(Base):
    """Field-level provenance from typed attributes back to canonical table cells."""

    __tablename__ = "event_evidence"
    __table_args__ = (
        UniqueConstraint("event_id", "attribute", name="uq_event_evidence_attribute"),
        Index("ix_event_evidence_filing", "filing_id"),
    )

    evidence_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("disclosure_events.event_id", ondelete="CASCADE"), nullable=False
    )
    filing_id: Mapped[str] = mapped_column(
        ForeignKey("disclosures.filing_id", ondelete="CASCADE"), nullable=False
    )
    attribute: Mapped[str] = mapped_column(String(64), nullable=False)
    document_id: Mapped[str] = mapped_column(String(192), nullable=False)
    table_id: Mapped[str] = mapped_column(String(256), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    value_column_index: Mapped[int] = mapped_column(Integer, nullable=False)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_value: Mapped[str] = mapped_column(Text, nullable=False)
    value_locator: Mapped[dict[str, object] | None] = mapped_column(JSON)
    label_locators: Mapped[list[dict[str, object] | None]] = mapped_column(JSON, nullable=False)
