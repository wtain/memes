"""add meme text ratings

Revision ID: e6b1c4d8f203
Revises: d5a9f3c1e7b2
Create Date: 2026-10-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e6b1c4d8f203'
down_revision: Union[str, Sequence[str], None] = 'd5a9f3c1e7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'meme_text_ratings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('query_hash', sa.String(length=64), nullable=False),
        sa.Column('query_text', sa.Text(), nullable=True),
        sa.Column('image_id', sa.UUID(), nullable=False),
        sa.Column('grade', sa.Integer(), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('reading', sa.String(length=8), nullable=False),
        sa.Column('parser_version', sa.String(), nullable=False),
        sa.Column('taxonomy_version', sa.Integer(), nullable=False),
        sa.Column('degraded', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('fallback', sa.String(), server_default='none', nullable=False),
        sa.Column('language', sa.String(length=8), nullable=False),
        sa.Column('rated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('grade IN (0, 1, 2)', name='ck_meme_text_ratings_grade'),
        sa.CheckConstraint('rank >= 1', name='ck_meme_text_ratings_rank'),
        sa.CheckConstraint("reading IN ('parsed', 'edited')", name='ck_meme_text_ratings_reading'),
        sa.ForeignKeyConstraint(['image_id'], ['images.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('query_hash', 'image_id', name='uq_meme_text_ratings_query_image'),
        comment=(
            "query_hash is sha256 of the message normalised like the parser (NFC, collapsed whitespace, cut at "
            "parse.max_input_chars); query_text is stored only because a rating exists and can be cleared per message"
        ),
    )
    op.create_index('ix_meme_text_ratings_rated_at', 'meme_text_ratings', ['rated_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_meme_text_ratings_rated_at', table_name='meme_text_ratings')
    op.drop_table('meme_text_ratings')
