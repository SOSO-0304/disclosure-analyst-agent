"""Add versioned retrieval chunk storage.

Revision ID: 20260830_01_retrieval_chunks
Revises: 20260829_01_source_layer
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260830_01_retrieval_chunks"
down_revision = "20260829_01_source_layer"
branch_labels = None
depends_on = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "retrieval_chunk_runs",
        sa.Column("chunk_run_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "source_load_run_id",
            sa.String(length=64),
            sa.ForeignKey("load_runs.load_run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("plan_version", sa.String(length=32), nullable=False),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("policy", JSON_DOCUMENT, nullable=False),
        sa.Column("counts", JSON_DOCUMENT, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint(
            "source_load_run_id",
            "plan_sha256",
            name="uq_retrieval_chunk_run_source_plan",
        ),
    )
    op.create_index(
        "ix_retrieval_chunk_runs_source_status",
        "retrieval_chunk_runs",
        ["source_load_run_id", "status"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_retrieval_chunk_runs_one_active "
        "ON retrieval_chunk_runs ((is_active)) WHERE is_active"
    )

    op.create_table(
        "retrieval_chunks",
        sa.Column("chunk_id", sa.String(length=512), primary_key=True),
        sa.Column(
            "chunk_run_id",
            sa.String(length=64),
            sa.ForeignKey("retrieval_chunk_runs.chunk_run_id", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
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
        sa.Column(
            "section_id",
            sa.String(length=320),
            sa.ForeignKey("source_sections.section_id", ondelete="SET NULL"),
        ),
        sa.Column("document_group", sa.String(length=32), nullable=False),
        sa.Column("chunk_type", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("heading_path", JSON_DOCUMENT, nullable=False),
        sa.Column("source_block_ids", JSON_DOCUMENT, nullable=False),
        sa.Column(
            "source_table_id",
            sa.String(length=384),
            sa.ForeignKey("source_tables.table_id", ondelete="CASCADE"),
        ),
        sa.Column("start_block_order", sa.Integer()),
        sa.Column("end_block_order", sa.Integer()),
        sa.Column("table_row_start", sa.Integer()),
        sa.Column("table_row_end", sa.Integer()),
        sa.Column("metadata", JSON_DOCUMENT, nullable=False),
        sa.CheckConstraint("char_count > 0", name="ck_retrieval_chunk_nonempty"),
        sa.CheckConstraint(
            "chunk_type IN ('narrative', 'table')",
            name="ck_retrieval_chunk_type",
        ),
    )
    op.create_index(
        "ix_retrieval_chunks_run_type",
        "retrieval_chunks",
        ["chunk_run_id", "chunk_type"],
    )
    op.create_index(
        "ix_retrieval_chunks_filing_type",
        "retrieval_chunks",
        ["filing_id", "chunk_type"],
    )
    op.create_index("ix_retrieval_chunks_document", "retrieval_chunks", ["document_id"])
    op.create_index("ix_retrieval_chunks_section", "retrieval_chunks", ["section_id"])
    op.create_index(
        "ix_retrieval_chunks_source_table",
        "retrieval_chunks",
        ["source_table_id"],
    )


def downgrade() -> None:
    op.drop_table("retrieval_chunks")
    op.drop_index(
        "uq_retrieval_chunk_runs_one_active",
        table_name="retrieval_chunk_runs",
    )
    op.drop_table("retrieval_chunk_runs")
