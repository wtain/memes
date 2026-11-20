"""add tags search indexes

Revision ID: b7e2d4a9c615
Revises: a9d3e6b1c847
Create Date: 2026-11-20 12:00:00.000000

Audit finding ALG-7. Smart search looks tags up by `upper(value) = :x` (exact tier) and `value % :x`
(pg_trgm fuzzy tier), and the facet filter by `key = :k AND value IN (...)`; `tags` had only id and image_id
indexes, so each was a sequential scan. Adds an expression index, a GIN trigram index and a (key, value) index.

Plain CREATE INDEX (not CONCURRENTLY): it blocks writes to `tags` while it builds. Measured on 1,000,000
synthetic rows: about 2 s (upper), 6.5 s (trigram), 2.7 s (key, value), so about 11 s of blocked writes on a
table of that size, which is acceptable for a deploy-time migration and keeps the migration transactional
and testable. pg_trgm already exists (6fc209b37e8b); the IF NOT EXISTS keeps this migration self-sufficient.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b7e2d4a9c615'
down_revision: Union[str, Sequence[str], None] = 'a9d3e6b1c847'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index('ix_tags_value_upper', 'tags', [sa.text('upper(value)')], unique=False)
    op.create_index(
        'ix_tags_value_trgm', 'tags', ['value'],
        unique=False, postgresql_using='gin', postgresql_ops={'value': 'gin_trgm_ops'},
    )
    op.create_index('ix_tags_key_value', 'tags', ['key', 'value'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_tags_key_value', table_name='tags')
    op.drop_index('ix_tags_value_trgm', table_name='tags')
    op.drop_index('ix_tags_value_upper', table_name='tags')
