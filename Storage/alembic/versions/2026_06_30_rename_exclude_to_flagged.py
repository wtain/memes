"""rename exclude to flagged

Revision ID: a1b2c3d4e5f6
Revises: d2d1ba3ab9f5
Create Date: 2026-06-30

"""
from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = 'd2d1ba3ab9f5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('image_extras', 'exclude', new_column_name='flagged')


def downgrade() -> None:
    op.alter_column('image_extras', 'flagged', new_column_name='exclude')
