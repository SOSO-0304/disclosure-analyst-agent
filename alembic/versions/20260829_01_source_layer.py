"""Add generic canonical source layer and isolated staging schema.

Revision ID: 20260829_01_source_layer
Revises: None
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260829_01_source_layer"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "load_runs",
        sa.Column("load_run_id", sa.String(length=64), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("base_sha256", sa.String(length=64)),
        sa.Column("overlay_sha256", sa.String(length=64)),
        sa.Column("manifest_sha256", sa.String(length=64)),
        sa.Column("manifest", JSONB),
        sa.Column("counts", JSONB, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "base_sha256",
            "overlay_sha256",
            name="uq_load_runs_effective_inputs",
        ),
    )
    op.create_index("ix_load_runs_status", "load_runs", ["status"])

    op.create_table(
        "source_filings",
        sa.Column("filing_id", sa.String(length=128), primary_key=True),
        sa.Column(
            "load_run_id",
            sa.String(length=64),
            sa.ForeignKey("load_runs.load_run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "corp_code",
            sa.String(length=16),
            sa.ForeignKey("companies.corp_code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("receipt_number", sa.String(length=32), nullable=False),
        sa.Column("document_group", sa.String(length=32), nullable=False),
        sa.Column("document_subtype", sa.Text(), nullable=False),
        sa.Column("report_name", sa.Text(), nullable=False),
        sa.Column("receipt_date", sa.Date(), nullable=False),
        sa.Column("filer_name", sa.Text(), nullable=False),
        sa.Column("is_correction", sa.Boolean(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("receipt_number", name="uq_source_filings_receipt_number"),
    )
    op.create_index(
        "ix_source_filings_corp_date",
        "source_filings",
        ["corp_code", "receipt_date"],
    )
    op.create_index(
        "ix_source_filings_group_subtype",
        "source_filings",
        ["document_group", "document_subtype"],
    )

    op.create_table(
        "source_documents",
        sa.Column("document_id", sa.String(length=256), primary_key=True),
        sa.Column(
            "filing_id",
            sa.String(length=128),
            sa.ForeignKey("source_filings.filing_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "load_run_id",
            sa.String(length=64),
            sa.ForeignKey("load_runs.load_run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("document_role", sa.String(length=64), nullable=False),
        sa.Column("title_raw", sa.Text()),
        sa.Column("title_normalized", sa.Text()),
        sa.Column("primary_source_file_id", sa.String(length=256), nullable=False),
        sa.Column("source_file_ids", JSONB, nullable=False),
        sa.Column("parse_status", sa.String(length=32), nullable=False),
        sa.Column("parser_name", sa.String(length=64)),
        sa.Column("parser_version", sa.String(length=32)),
        sa.Column("recovered", sa.Boolean(), nullable=False),
        sa.Column("emitted_section_count", sa.Integer(), nullable=False),
        sa.Column("emitted_block_count", sa.Integer(), nullable=False),
        sa.Column("emitted_table_count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_source_documents_filing", "source_documents", ["filing_id"])

    op.create_table(
        "source_sections",
        sa.Column("section_id", sa.String(length=320), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(length=256),
            sa.ForeignKey("source_documents.document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filing_id", sa.String(length=128), nullable=False),
        sa.Column(
            "load_run_id",
            sa.String(length=64),
            sa.ForeignKey("load_runs.load_run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("parent_section_id", sa.String(length=320)),
        sa.Column("section_order", sa.Integer(), nullable=False),
        sa.Column("section_level", sa.Integer(), nullable=False),
        sa.Column("title_raw", sa.Text()),
        sa.Column("title_normalized", sa.Text()),
        sa.Column("source_locator", JSONB),
        sa.Column("attributes_raw", JSONB, nullable=False),
    )
    op.create_index(
        "ix_source_sections_document_order",
        "source_sections",
        ["document_id", "section_order"],
    )
    op.create_index("ix_source_sections_parent", "source_sections", ["parent_section_id"])

    op.create_table(
        "source_blocks",
        sa.Column("block_id", sa.String(length=320), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(length=256),
            sa.ForeignKey("source_documents.document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filing_id", sa.String(length=128), nullable=False),
        sa.Column(
            "load_run_id",
            sa.String(length=64),
            sa.ForeignKey("load_runs.load_run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("section_id", sa.String(length=320)),
        sa.Column("block_order", sa.Integer(), nullable=False),
        sa.Column("block_type", sa.String(length=32), nullable=False),
        sa.Column("text_raw", sa.Text()),
        sa.Column("text_normalized", sa.Text()),
        sa.Column("heading_level", sa.Integer()),
        sa.Column("table_id", sa.String(length=384)),
        sa.Column("source_locator", JSONB),
        sa.Column("attributes_raw", JSONB, nullable=False),
    )
    op.create_index(
        "ix_source_blocks_document_order",
        "source_blocks",
        ["document_id", "block_order"],
    )
    op.create_index("ix_source_blocks_section", "source_blocks", ["section_id"])

    op.create_table(
        "source_tables",
        sa.Column("table_id", sa.String(length=384), primary_key=True),
        sa.Column(
            "block_id",
            sa.String(length=320),
            sa.ForeignKey("source_blocks.block_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_id", sa.String(length=256), nullable=False),
        sa.Column("filing_id", sa.String(length=128), nullable=False),
        sa.Column(
            "load_run_id",
            sa.String(length=64),
            sa.ForeignKey("load_runs.load_run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("caption_raw", sa.Text()),
        sa.Column("caption_normalized", sa.Text()),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("column_count", sa.Integer(), nullable=False),
        sa.Column("header_row_indices", JSONB, nullable=False),
        sa.Column("parent_table_id", sa.String(length=384)),
        sa.Column("parent_cell_locator", JSONB),
        sa.Column("source_locator", JSONB),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("grid", JSONB, nullable=False),
        sa.Column("attributes_raw", JSONB, nullable=False),
        sa.UniqueConstraint("block_id", name="uq_source_tables_block_id"),
    )
    op.create_index("ix_source_tables_document", "source_tables", ["document_id"])
    op.create_index("ix_source_tables_parent", "source_tables", ["parent_table_id"])

    op.execute("CREATE SCHEMA IF NOT EXISTS source_staging")
    for table_name in (
        "companies",
        "source_filings",
        "source_documents",
        "source_sections",
        "source_blocks",
        "source_tables",
    ):
        op.execute(
            f"CREATE UNLOGGED TABLE source_staging.{table_name} "
            f"(LIKE public.{table_name} INCLUDING DEFAULTS)"
        )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS source_staging CASCADE")
    op.drop_table("source_tables")
    op.drop_table("source_blocks")
    op.drop_table("source_sections")
    op.drop_table("source_documents")
    op.drop_table("source_filings")
    op.drop_table("load_runs")
