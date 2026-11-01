"""add meme_impressions

Revision ID: a2f6d8c3b715
Revises: d5b8e3a07c41
Create Date: 2026-11-01 12:00:00.000000

Epic meme-popularity, project S1. Append-only log of search impressions, one row per image returned in a search
result page. No foreign keys: ids outlive deleted images and cleared search history.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a2f6d8c3b715'
down_revision: Union[str, Sequence[str], None] = 'd5b8e3a07c41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'meme_impressions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('image_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('shown_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('rank', sa.SmallInteger(), nullable=False),
        sa.Column('surface', sa.String(length=16), nullable=False),
        sa.Column('search_history_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("surface IN ('search','similar','reply')", name='ck_meme_impressions_surface'),
    )
    op.create_index('ix_meme_impressions_image_id_shown_at', 'meme_impressions', ['image_id', 'shown_at'])
    op.create_index('ix_meme_impressions_search_history_id', 'meme_impressions', ['search_history_id'])


def downgrade() -> None:
    op.drop_index('ix_meme_impressions_search_history_id', table_name='meme_impressions')
    op.drop_index('ix_meme_impressions_image_id_shown_at', table_name='meme_impressions')
    op.drop_table('meme_impressions')
