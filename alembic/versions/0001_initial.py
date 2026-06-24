"""initial schema: tenders, tender_scores, tender_actions

Revision ID: 0001
Revises:
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenders",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("cpv", sa.JSON(), nullable=True),
        sa.Column("buyer", sa.String(), nullable=True),
        sa.Column("budget_amount", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(), nullable=False, server_default="EUR"),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="discovered"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "source_id", name="uq_tender_source"),
    )
    op.create_index("ix_tenders_source", "tenders", ["source"])
    op.create_index("ix_tenders_source_id", "tenders", ["source_id"])
    op.create_index("ix_tenders_status", "tenders", ["status"])

    op.create_table(
        "tender_scores",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tender_id", sa.String(), sa.ForeignKey("tenders.id"), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("breakdown", sa.JSON(), nullable=True),
        sa.Column("recommendation", sa.String(), nullable=False),
        sa.Column("hard_rules", sa.JSON(), nullable=True),
        sa.Column("factors", sa.JSON(), nullable=True),
        sa.Column("model_version", sa.String(), nullable=False, server_default="1.0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tender_scores_tender_id", "tender_scores", ["tender_id"])
    op.create_index("ix_tender_scores_recommendation", "tender_scores", ["recommendation"])

    op.create_table(
        "tender_actions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tender_id", sa.String(), sa.ForeignKey("tenders.id"), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tender_actions_tender_id", "tender_actions", ["tender_id"])
    op.create_index("ix_tender_actions_action", "tender_actions", ["action"])


def downgrade() -> None:
    op.drop_table("tender_actions")
    op.drop_table("tender_scores")
    op.drop_table("tenders")
