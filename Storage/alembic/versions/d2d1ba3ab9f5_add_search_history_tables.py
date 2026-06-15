"""add_search_history_tables

Revision ID: d2d1ba3ab9f5
Revises: 65281efbcb60
Create Date: 2026-06-15 23:34:35.116984

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2d1ba3ab9f5'
down_revision: Union[str, Sequence[str], None] = '65281efbcb60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('search_history',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('searched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('query', sa.Text(), nullable=True),
        sa.Column('client', sa.String(length=20), nullable=False),
        sa.Column('result_count', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_search_history_searched_at', 'search_history', [sa.literal_column('searched_at DESC')], unique=False)
    op.create_table('search_history_tags',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('search_id', sa.UUID(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('value', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['search_id'], ['search_history.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_search_history_tags_search_id'), 'search_history_tags', ['search_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_search_history_tags_search_id'), table_name='search_history_tags')
    op.drop_table('search_history_tags')
    op.drop_index('ix_search_history_searched_at', table_name='search_history')
    op.drop_table('search_history')
