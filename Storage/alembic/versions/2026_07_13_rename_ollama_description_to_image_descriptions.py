"""rename ollama_description to image_descriptions, add prompt/model tracking

Revision ID: f1a2b3c4d5e6
Revises: d4a1f7b2c9e6
Create Date: 2026-07-13

"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = 'f1a2b3c4d5e6'
down_revision = 'd4a1f7b2c9e6'
branch_labels = None
depends_on = None

# Matches Storage.models.TEXT_EMBEDDING_DIM. Hardcoded rather than imported —
# migrations must stay valid even if the model constant changes later.
TEXT_EMBEDDING_DIM = 1024


def upgrade() -> None:
    op.rename_table('ollama_description', 'image_descriptions')
    op.execute("ALTER INDEX ix_ollama_description_id RENAME TO ix_image_descriptions_id")
    op.execute("ALTER INDEX ix_ollama_description_image_id RENAME TO ix_image_descriptions_image_id")

    op.add_column('image_descriptions', sa.Column('prompt_key', sa.String(), nullable=True))
    op.add_column('image_descriptions', sa.Column('model_used', sa.String(), nullable=True))

    op.execute("UPDATE image_descriptions SET prompt_key = 'legacy' WHERE prompt_key IS NULL")
    op.execute("UPDATE image_descriptions SET model_used = 'llava' WHERE model_used IS NULL")

    op.alter_column('image_descriptions', 'prompt_key', nullable=False)
    op.alter_column('image_descriptions', 'model_used', nullable=False)

    op.create_unique_constraint(
        'uq_image_description_image_prompt', 'image_descriptions', ['image_id', 'prompt_key']
    )

    op.create_table(
        'image_description_embeddings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('image_description_id', sa.UUID(), nullable=True),
        sa.Column('embedding', Vector(TEXT_EMBEDDING_DIM), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['image_description_id'], ['image_descriptions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('image_description_id'),
    )
    op.create_index(
        op.f('ix_image_description_embeddings_image_description_id'),
        'image_description_embeddings', ['image_description_id'], unique=True,
    )
    op.create_index(
        op.f('ix_image_description_embeddings_embedding'),
        'image_description_embeddings', ['embedding'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_image_description_embeddings_embedding'), table_name='image_description_embeddings')
    op.drop_index(op.f('ix_image_description_embeddings_image_description_id'), table_name='image_description_embeddings')
    op.drop_table('image_description_embeddings')

    op.drop_constraint('uq_image_description_image_prompt', 'image_descriptions', type_='unique')
    op.drop_column('image_descriptions', 'model_used')
    op.drop_column('image_descriptions', 'prompt_key')

    op.execute("ALTER INDEX ix_image_descriptions_image_id RENAME TO ix_ollama_description_image_id")
    op.execute("ALTER INDEX ix_image_descriptions_id RENAME TO ix_ollama_description_id")
    op.rename_table('image_descriptions', 'ollama_description')
