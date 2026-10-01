"""add description_lemmas table

Revision ID: c4d8e1f2a5b7
Revises: b7e2c4a91d30
Create Date: 2026-10-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4d8e1f2a5b7'
down_revision: Union[str, Sequence[str], None] = 'b7e2c4a91d30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'description_lemmas',
        sa.Column('image_description_id', sa.UUID(), nullable=False),
        sa.Column('lemma', sa.String(), nullable=False),
        sa.Column('phonetic_code', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['image_description_id'], ['image_descriptions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('image_description_id', 'lemma'),
    )
    op.create_index('ix_description_lemmas_lemma', 'description_lemmas', ['lemma'], unique=False)
    # pg_trgm extension already created by 6fc209b37e8b_add_ocr_lemmas_trigram_index.py
    op.create_index(
        'ix_description_lemmas_lemma_trgm', 'description_lemmas', ['lemma'],
        unique=False, postgresql_using='gin',
        postgresql_ops={'lemma': 'gin_trgm_ops'},
    )


def downgrade() -> None:
    op.drop_table('description_lemmas')
