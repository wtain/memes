"""add image_cuts.failure_reason

Revision ID: d7a3b9e2c451
Revises: c5e2a9d7b140
Create Date: 2026-10-26 10:00:00.000000

Epic screenshot-recut, project R3a. Nullable with no default, so adding it does not rewrite the table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd7a3b9e2c451'
down_revision: Union[str, Sequence[str], None] = 'c5e2a9d7b140'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('image_cuts', sa.Column('failure_reason', sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column('image_cuts', 'failure_reason')
