"""Add conservative generic fact storage.

Revision ID: 20260829_02_generic_facts
Revises: 20260829_01_source_layer
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260829_02_generic_facts"
down_revision: str | None = "20260829_01_source_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "generic_facts",
        sa.Column("fact_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "load_run_id",
            sa.String(length=64),
            sa.ForeignKey("load_runs.load_run_id", ondelete="RESTRICT"),
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
        sa.Column(
            "block_id",
            sa.String(length=320),
            sa.ForeignKey("source_blocks.block_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "table_id",
            sa.String(length=384),
            sa.ForeignKey("source_tables.table_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("column_index", sa.Integer(), nullable=False),
        sa.Column("fact_kind", sa.String(length=32), nullable=False),
        sa.Column("label_text", sa.Text()),
        sa.Column("header_text", sa.Text()),
        sa.Column("path_text", sa.Text(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=False),
        sa.Column("numeric_value", sa.Numeric()),
        sa.Column("unit_raw", sa.Text()),
        sa.Column("currency", sa.String(length=32)),
        sa.Column("concept_code", sa.Text()),
        sa.Column("context_ref", sa.Text()),
        sa.Column("source_locator", JSONB),
        sa.UniqueConstraint(
            "table_id",
            "row_index",
            "column_index",
            name="uq_generic_facts_table_cell",
        ),
    )
    op.create_index("ix_generic_facts_filing", "generic_facts", ["filing_id"])
    op.create_index("ix_generic_facts_document", "generic_facts", ["document_id"])
    op.create_index("ix_generic_facts_kind", "generic_facts", ["fact_kind"])
    op.create_index("ix_generic_facts_concept_code", "generic_facts", ["concept_code"])
    op.execute(
        "CREATE UNLOGGED TABLE source_staging.generic_facts "
        "(LIKE public.generic_facts INCLUDING DEFAULTS)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS source_staging.generic_facts")
    op.drop_table("generic_facts")
