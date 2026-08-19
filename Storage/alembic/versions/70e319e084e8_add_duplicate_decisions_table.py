"""add duplicate_decisions table

Revision ID: 70e319e084e8
Revises: ecd5f1e7c0bd
Create Date: 2026-08-19 16:58:16.328778

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '70e319e084e8'
down_revision: Union[str, Sequence[str], None] = 'ecd5f1e7c0bd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'duplicate_decisions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('image_id1', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('image_id2', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_foreign_key(
        'duplicate_decisions_image_id1_fkey', 'duplicate_decisions', 'images',
        ['image_id1'], ['id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        'duplicate_decisions_image_id2_fkey', 'duplicate_decisions', 'images',
        ['image_id2'], ['id'], ondelete='CASCADE',
    )
    op.create_unique_constraint(
        'uq_duplicate_decisions_pair', 'duplicate_decisions', ['image_id1', 'image_id2'],
    )
    op.create_index('ix_duplicate_decisions_image_id1', 'duplicate_decisions', ['image_id1'])
    op.create_index('ix_duplicate_decisions_image_id2', 'duplicate_decisions', ['image_id2'])


def downgrade() -> None:
    op.drop_table('duplicate_decisions')
