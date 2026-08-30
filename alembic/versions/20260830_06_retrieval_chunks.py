"""Add deterministic semantic retrieval chunk storage.

Revision ID: 20260830_06_retrieval_chunks
Revises: 20260830_05_fundraising
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_06_retrieval_chunks"
down_revision: str | None = "20260830_05_fundraising"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retrieval_chunks",
        sa.Column("chunk_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "corp_code",
            sa.String(length=16),
            sa.ForeignKey("source_companies.corp_code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "filing_id",
            sa.String(length=128),
            sa.ForeignKey("source_filings.filing_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.String(length=256),
            sa.ForeignKey("source_documents.document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section_id", sa.String(length=320)),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("start_block_order", sa.Integer(), nullable=False),
        sa.Column("end_block_order", sa.Integer(), nullable=False),
        sa.Column("block_ids", sa.JSON(), nullable=False),
        sa.Column("table_ids", sa.JSON(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_retrieval_chunks_document_index",
        ),
    )
    op.create_index("ix_retrieval_chunks_corp", "retrieval_chunks", ["corp_code"])
    op.create_index("ix_retrieval_chunks_filing", "retrieval_chunks", ["filing_id"])
    op.create_index("ix_retrieval_chunks_document", "retrieval_chunks", ["document_id"])
    op.create_index("ix_retrieval_chunks_section", "retrieval_chunks", ["section_id"])


def downgrade() -> None:
    op.drop_table("retrieval_chunks")
