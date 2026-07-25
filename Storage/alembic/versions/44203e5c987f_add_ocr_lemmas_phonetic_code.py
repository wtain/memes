"""add_ocr_lemmas_phonetic_code

Revision ID: 44203e5c987f
Revises: 6fc209b37e8b
Create Date: 2026-07-25 14:17:17.782877

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '44203e5c987f'
down_revision: Union[str, Sequence[str], None] = '6fc209b37e8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    from rules.phonetic import is_cyrillic_word, russian_metaphone

    op.add_column("ocr_lemmas", sa.Column("phonetic_code", sa.String(), nullable=True))

    # Backfill by distinct lemma, not by row: a lemma repeats across many
    # (image_id, lemma) rows, and phonetic_code is a pure function of
    # lemma, so updating once per distinct lemma (rather than once per
    # row) is enough and scales with vocabulary size, not corpus size.
    connection = op.get_bind()
    distinct_lemmas = connection.execute(
        sa.text("SELECT DISTINCT lemma FROM ocr_lemmas")
    ).fetchall()
    for row in distinct_lemmas:
        if not is_cyrillic_word(row.lemma):
            continue  # stays NULL -- phonetic matching never applies to non-Cyrillic lemmas
        code = russian_metaphone(row.lemma)
        connection.execute(
            sa.text("UPDATE ocr_lemmas SET phonetic_code = :code WHERE lemma = :lemma"),
            {"code": code, "lemma": row.lemma},
        )

    op.create_index("ix_ocr_lemmas_phonetic_code", "ocr_lemmas", ["phonetic_code"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_ocr_lemmas_phonetic_code", table_name="ocr_lemmas")
    op.drop_column("ocr_lemmas", "phonetic_code")
