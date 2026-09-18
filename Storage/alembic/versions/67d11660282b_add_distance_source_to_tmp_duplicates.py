"""add distance_source to tmp_duplicates

Revision ID: 67d11660282b
Revises: c358dccbcf48
Create Date: 2026-09-19 01:11:37.549592

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '67d11660282b'
down_revision: Union[str, Sequence[str], None] = 'c358dccbcf48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Note: autogenerate also picked up 'ix_duplicate_decisions_id' and 'ix_tmp_duplicates_id'
    # index-creation drift here (pre-existing, unrelated to this change -- both id columns are
    # already declared index=True in Storage/models.py but the indexes were never migrated).
    # Intentionally left out of this migration; the underlying drift is untouched, not fixed here.
    op.add_column('tmp_duplicates', sa.Column('distance_source', sa.String(length=20), server_default='clip', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tmp_duplicates', 'distance_source')
