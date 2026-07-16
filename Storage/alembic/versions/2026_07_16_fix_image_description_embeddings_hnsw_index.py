"""fix image_description_embeddings index — btree can't index 1024-dim vectors

Revision ID: a3f9c1d8b6e2
Revises: f1a2b3c4d5e6
Create Date: 2026-07-16

"""
from alembic import op

revision = 'a3f9c1d8b6e2'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The original migration's op.create_index() defaulted to a plain btree
    # index, which Postgres caps at ~2704 bytes per indexed row — a 1024-dim
    # vector row is 4112 bytes, so every insert into this table failed
    # outright. hnsw (pgvector's ANN index type) has no such cap, and is the
    # correct index type for a cosine-distance vector column — note it does
    # not accelerate get_similar_by_description's join-based distance
    # computation (that compares two joined columns, not a column against a
    # constant query vector), only true nearest-neighbor-style queries
    # (ORDER BY embedding <=> :query_vector).
    op.drop_index('ix_image_description_embeddings_embedding', table_name='image_description_embeddings')
    op.create_index(
        'ix_image_description_embeddings_embedding',
        'image_description_embeddings',
        ['embedding'],
        unique=False,
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )


def downgrade() -> None:
    op.drop_index('ix_image_description_embeddings_embedding', table_name='image_description_embeddings')
    op.create_index(
        'ix_image_description_embeddings_embedding',
        'image_description_embeddings',
        ['embedding'],
        unique=False,
    )
