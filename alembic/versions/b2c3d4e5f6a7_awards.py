"""awards (inteligencia de mercado / MVP-5)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-01 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: str | None = 'a1b2c3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'awards',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('source_id', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('buyer', sa.String(), nullable=True),
        sa.Column('cpv', sa.JSON(), nullable=False),
        sa.Column('cpv_division', sa.String(), nullable=True),
        sa.Column('budget_amount', sa.Float(), nullable=True),
        sa.Column('awarded_amount', sa.Float(), nullable=True),
        sa.Column('awarded_supplier', sa.String(), nullable=True),
        sa.Column('num_bidders', sa.Integer(), nullable=True),
        sa.Column('award_date', sa.Date(), nullable=True),
        sa.Column('url', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source', 'source_id', name='uq_award_source'),
    )
    op.create_index(op.f('ix_awards_source'), 'awards', ['source'], unique=False)
    op.create_index(op.f('ix_awards_source_id'), 'awards', ['source_id'], unique=False)
    op.create_index(op.f('ix_awards_buyer'), 'awards', ['buyer'], unique=False)
    op.create_index(op.f('ix_awards_cpv_division'), 'awards', ['cpv_division'], unique=False)
    op.create_index(
        op.f('ix_awards_awarded_supplier'), 'awards', ['awarded_supplier'], unique=False
    )
    op.create_index(op.f('ix_awards_award_date'), 'awards', ['award_date'], unique=False)


def downgrade() -> None:
    for ix in (
        'ix_awards_award_date', 'ix_awards_awarded_supplier', 'ix_awards_cpv_division',
        'ix_awards_buyer', 'ix_awards_source_id', 'ix_awards_source',
    ):
        op.drop_index(op.f(ix), table_name='awards')
    op.drop_table('awards')
