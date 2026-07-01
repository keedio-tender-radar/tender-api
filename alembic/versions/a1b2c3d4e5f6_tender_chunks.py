"""tender_chunks (chat documental / RAG por expediente)

Revision ID: a1b2c3d4e5f6
Revises: 3f05d2a4945b
Create Date: 2026-07-01 09:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = '3f05d2a4945b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'tender_chunks',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tender_id', sa.String(), nullable=False),
        sa.Column('document_id', sa.String(), nullable=True),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('section', sa.String(), nullable=True),
        sa.Column('content', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tender_id'], ['tenders.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_tender_chunks_tender_id'), 'tender_chunks', ['tender_id'], unique=False)
    op.create_index(
        op.f('ix_tender_chunks_document_id'), 'tender_chunks', ['document_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_tender_chunks_document_id'), table_name='tender_chunks')
    op.drop_index(op.f('ix_tender_chunks_tender_id'), table_name='tender_chunks')
    op.drop_table('tender_chunks')
