"""Make tmp_duplicates a persistent migration-managed table with match_source/tier review columns

Revision ID: fa057003a158
Revises: 51b3d777ab8a
Create Date: 2026-07-25 00:00:02.000000

`tmp_duplicates` was previously created ad hoc by batch/rebuild_duplicates.py's own
`CREATE TABLE ... AS SELECT`, dropped and fully recreated on every run (a full O(n^2)
cross join over every image -- see docs/adr/adr-2026-07-10-tmp-duplicates-fk-index.md,
which measured 489,389,056 rows / ~84GB for 22,402 images on `general`). This migration
replaces that with a real, persistent, migration-owned table that the rewritten
rebuild_duplicates.py inserts into incrementally (INSERT ... ON CONFLICT DO NOTHING).

The pre-existing script-created table (whatever shape it happens to be in on a given
environment) is dropped unconditionally before creating the new one. This is safe:
tmp_duplicates is a derived cache, not source data -- nothing else depends on its
content surviving a rebuild (clusterize.py fully reconstructs tmp_clusters from it
every time already). A `--full` rebuild (see rebuild_duplicates.py) regenerates it,
cheaply, using the new HNSW-assisted KNN query instead of the old cross join.

See docs/superpowers/specs/2026-07-25-duplicate-clustering-incremental-design.md.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'fa057003a158'
down_revision: Union[str, Sequence[str], None] = '51b3d777ab8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tmp_duplicates")

    op.create_table(
        'tmp_duplicates',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('image_id1', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('image_id2', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('distance', sa.Float(), nullable=False),
        sa.Column('match_source', sa.String(length=20), nullable=True),
        sa.Column('tier_a_reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tier_b_reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_foreign_key(
        'tmp_duplicates_image_id1_fkey', 'tmp_duplicates', 'images',
        ['image_id1'], ['id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        'tmp_duplicates_image_id2_fkey', 'tmp_duplicates', 'images',
        ['image_id2'], ['id'], ondelete='CASCADE',
    )
    op.create_unique_constraint('uq_tmp_duplicates_pair', 'tmp_duplicates', ['image_id1', 'image_id2'])

    # Postgres does not auto-index the referencing side of a FK -- without these,
    # cascade deletes force a full sequential scan per the ADR above.
    op.create_index('ix_tmp_duplicates_image_id1', 'tmp_duplicates', ['image_id1'])
    op.create_index('ix_tmp_duplicates_image_id2', 'tmp_duplicates', ['image_id2'])
    op.create_index('idx_tmp_duplicates_distance', 'tmp_duplicates', ['distance'])


def downgrade() -> None:
    op.drop_table('tmp_duplicates')
