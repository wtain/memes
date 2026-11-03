"""add comics tables

Revision ID: d7a3b9c5e214
Revises: b4c9e1f7a028
Create Date: 2026-11-03 12:00:00.000000

Epic multi-image-comics, C1. Adds `comics` and `comics_panels` and the tenth popularity source `comics_build`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'd7a3b9c5e214'
down_revision: Union[str, Sequence[str], None] = 'b4c9e1f7a028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_SOURCES = ("hash_in_batch", "hash_cross_corpus", "ingest_tier_a", "ingest_tier_b",
                "cluster_review", "agent_review", "file_duplicates", "recut", "adjustment")
_NEW_SOURCES = _OLD_SOURCES + ("comics_build",)
_TABLE = 'popularity_absorptions'


def _in_list(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _replace_source_check(sources) -> None:
    op.drop_constraint('ck_popularity_absorptions_source', _TABLE, type_='check')
    op.create_check_constraint('ck_popularity_absorptions_source', _TABLE, _in_list('source', sources))


def upgrade() -> None:
    op.create_table(
        'comics',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('name_key', sa.String(), nullable=False),
        sa.Column('state', sa.String(length=20), nullable=False, server_default='draft'),
        sa.Column('built_image_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('images.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('built_content_hash', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('built_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('draft', 'built')", name='ck_comics_state'),
    )
    op.create_index('uq_comics_name_key', 'comics', ['name_key'], unique=True)
    op.create_index('uq_comics_built_image_id', 'comics', ['built_image_id'], unique=True)

    op.create_table(
        'comics_panels',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('comics_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('comics.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('image_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('images.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('source_filename', sa.String(), nullable=False),
        sa.Column('source_content_hash', sa.String(), nullable=True),
        sa.UniqueConstraint('comics_id', 'position', name='uq_comics_panels_position',
                            deferrable=True, initially='DEFERRED'),
        sa.CheckConstraint('position >= 0', name='ck_comics_panels_position'),
    )
    op.create_index('uq_comics_panels_image_id', 'comics_panels', ['image_id'], unique=True)

    _replace_source_check(_NEW_SOURCES)


def downgrade() -> None:
    count = op.get_bind().exec_driver_sql(
        "SELECT count(*) FROM popularity_absorptions WHERE source = 'comics_build'").scalar()
    if count:
        raise RuntimeError(
            f"Cannot downgrade: {count} popularity_absorptions row(s) have source 'comics_build', which the "
            "older constraint rejects. Delete them (and reconcile images.popularity) before downgrading.")
    _replace_source_check(_OLD_SOURCES)
    op.drop_index('uq_comics_panels_image_id', table_name='comics_panels')
    op.drop_table('comics_panels')
    op.drop_index('uq_comics_built_image_id', table_name='comics')
    op.drop_index('uq_comics_name_key', table_name='comics')
    op.drop_table('comics')
