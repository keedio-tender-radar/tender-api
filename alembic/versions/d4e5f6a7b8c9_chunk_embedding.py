"""tender_chunks.embedding (RAG semántico)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-02 13:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: str | None = 'c3d4e5f6a7b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('tender_chunks', sa.Column('embedding', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('tender_chunks', 'embedding')
