"""Add versioned CLOVA Studio embeddings and a cosine HNSW index.

Revision ID: 20260830_02_embeddings
Revises: 20260830_01_retrieval_chunks
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260830_02_embeddings"
down_revision = "20260830_01_retrieval_chunks"
branch_labels = None
depends_on = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "embedding_runs",
        sa.Column("embedding_run_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "chunk_run_id",
            sa.String(length=64),
            sa.ForeignKey("retrieval_chunk_runs.chunk_run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("distance_metric", sa.String(length=32), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("input_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("counts", JSON_DOCUMENT, nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("dimensions = 1024", name="ck_embedding_run_dimensions"),
        sa.CheckConstraint(
            "distance_metric = 'cosine'",
            name="ck_embedding_run_distance_metric",
        ),
    )
    op.create_index(
        "ix_embedding_runs_chunk_status",
        "embedding_runs",
        ["chunk_run_id", "status"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_embedding_runs_one_active_per_chunk_run "
        "ON embedding_runs (chunk_run_id) WHERE is_active"
    )

    op.create_table(
        "retrieval_embeddings",
        sa.Column(
            "embedding_run_id",
            sa.String(length=64),
            sa.ForeignKey("embedding_runs.embedding_run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("chunk_run_id", sa.String(length=64), nullable=False),
        sa.Column("chunk_id", sa.String(length=512), primary_key=True),
        sa.Column("chunk_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("provider_request_id", sa.String(length=64)),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id", "chunk_run_id"],
            ["retrieval_chunks.chunk_id", "retrieval_chunks.chunk_run_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_retrieval_embeddings_chunk_run",
        "retrieval_embeddings",
        ["chunk_run_id"],
    )
    op.create_index(
        "ix_retrieval_embeddings_content_sha",
        "retrieval_embeddings",
        ["chunk_content_sha256"],
    )
    op.execute(
        "CREATE INDEX ix_retrieval_embeddings_hnsw_cosine "
        "ON retrieval_embeddings USING hnsw "
        "(embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.drop_table("retrieval_embeddings")
    op.drop_index(
        "uq_embedding_runs_one_active_per_chunk_run",
        table_name="embedding_runs",
    )
    op.drop_table("embedding_runs")
