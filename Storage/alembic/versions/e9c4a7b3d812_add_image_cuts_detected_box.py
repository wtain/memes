"""add image_cuts.detected_crop_x/y/w/h

Revision ID: e9c4a7b3d812
Revises: c8e2f5a1d093
Create Date: 2026-10-30 10:00:00.000000

Epic screenshot-recut, project R6. Four nullable integers with no default: no table rewrite.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e9c4a7b3d812'
down_revision: Union[str, Sequence[str], None] = 'c8e2f5a1d093'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = ("detected_crop_x", "detected_crop_y", "detected_crop_w", "detected_crop_h")


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column('image_cuts', sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column('image_cuts', name)
