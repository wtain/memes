"""add_ocr_lemmas_trigram_index

Revision ID: 6fc209b37e8b
Revises: ffd5c2d55945
Create Date: 2026-07-24 19:52:36.633763

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6fc209b37e8b'
down_revision: Union[str, Sequence[str], None] = 'ffd5c2d55945'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        'ix_ocr_lemmas_lemma_trgm', 'ocr_lemmas', ['lemma'],
        unique=False, postgresql_using='gin',
        postgresql_ops={'lemma': 'gin_trgm_ops'}
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_ocr_lemmas_lemma_trgm', table_name='ocr_lemmas')
