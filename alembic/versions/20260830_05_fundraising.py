"""Add canonical fundraising events and source-occurrence storage.

Revision ID: 20260830_05_fundraising
Revises: 20260830_04_facility_lineage
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_05_fundraising"
down_revision: str | None = "20260830_04_facility_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fundraising_events",
        sa.Column("event_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "corp_code",
            sa.String(length=16),
            sa.ForeignKey("source_companies.corp_code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("instrument_type", sa.String(length=32), nullable=False),
        sa.Column("issuer_name", sa.Text(), nullable=False),
        sa.Column("issue_date", sa.Date()),
        sa.Column("security_name", sa.Text()),
        sa.Column("series", sa.Text()),
        sa.Column("issuance_method", sa.Text()),
        sa.Column("stock_kind", sa.Text()),
        sa.Column("share_quantity", sa.BigInteger()),
        sa.Column("issue_price_krw", sa.BigInteger()),
        sa.Column("amount_krw", sa.BigInteger()),
        sa.Column("amount_raw", sa.Text()),
        sa.Column("amount_unit", sa.String(length=32)),
        sa.Column(
            "representative_filing_id",
            sa.String(length=128),
            sa.ForeignKey("source_filings.filing_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "representative_table_id",
            sa.String(length=384),
            sa.ForeignKey("source_tables.table_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("representative_row_index", sa.Integer(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_fundraising_events_corp_date",
        "fundraising_events",
        ["corp_code", "issue_date"],
    )
    op.create_index(
        "ix_fundraising_events_type_date",
        "fundraising_events",
        ["instrument_type", "issue_date"],
    )
    op.create_index(
        "ix_fundraising_events_amount",
        "fundraising_events",
        ["amount_krw"],
    )

    op.create_table(
        "fundraising_event_sources",
        sa.Column("source_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "event_id",
            sa.String(length=64),
            sa.ForeignKey("fundraising_events.event_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "filing_id",
            sa.String(length=128),
            sa.ForeignKey("source_filings.filing_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "table_id",
            sa.String(length=384),
            sa.ForeignKey("source_tables.table_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("receipt_date", sa.Date(), nullable=False),
        sa.Column("issuer_name", sa.Text(), nullable=False),
        sa.Column("issue_date", sa.Date()),
        sa.Column("security_name", sa.Text()),
        sa.Column("series", sa.Text()),
        sa.Column("issuance_method", sa.Text()),
        sa.Column("stock_kind", sa.Text()),
        sa.Column("share_quantity", sa.BigInteger()),
        sa.Column("issue_price_krw", sa.BigInteger()),
        sa.Column("amount_krw", sa.BigInteger()),
        sa.Column("amount_raw", sa.Text()),
        sa.Column("amount_unit", sa.String(length=32)),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "event_id",
            "filing_id",
            "table_id",
            "row_index",
            name="uq_fundraising_event_sources_occurrence",
        ),
    )
    op.create_index(
        "ix_fundraising_event_sources_event",
        "fundraising_event_sources",
        ["event_id"],
    )
    op.create_index(
        "ix_fundraising_event_sources_filing",
        "fundraising_event_sources",
        ["filing_id"],
    )


def downgrade() -> None:
    op.drop_table("fundraising_event_sources")
    op.drop_table("fundraising_events")
