"""Generalize trend sources: rename feed_sources, add connector config

Revision ID: d4a1f7b2c9e6
Revises: b7f3c9a2d4e1
Create Date: 2026-07-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a1f7b2c9e6'
down_revision: Union[str, Sequence[str], None] = 'b7f3c9a2d4e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table('feed_sources', 'trend_sources')

    op.add_column('trend_sources', sa.Column('connector_type', sa.String(length=50), nullable=True))
    op.add_column('trend_sources', sa.Column('config', sa.JSON(), nullable=True))
    op.add_column('trend_sources', sa.Column('extraction', sa.JSON(), nullable=True))

    op.execute("""
        UPDATE trend_sources
        SET connector_type = 'rss',
            config = json_build_object('url', url, 'selector', selector)
        WHERE connector_type IS NULL
    """)

    op.alter_column('trend_sources', 'connector_type', nullable=False)
    op.alter_column('trend_sources', 'config', nullable=False)

    op.drop_column('trend_sources', 'url')
    op.drop_column('trend_sources', 'selector')


def downgrade() -> None:
    op.add_column('trend_sources', sa.Column('url', sa.Text(), nullable=True))
    op.add_column('trend_sources', sa.Column('selector', sa.Text(), nullable=True))

    op.execute("""
        UPDATE trend_sources
        SET url = config->>'url',
            selector = config->>'selector'
        WHERE connector_type = 'rss'
    """)

    op.alter_column('trend_sources', 'url', nullable=False)
    op.alter_column('trend_sources', 'selector', nullable=False)

    op.drop_column('trend_sources', 'extraction')
    op.drop_column('trend_sources', 'config')
    op.drop_column('trend_sources', 'connector_type')

    op.rename_table('trend_sources', 'feed_sources')
