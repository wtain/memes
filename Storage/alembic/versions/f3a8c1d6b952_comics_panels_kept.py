"""comics_panels.kept

Revision ID: f3a8c1d6b952
Revises: d7a3b9c5e214
Create Date: 2026-11-05 12:00:00.000000

Epic multi-image-comics, C4. Adds `comics_panels.kept`: true when the owner chose to keep the panel's image in the
library after the build (so the build does not retire it).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f3a8c1d6b952'
down_revision: Union[str, Sequence[str], None] = 'd7a3b9c5e214'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('comics_panels',
                  sa.Column('kept', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('comics_panels', 'kept')
