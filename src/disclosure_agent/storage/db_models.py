"""SQLAlchemy models for canonical source data and typed disclosure domains."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


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


class LoadRunRow(Base):
    """One source-layer ingestion run and its accepted effective-view manifest."""

    __tablename__ = "load_runs"
    __table_args__ = (
        Index("ix_load_runs_status", "status"),
        UniqueConstraint(
            "base_sha256",
            "overlay_sha256",
            name="uq_load_runs_effective_inputs",
        ),
    )

    load_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    base_sha256: Mapped[str | None] = mapped_column(String(64))
    overlay_sha256: Mapped[str | None] = mapped_column(String(64))
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    manifest: Mapped[dict[str, object] | None] = mapped_column(JSON_DOCUMENT)
    counts: Mapped[dict[str, int]] = mapped_column(JSON_DOCUMENT, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceCompanyRow(Base):
    """Full 70-company source master isolated from the typed contract company slice."""

    __tablename__ = "source_companies"

    corp_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    stock_code: Mapped[str | None] = mapped_column(String(16))
    corp_name: Mapped[str] = mapped_column(Text, nullable=False)
    listed_name: Mapped[str] = mapped_column(Text, nullable=False)
    industry: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(Text)


class SourceFilingRow(Base):
    """Effective canonical filing metadata, separate from typed domain projections."""

    __tablename__ = "source_filings"
    __table_args__ = (
        UniqueConstraint("receipt_number", name="uq_source_filings_receipt_number"),
        Index("ix_source_filings_corp_date", "corp_code", "receipt_date"),
        Index("ix_source_filings_group_subtype", "document_group", "document_subtype"),
    )

    filing_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    load_run_id: Mapped[str] = mapped_column(
        ForeignKey("load_runs.load_run_id", ondelete="RESTRICT"), nullable=False
    )
    corp_code: Mapped[str] = mapped_column(
        ForeignKey("source_companies.corp_code", ondelete="RESTRICT"), nullable=False
    )
    receipt_number: Mapped[str] = mapped_column(String(32), nullable=False)
    document_group: Mapped[str] = mapped_column(String(32), nullable=False)
    document_subtype: Mapped[str | None] = mapped_column(Text)
    report_name: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    filer_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_correction: Mapped[bool] = mapped_column(Boolean, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)


class SourceDocumentRow(Base):
    """One semantic document in an effective canonical filing."""

    __tablename__ = "source_documents"
    __table_args__ = (Index("ix_source_documents_filing", "filing_id"),)

    document_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    filing_id: Mapped[str] = mapped_column(
        ForeignKey("source_filings.filing_id", ondelete="CASCADE"), nullable=False
    )
    load_run_id: Mapped[str] = mapped_column(
        ForeignKey("load_runs.load_run_id", ondelete="RESTRICT"), nullable=False
    )
    document_role: Mapped[str] = mapped_column(String(64), nullable=False)
    title_raw: Mapped[str | None] = mapped_column(Text)
    title_normalized: Mapped[str | None] = mapped_column(Text)
    primary_source_file_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_file_ids: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    parse_status: Mapped[str] = mapped_column(String(32), nullable=False)
    parser_name: Mapped[str | None] = mapped_column(String(64))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    recovered: Mapped[bool] = mapped_column(Boolean, nullable=False)
    emitted_section_count: Mapped[int] = mapped_column(Integer, nullable=False)
    emitted_block_count: Mapped[int] = mapped_column(Integer, nullable=False)
    emitted_table_count: Mapped[int] = mapped_column(Integer, nullable=False)


class SourceSectionRow(Base):
    """Canonical section hierarchy used for structured and semantic retrieval."""

    __tablename__ = "source_sections"
    __table_args__ = (
        Index("ix_source_sections_document_order", "document_id", "section_order"),
        Index("ix_source_sections_parent", "parent_section_id"),
    )

    section_id: Mapped[str] = mapped_column(String(320), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.document_id", ondelete="CASCADE"), nullable=False
    )
    filing_id: Mapped[str] = mapped_column(String(128), nullable=False)
    load_run_id: Mapped[str] = mapped_column(
        ForeignKey("load_runs.load_run_id", ondelete="RESTRICT"), nullable=False
    )
    parent_section_id: Mapped[str | None] = mapped_column(String(320))
    section_order: Mapped[int] = mapped_column(Integer, nullable=False)
    section_level: Mapped[int] = mapped_column(Integer, nullable=False)
    title_raw: Mapped[str | None] = mapped_column(Text)
    title_normalized: Mapped[str | None] = mapped_column(Text)
    source_locator: Mapped[dict[str, object] | None] = mapped_column(JSON_DOCUMENT)
    attributes_raw: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT, nullable=False)


class SourceBlockRow(Base):
    """Ordered text/table placeholder block without exploding table cells into rows."""

    __tablename__ = "source_blocks"
    __table_args__ = (
        Index("ix_source_blocks_document_order", "document_id", "block_order"),
        Index("ix_source_blocks_section", "section_id"),
    )

    block_id: Mapped[str] = mapped_column(String(320), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.document_id", ondelete="CASCADE"), nullable=False
    )
    filing_id: Mapped[str] = mapped_column(String(128), nullable=False)
    load_run_id: Mapped[str] = mapped_column(
        ForeignKey("load_runs.load_run_id", ondelete="RESTRICT"), nullable=False
    )
    section_id: Mapped[str | None] = mapped_column(String(320))
    block_order: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str] = mapped_column(String(32), nullable=False)
    text_raw: Mapped[str | None] = mapped_column(Text)
    text_normalized: Mapped[str | None] = mapped_column(Text)
    heading_level: Mapped[int | None] = mapped_column(Integer)
    table_id: Mapped[str | None] = mapped_column(String(384))
    source_locator: Mapped[dict[str, object] | None] = mapped_column(JSON_DOCUMENT)
    attributes_raw: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT, nullable=False)


class SourceTableRow(Base):
    """Table metadata plus a JSONB grid; individual cells remain canonical JSON facts."""

    __tablename__ = "source_tables"
    __table_args__ = (
        UniqueConstraint("block_id", name="uq_source_tables_block_id"),
        Index("ix_source_tables_document", "document_id"),
        Index("ix_source_tables_parent", "parent_table_id"),
    )

    table_id: Mapped[str] = mapped_column(String(384), primary_key=True)
    block_id: Mapped[str] = mapped_column(
        ForeignKey("source_blocks.block_id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(String(256), nullable=False)
    filing_id: Mapped[str] = mapped_column(String(128), nullable=False)
    load_run_id: Mapped[str] = mapped_column(
        ForeignKey("load_runs.load_run_id", ondelete="RESTRICT"), nullable=False
    )
    caption_raw: Mapped[str | None] = mapped_column(Text)
    caption_normalized: Mapped[str | None] = mapped_column(Text)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    header_row_indices: Mapped[list[int]] = mapped_column(JSON_DOCUMENT, nullable=False)
    parent_table_id: Mapped[str | None] = mapped_column(String(384))
    parent_cell_locator: Mapped[dict[str, object] | None] = mapped_column(JSON_DOCUMENT)
    source_locator: Mapped[dict[str, object] | None] = mapped_column(JSON_DOCUMENT)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    grid: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT, nullable=False)
    attributes_raw: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT, nullable=False)


class RetrievalChunkRunRow(Base):
    """One validated materialization of an approved retrieval plan."""

    __tablename__ = "retrieval_chunk_runs"
    __table_args__ = (
        UniqueConstraint(
            "source_load_run_id",
            "plan_sha256",
            name="uq_retrieval_chunk_run_source_plan",
        ),
        Index(
            "ix_retrieval_chunk_runs_source_status",
            "source_load_run_id",
            "status",
        ),
    )

    chunk_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_load_run_id: Mapped[str] = mapped_column(
        ForeignKey("load_runs.load_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    plan_version: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    policy: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT, nullable=False)
    counts: Mapped[dict[str, int]] = mapped_column(JSON_DOCUMENT, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RetrievalChunkRow(Base):
    """Versioned retrieval text plus canonical block/table provenance."""

    __tablename__ = "retrieval_chunks"
    __table_args__ = (
        Index("ix_retrieval_chunks_run_type", "chunk_run_id", "chunk_type"),
        Index("ix_retrieval_chunks_filing_type", "filing_id", "chunk_type"),
        Index("ix_retrieval_chunks_document", "document_id"),
        Index("ix_retrieval_chunks_section", "section_id"),
        Index("ix_retrieval_chunks_source_table", "source_table_id"),
    )

    chunk_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    chunk_run_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_chunk_runs.chunk_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    filing_id: Mapped[str] = mapped_column(
        ForeignKey("source_filings.filing_id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.document_id", ondelete="CASCADE"),
        nullable=False,
    )
    section_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_sections.section_id", ondelete="SET NULL")
    )
    document_group: Mapped[str] = mapped_column(String(32), nullable=False)
    chunk_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    source_block_ids: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    source_table_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_tables.table_id", ondelete="CASCADE")
    )
    start_block_order: Mapped[int | None] = mapped_column(Integer)
    end_block_order: Mapped[int | None] = mapped_column(Integer)
    table_row_start: Mapped[int | None] = mapped_column(Integer)
    table_row_end: Mapped[int | None] = mapped_column(Integer)
    metadata: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT, nullable=False)
