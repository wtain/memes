"""add image_cuts and stale columns

Revision ID: b8d4f1a6c935
Revises: f3a9c27b5d14
Create Date: 2026-10-17 12:00:00.000000

Epic screenshot-recut, project R1. Adds the image_cuts relation and a stale flag on the four derived
tables whose jobs select work by absence of rows. NOT NULL DEFAULT false is a metadata-only change on
PostgreSQL 11+ (the three environments run 18.3), so no table is rewritten.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b8d4f1a6c935'
down_revision: Union[str, Sequence[str], None] = 'f3a9c27b5d14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STALE_TABLES = ("ocr_texts", "ocr_lemmas", "image_descriptions", "tags")
LIVE_PREDICATE = "state IN ('detected', 'proposed', 'approved')"


def upgrade() -> None:
    op.create_table(
        'image_cuts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('source_image_id', sa.UUID(), nullable=True),
        sa.Column('cut_image_id', sa.UUID(), nullable=True),
        sa.Column('source_content_hash', sa.String(), nullable=True),
        sa.Column('crop_x', sa.Integer(), nullable=False),
        sa.Column('crop_y', sa.Integer(), nullable=False),
        sa.Column('crop_w', sa.Integer(), nullable=False),
        sa.Column('crop_h', sa.Integer(), nullable=False),
        sa.Column('detector', sa.String(length=50), nullable=False),
        sa.Column('detector_version', sa.String(length=50), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('detect_run_id', sa.UUID(), nullable=True),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('crop_x >= 0 AND crop_y >= 0 AND crop_w > 0 AND crop_h > 0', name='ck_image_cuts_box'),
        sa.CheckConstraint("state IN ('detected', 'proposed', 'approved', 'rejected', 'failed')",
                           name='ck_image_cuts_state'),
        sa.ForeignKeyConstraint(['source_image_id'], ['images.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['cut_image_id'], ['images.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['detect_run_id'], ['batch_runs.run_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('uq_image_cuts_live_source', 'image_cuts', ['source_image_id'], unique=True,
                    postgresql_where=sa.text(LIVE_PREDICATE))
    op.create_index('uq_image_cuts_cut_image_id', 'image_cuts', ['cut_image_id'], unique=True)
    op.create_index('ix_image_cuts_detect_run_id', 'image_cuts', ['detect_run_id'])
    op.create_index('ix_image_cuts_state', 'image_cuts', ['state'])

    for table in STALE_TABLES:
        op.add_column(table, sa.Column('stale', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    for table in reversed(STALE_TABLES):
        op.drop_column(table, 'stale')
    op.drop_index('ix_image_cuts_state', table_name='image_cuts')
    op.drop_index('ix_image_cuts_detect_run_id', table_name='image_cuts')
    op.drop_index('uq_image_cuts_cut_image_id', table_name='image_cuts')
    op.drop_index('uq_image_cuts_live_source', table_name='image_cuts')
    op.drop_table('image_cuts')
