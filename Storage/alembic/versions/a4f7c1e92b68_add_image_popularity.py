"""add images.popularity and popularity_absorptions

Revision ID: a4f7c1e92b68
Revises: d7a3b9e2c451
Create Date: 2026-10-26 12:00:00.000000

Epic meme-popularity, project P1. A constant server default makes the new column metadata-only in
Postgres, so every existing image reads 1 without a table rewrite. The log table has no foreign key to
images on purpose: ids outlive deleted images.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a4f7c1e92b68'
down_revision: Union[str, Sequence[str], None] = 'd7a3b9e2c451'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SOURCES = ("hash_in_batch", "hash_cross_corpus", "ingest_tier_a", "ingest_tier_b",
            "cluster_review", "agent_review", "file_duplicates", "recut")
_REASONS = ("undo_reject", "unflag", "abort", "correction")


def _in_list(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.add_column('images', sa.Column('popularity', sa.Integer(), nullable=False, server_default='1'))
    op.create_check_constraint('ck_images_popularity', 'images', 'popularity >= 0')

    op.create_table(
        'popularity_absorptions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('loser_image_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('winner_image_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('weight', sa.Integer(), nullable=False),
        sa.Column('loser_filename', sa.String(), nullable=True),
        sa.Column('loser_content_hash', sa.String(), nullable=True),
        sa.Column('winner_content_hash', sa.String(), nullable=True),
        sa.Column('dedupe_key', sa.String(), nullable=True),
        sa.Column('batch_run_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('reverses_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('reversed_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('reason', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['batch_run_id'], ['batch_runs.run_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['reverses_id'], ['popularity_absorptions.id']),
        sa.ForeignKeyConstraint(['reversed_by_id'], ['popularity_absorptions.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dedupe_key', name='uq_popularity_dedupe_key'),
        sa.UniqueConstraint('reverses_id', name='uq_popularity_reverses_id'),
        sa.CheckConstraint(_in_list('kind', ('absorb', 'reverse')), name='ck_popularity_absorptions_kind'),
        sa.CheckConstraint('weight >= 0', name='ck_popularity_absorptions_weight'),
        sa.CheckConstraint(_in_list('source', _SOURCES), name='ck_popularity_absorptions_source'),
        sa.CheckConstraint(f"reason IS NULL OR {_in_list('reason', _REASONS)}",
                           name='ck_popularity_absorptions_reason'),
        sa.CheckConstraint(
            "(kind = 'absorb' AND reverses_id IS NULL AND reason IS NULL) OR "
            "(kind = 'reverse' AND reverses_id IS NOT NULL AND reason IS NOT NULL)",
            name='ck_popularity_absorptions_shape'),
    )
    op.create_index(
        'uq_popularity_open_loser', 'popularity_absorptions', ['loser_image_id'], unique=True,
        postgresql_where=sa.text("kind = 'absorb' AND reversed_by_id IS NULL AND loser_image_id IS NOT NULL"))
    op.create_index(
        'ix_popularity_open_winner', 'popularity_absorptions', ['winner_image_id'],
        postgresql_where=sa.text("kind = 'absorb' AND reversed_by_id IS NULL"))
    op.create_index('ix_popularity_batch_run_id', 'popularity_absorptions', ['batch_run_id'])


def downgrade() -> None:
    op.drop_index('ix_popularity_batch_run_id', table_name='popularity_absorptions')
    op.drop_index('ix_popularity_open_winner', table_name='popularity_absorptions')
    op.drop_index('uq_popularity_open_loser', table_name='popularity_absorptions')
    op.drop_table('popularity_absorptions')
    op.drop_constraint('ck_images_popularity', 'images', type_='check')
    op.drop_column('images', 'popularity')
