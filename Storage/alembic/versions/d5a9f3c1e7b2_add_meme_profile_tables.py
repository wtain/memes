"""add meme profile tables

Revision ID: d5a9f3c1e7b2
Revises: c4d8e1f2a5b7
Create Date: 2026-10-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = 'd5a9f3c1e7b2'
down_revision: Union[str, Sequence[str], None] = 'c4d8e1f2a5b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Matches Storage.models.PROFILE_EMBEDDING_DIM. Hardcoded rather than imported --
# migrations must stay valid even if the model constant changes later.
PROFILE_EMBEDDING_DIM = 1024


def upgrade() -> None:
    op.create_table(
        'meme_profiles',
        sa.Column('image_id', sa.UUID(), nullable=False),
        sa.Column('taxonomy_version', sa.Integer(), nullable=False),
        sa.Column('extractor_model', sa.String(), nullable=False),
        sa.Column('extractor_prompt_version', sa.String(), nullable=False),
        sa.Column('inputs_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('situation_text', sa.Text(), nullable=True),
        sa.Column('situation_confidence', sa.Float(), nullable=True),
        sa.Column('use_when', sa.Text(), nullable=True),
        sa.Column('human_verified', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('extracted_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['image_id'], ['images.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('image_id'),
    )
    op.create_table(
        'meme_profile_values',
        sa.Column('image_id', sa.UUID(), nullable=False),
        sa.Column('axis', sa.String(length=16), nullable=False),
        sa.Column('value_key', sa.String(length=48), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('source', sa.String(length=8), nullable=False),
        sa.CheckConstraint("axis IN ('emotion', 'stance', 'topic', 'tone')", name='ck_meme_profile_values_axis'),
        sa.CheckConstraint("source IN ('model', 'human')", name='ck_meme_profile_values_source'),
        sa.ForeignKeyConstraint(['image_id'], ['meme_profiles.image_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('image_id', 'axis', 'value_key'),
    )
    op.create_index('ix_meme_profile_values_axis_value_key', 'meme_profile_values', ['axis', 'value_key'], unique=False)
    op.create_table(
        'meme_profile_situation_embeddings',
        sa.Column('image_id', sa.UUID(), nullable=False),
        sa.Column('embedding', Vector(PROFILE_EMBEDDING_DIM), nullable=True),
        sa.Column('model_name', sa.String(), nullable=False),
        sa.Column('model_digest', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['image_id'], ['meme_profiles.image_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('image_id'),
    )
    op.create_index(
        'ix_meme_profile_situation_embeddings_embedding',
        'meme_profile_situation_embeddings',
        ['embedding'],
        unique=False,
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )


def downgrade() -> None:
    op.drop_index('ix_meme_profile_situation_embeddings_embedding', table_name='meme_profile_situation_embeddings')
    op.drop_table('meme_profile_situation_embeddings')
    op.drop_index('ix_meme_profile_values_axis_value_key', table_name='meme_profile_values')
    op.drop_table('meme_profile_values')
    op.drop_table('meme_profiles')
