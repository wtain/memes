"""add ocr_text_embeddings table

Revision ID: c358dccbcf48
Revises: 6f770efe4fb4
Create Date: 2026-09-17 17:26:05.800530

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = 'c358dccbcf48'
down_revision: Union[str, Sequence[str], None] = '6f770efe4fb4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Matches Storage.models.OCR_TEXT_EMBEDDING_DIM. Hardcoded rather than imported --
# migrations must stay valid even if the model constant changes later.
OCR_TEXT_EMBEDDING_DIM = 384


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ocr_text_embeddings',
        sa.Column('image_id', sa.UUID(), nullable=False),
        sa.Column('embedding', Vector(OCR_TEXT_EMBEDDING_DIM), nullable=True),
        sa.Column('computed_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['image_id'], ['images.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('image_id'),
    )
    op.create_index(
        'ix_ocr_text_embeddings_embedding',
        'ocr_text_embeddings',
        ['embedding'],
        unique=False,
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_ocr_text_embeddings_embedding', table_name='ocr_text_embeddings')
    op.drop_table('ocr_text_embeddings')
