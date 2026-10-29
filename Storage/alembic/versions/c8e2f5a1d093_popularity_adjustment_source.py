"""popularity: adjustment source and signed weight

Revision ID: c8e2f5a1d093
Revises: a4f7c1e92b68
Create Date: 2026-10-29 12:00:00.000000

Epic meme-popularity, project P4. Adds the ninth log source `adjustment` (a manual "set popularity to N")
and lets only that source carry a signed, non-zero weight. Other sources keep `weight >= 0`.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c8e2f5a1d093'
down_revision: Union[str, Sequence[str], None] = 'a4f7c1e92b68'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_SOURCES = ("hash_in_batch", "hash_cross_corpus", "ingest_tier_a", "ingest_tier_b",
                "cluster_review", "agent_review", "file_duplicates", "recut")
_NEW_SOURCES = _OLD_SOURCES + ("adjustment",)
_TABLE = 'popularity_absorptions'


def _in_list(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _replace_checks(sources, weight_predicate: str) -> None:
    op.drop_constraint('ck_popularity_absorptions_source', _TABLE, type_='check')
    op.drop_constraint('ck_popularity_absorptions_weight', _TABLE, type_='check')
    op.create_check_constraint('ck_popularity_absorptions_source', _TABLE, _in_list('source', sources))
    op.create_check_constraint('ck_popularity_absorptions_weight', _TABLE, weight_predicate)


def upgrade() -> None:
    _replace_checks(_NEW_SOURCES,
                    "(source <> 'adjustment' AND weight >= 0) OR (source = 'adjustment' AND weight <> 0)")


def downgrade() -> None:
    count = op.get_bind().exec_driver_sql(
        "SELECT count(*) FROM popularity_absorptions WHERE source = 'adjustment'").scalar()
    if count:
        raise RuntimeError(
            f"Cannot downgrade: {count} popularity_absorptions row(s) have source 'adjustment', which the "
            "older constraints reject. Delete them (and reconcile images.popularity) before downgrading.")
    _replace_checks(_OLD_SOURCES, 'weight >= 0')
