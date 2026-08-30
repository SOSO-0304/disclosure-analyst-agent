"""Add CLOVA Studio embedding storage to retrieval chunks.

Revision ID: 20260830_07_retrieval_embeddings
Revises: 20260830_06_retrieval_chunks
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "20260830_07_retrieval_embeddings"
down_revision: str | None = "20260830_06_retrieval_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("retrieval_chunks", sa.Column("embedding", Vector(1024)))
    op.add_column(
        "retrieval_chunks",
        sa.Column("embedding_model", sa.String(length=64)),
    )
    op.add_column(
        "retrieval_chunks",
        sa.Column("embedding_input_tokens", sa.Integer()),
    )
    op.add_column(
        "retrieval_chunks",
        sa.Column("embedded_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_retrieval_chunks_embedding_model",
        "retrieval_chunks",
        ["embedding_model"],
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_chunks_embedding_model", table_name="retrieval_chunks")
    op.drop_column("retrieval_chunks", "embedded_at")
    op.drop_column("retrieval_chunks", "embedding_input_tokens")
    op.drop_column("retrieval_chunks", "embedding_model")
    op.drop_column("retrieval_chunks", "embedding")
