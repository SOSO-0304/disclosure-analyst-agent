"""Add facility-investment correction lineage and latest-state storage.

Revision ID: 20260830_04_facility_investment_lineage
Revises: 20260830_03_facility_investment
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260830_04_facility_investment_lineage"
down_revision: str | None = "20260830_03_facility_investment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "facility_investment_correction_links",
        sa.Column(
            "correction_filing_id",
            sa.String(length=128),
            sa.ForeignKey("source_filings.filing_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("predecessor_filing_id", sa.String(length=128)),
        sa.Column("root_filing_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("candidate_filing_ids", JSONB, nullable=False),
        sa.Column("match_score", sa.Integer()),
    )
    op.create_index(
        "ix_facility_investment_correction_root",
        "facility_investment_correction_links",
        ["root_filing_id"],
    )
    op.create_index(
        "ix_facility_investment_correction_status",
        "facility_investment_correction_links",
        ["status"],
    )

    op.create_table(
        "facility_investment_lifecycle",
        sa.Column("root_filing_id", sa.String(length=128), primary_key=True),
        sa.Column(
            "corp_code",
            sa.String(length=16),
            sa.ForeignKey("source_companies.corp_code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "latest_filing_id",
            sa.String(length=128),
            sa.ForeignKey("source_filings.filing_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("latest_receipt_date", sa.Date(), nullable=False),
        sa.Column("correction_count", sa.Integer(), nullable=False),
        sa.Column("lineage_complete", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
    )
    op.create_index(
        "ix_facility_investment_lifecycle_corp",
        "facility_investment_lifecycle",
        ["corp_code"],
    )
    op.create_index(
        "ix_facility_investment_lifecycle_latest",
        "facility_investment_lifecycle",
        ["latest_receipt_date"],
    )
    op.create_index(
        "ix_facility_investment_lifecycle_status",
        "facility_investment_lifecycle",
        ["status"],
    )


def downgrade() -> None:
    op.drop_table("facility_investment_lifecycle")
    op.drop_table("facility_investment_correction_links")
