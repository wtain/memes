"""partial index for unreviewed tier-b pairs

Revision ID: 3ce6b76e0b01
Revises: e946daa96901
Create Date: 2026-09-11 20:24:40.824876

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3ce6b76e0b01'
down_revision: Union[str, Sequence[str], None] = 'e946daa96901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # tmp_duplicates is large and continuously written (rebuild_duplicates.py inserts
    # incrementally, every Tier A/B review decision updates tier_*_reviewed_at) -- CONCURRENTLY
    # avoids a blocking lock, same reasoning as 2026_07_16_fix_embeddings_hnsw_index.py. Requires
    # autocommit_block() since CONCURRENTLY can't run inside a transaction. The partial predicate
    # is deliberately just "unreviewed", not the tier band's literal bounds -- see
    # docs/superpowers/specs/2026-09-11-ingestion-tier-b-review-partial-index.md's "Key facts".
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY idx_tmp_duplicates_tier_b_unreviewed_distance "
            "ON tmp_duplicates (distance) WHERE tier_b_reviewed_at IS NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY idx_tmp_duplicates_tier_b_unreviewed_distance")
