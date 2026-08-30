"""Add full-corpus source event and facility-investment storage.

Revision ID: 20260830_03_facility_investment
Revises: 20260829_02_generic_facts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_03_facility_investment"
down_revision: str | None = "20260829_02_generic_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_events",
        sa.Column("event_id", sa.String(length=192), primary_key=True),
        sa.Column(
            "filing_id",
            sa.String(length=128),
            sa.ForeignKey("source_filings.filing_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "corp_code",
            sa.String(length=16),
            sa.ForeignKey("source_companies.corp_code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_date", sa.Date()),
        sa.UniqueConstraint("filing_id", "event_type", name="uq_source_events_filing_type"),
    )
    op.create_index("ix_source_events_corp_type", "source_events", ["corp_code", "event_type"])
    op.create_index("ix_source_events_type_date", "source_events", ["event_type", "event_date"])

    op.create_table(
        "source_event_evidence",
        sa.Column(
            "event_id",
            sa.String(length=192),
            sa.ForeignKey("source_events.event_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("attribute", sa.String(length=64), primary_key=True),
        sa.Column(
            "fact_id",
            sa.String(length=64),
            sa.ForeignKey("generic_facts.fact_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("event_id", "attribute", name="uq_source_event_evidence_attribute"),
    )
    op.create_index("ix_source_event_evidence_fact", "source_event_evidence", ["fact_id"])

    op.create_table(
        "facility_investment_events",
        sa.Column(
            "filing_id",
            sa.String(length=128),
            sa.ForeignKey("source_filings.filing_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "event_id",
            sa.String(length=192),
            sa.ForeignKey("source_events.event_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("investment_type", sa.Text()),
        sa.Column("investment_subject", sa.Text()),
        sa.Column("investment_amount_krw", sa.BigInteger()),
        sa.Column("equity_krw", sa.BigInteger()),
        sa.Column("equity_ratio", sa.Numeric(20, 8)),
        sa.Column("purpose", sa.Text()),
        sa.Column("investment_start_date", sa.Date()),
        sa.Column("investment_end_date", sa.Date()),
        sa.Column("decision_date", sa.Date()),
        sa.Column("defer_reason", sa.Text()),
        sa.Column("defer_until", sa.Date()),
        sa.Column("notes", sa.Text()),
    )
    op.create_index(
        "ix_facility_investment_amount",
        "facility_investment_events",
        ["investment_amount_krw"],
    )
    op.create_index(
        "ix_facility_investment_decision_date",
        "facility_investment_events",
        ["decision_date"],
    )


def downgrade() -> None:
    op.drop_table("facility_investment_events")
    op.drop_table("source_event_evidence")
    op.drop_table("source_events")
