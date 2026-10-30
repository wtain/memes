"""add meme_saves

Revision ID: d5b8e3a07c41
Revises: c8e2f5a1d093
Create Date: 2026-10-30 12:00:00.000000

Epic meme-popularity, project S2. Append-only log of meme saves, one row per save. No foreign key to
images: ids outlive deleted images so a duplicate tree keeps its count.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd5b8e3a07c41'
down_revision: Union[str, Sequence[str], None] = 'c8e2f5a1d093'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'meme_saves',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('image_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_meme_saves_image_id', 'meme_saves', ['image_id'])


def downgrade() -> None:
    op.drop_index('ix_meme_saves_image_id', table_name='meme_saves')
    op.drop_table('meme_saves')
