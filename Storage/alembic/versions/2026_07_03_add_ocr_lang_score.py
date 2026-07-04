"""add lang_score to ocr_texts

Revision ID: b7f3c9a2d4e1
Revises: a1b2c3d4e5f6
Create Date: 2026-07-03

"""
from alembic import op
import sqlalchemy as sa

revision = 'b7f3c9a2d4e1'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ocr_texts', sa.Column('lang_score', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('ocr_texts', 'lang_score')
