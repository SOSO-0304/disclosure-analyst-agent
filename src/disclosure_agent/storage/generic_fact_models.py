"""SQLAlchemy model for conservative generic canonical facts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from disclosure_agent.storage.db_models import JSON_DOCUMENT, Base


class GenericFactRow(Base):
    """One atomic canonical table value plus retrieval and evidence context."""

    __tablename__ = "generic_facts"
    __table_args__ = (
        UniqueConstraint(
            "table_id",
            "row_index",
            "column_index",
            name="uq_generic_facts_table_cell",
        ),
        Index("ix_generic_facts_filing", "filing_id"),
        Index("ix_generic_facts_document", "document_id"),
        Index("ix_generic_facts_kind", "fact_kind"),
        Index("ix_generic_facts_concept_code", "concept_code"),
    )

    fact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    load_run_id: Mapped[str] = mapped_column(
        ForeignKey("load_runs.load_run_id", ondelete="RESTRICT"), nullable=False
    )
    filing_id: Mapped[str] = mapped_column(
        ForeignKey("source_filings.filing_id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.document_id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[str | None] = mapped_column(String(320))
    block_id: Mapped[str] = mapped_column(
        ForeignKey("source_blocks.block_id", ondelete="CASCADE"), nullable=False
    )
    table_id: Mapped[str] = mapped_column(
        ForeignKey("source_tables.table_id", ondelete="CASCADE"), nullable=False
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    column_index: Mapped[int] = mapped_column(Integer, nullable=False)
    fact_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    label_text: Mapped[str | None] = mapped_column(Text)
    header_text: Mapped[str | None] = mapped_column(Text)
    path_text: Mapped[str] = mapped_column(Text, nullable=False)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_value: Mapped[str] = mapped_column(Text, nullable=False)
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric())
    unit_raw: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(String(32))
    concept_code: Mapped[str | None] = mapped_column(Text)
    context_ref: Mapped[str | None] = mapped_column(Text)
    source_locator: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT)
