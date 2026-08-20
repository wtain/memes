"""add description notes tables

Revision ID: e946daa96901
Revises: 70e319e084e8
Create Date: 2026-08-20 20:06:58.362783

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = 'e946daa96901'
down_revision: Union[str, Sequence[str], None] = '70e319e084e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Matches Storage.models.TEXT_EMBEDDING_DIM. Hardcoded rather than imported --
# migrations must stay valid even if the model constant changes later.
TEXT_EMBEDDING_DIM = 1024


def upgrade() -> None:
    op.create_table(
        'description_notes',
        sa.Column('image_id', sa.UUID(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('lemmas_built_at', sa.DateTime(), nullable=True),
        sa.Column('embedding_built_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['image_id'], ['images.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('image_id'),
    )

    op.create_table(
        'description_note_embeddings',
        sa.Column('description_note_id', sa.UUID(), nullable=False),
        sa.Column('embedding', Vector(TEXT_EMBEDDING_DIM), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['description_note_id'], ['description_notes.image_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('description_note_id'),
    )
    # Created directly as HNSW/cosine in one step -- do not repeat the
    # btree-then-fix history image_description_embeddings went through
    # (2026_07_13 created a default btree index, fixed by a follow-up
    # 2026_07_16 migration).
    op.create_index(
        'ix_description_note_embeddings_embedding',
        'description_note_embeddings',
        ['embedding'],
        unique=False,
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )

    op.create_table(
        'description_note_lemmas',
        sa.Column('image_id', sa.UUID(), nullable=False),
        sa.Column('lemma', sa.String(), nullable=False),
        sa.Column('phonetic_code', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['image_id'], ['images.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('image_id', 'lemma'),
    )
    op.create_index('ix_description_note_lemmas_lemma', 'description_note_lemmas', ['lemma'], unique=False)
    # pg_trgm extension already created by 6fc209b37e8b_add_ocr_lemmas_trigram_index.py
    op.create_index(
        'ix_description_note_lemmas_lemma_trgm', 'description_note_lemmas', ['lemma'],
        unique=False, postgresql_using='gin',
        postgresql_ops={'lemma': 'gin_trgm_ops'}
    )


def downgrade() -> None:
    # Drop in reverse order of creation. Use IF EXISTS to handle cases where
    # conftest or other cleanup has already dropped tables.
    op.execute(text('DROP INDEX IF EXISTS ix_description_note_lemmas_lemma_trgm'))
    op.execute(text('DROP INDEX IF EXISTS ix_description_note_lemmas_lemma'))
    op.execute(text('DROP TABLE IF EXISTS description_note_lemmas'))
    op.execute(text('DROP INDEX IF EXISTS ix_description_note_embeddings_embedding'))
    op.execute(text('DROP TABLE IF EXISTS description_note_embeddings'))
    op.execute(text('DROP TABLE IF EXISTS description_notes'))
