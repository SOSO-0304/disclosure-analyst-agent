"""SQLAlchemy model for semantic retrieval chunks."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from disclosure_agent.storage.db_models import JSON_DOCUMENT, Base


class RetrievalChunkRow(Base):
    """One deterministic semantic chunk with source-block lineage."""

    __tablename__ = "retrieval_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_retrieval_chunks_document_index",
        ),
        Index("ix_retrieval_chunks_corp", "corp_code"),
        Index("ix_retrieval_chunks_filing", "filing_id"),
        Index("ix_retrieval_chunks_document", "document_id"),
        Index("ix_retrieval_chunks_section", "section_id"),
    )

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    corp_code: Mapped[str] = mapped_column(
        ForeignKey("source_companies.corp_code", ondelete="RESTRICT"),
        nullable=False,
    )
    filing_id: Mapped[str] = mapped_column(
        ForeignKey("source_filings.filing_id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.document_id", ondelete="CASCADE"),
        nullable=False,
    )
    section_id: Mapped[str | None] = mapped_column(String(320))
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_block_order: Mapped[int] = mapped_column(Integer, nullable=False)
    end_block_order: Mapped[int] = mapped_column(Integer, nullable=False)
    block_ids: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    table_ids: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
