"""add search outcomes

Revision ID: b4c9e1f7a028
Revises: a2f6d8c3b715
Create Date: 2026-11-01 15:00:00.000000

Epic meme-popularity, project S6. meme_saves.search_history_id links a save to the search that led to it, and
search_not_found is an append-only log of "I didn't find it" presses (one per search). No foreign keys: ids outlive
cleared search history.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b4c9e1f7a028'
down_revision: Union[str, Sequence[str], None] = 'a2f6d8c3b715'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('meme_saves', sa.Column('search_history_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index('ix_meme_saves_search_history_id', 'meme_saves', ['search_history_id'])
    op.create_table(
        'search_not_found',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('search_history_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('search_history_id'),
    )


def downgrade() -> None:
    op.drop_table('search_not_found')
    op.drop_index('ix_meme_saves_search_history_id', table_name='meme_saves')
    op.drop_column('meme_saves', 'search_history_id')
