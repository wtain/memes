"""add comics candidates

Revision ID: a9d3e6b1c847
Revises: f3a8c1d6b952
Create Date: 2026-11-08 12:00:00.000000

Epic multi-image-comics, C9. Adds `comics_candidates` (suggested groups of images that may be one story) and
`comics_candidate_images` (the images of a group, in order).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a9d3e6b1c847'
down_revision: Union[str, Sequence[str], None] = 'f3a8c1d6b952'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'comics_candidates',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('state', sa.String(length=20), nullable=False, server_default='open'),
        sa.Column('detector_version', sa.String(), nullable=False),
        sa.Column('max_gap_seconds', sa.Float(), nullable=False),
        sa.Column('max_distance', sa.Float(), nullable=False),
        sa.Column('comics_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('comics.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('open', 'accepted', 'dismissed')", name='ck_comics_candidates_state'),
    )
    op.create_index('uq_comics_candidates_key', 'comics_candidates', ['key'], unique=True)
    op.create_index('ix_comics_candidates_state', 'comics_candidates', ['state'])

    op.create_table(
        'comics_candidate_images',
        sa.Column('candidate_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('comics_candidates.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('image_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('images.id', ondelete='CASCADE'),
                  primary_key=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.CheckConstraint('position >= 0', name='ck_comics_candidate_images_position'),
    )
    op.create_index('ix_comics_candidate_images_image_id', 'comics_candidate_images', ['image_id'])


def downgrade() -> None:
    op.drop_index('ix_comics_candidate_images_image_id', table_name='comics_candidate_images')
    op.drop_table('comics_candidate_images')
    op.drop_index('ix_comics_candidates_state', table_name='comics_candidates')
    op.drop_index('uq_comics_candidates_key', table_name='comics_candidates')
    op.drop_table('comics_candidates')
