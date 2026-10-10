"""add meme text query log columns

Revision ID: f3a9c27b5d14
Revises: e6b1c4d8f203
Create Date: 2026-10-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f3a9c27b5d14'
down_revision: Union[str, Sequence[str], None] = 'e6b1c4d8f203'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("search_history", sa.Column("kind", sa.String(20), nullable=False, server_default="search"))
    op.add_column("search_history", sa.Column("language", sa.String(8), nullable=True))
    op.add_column("search_history", sa.Column("query_hash", sa.String(64), nullable=True))
    op.add_column("search_history", sa.Column("situation", sa.Text(), nullable=True))
    op.add_column("search_history", sa.Column("degraded", sa.Boolean(), nullable=True))
    op.add_column("search_history", sa.Column("fallback", sa.String(64), nullable=True))
    op.add_column("search_history", sa.Column("parse_ms", sa.Integer(), nullable=True))
    op.add_column("search_history", sa.Column("retrieval_ms", sa.Integer(), nullable=True))
    op.create_index("ix_search_history_kind_searched_at", "search_history",
                    ["kind", sa.text("searched_at DESC")])
    op.create_index("ix_search_history_query_hash", "search_history", ["query_hash"])


def downgrade() -> None:
    op.drop_index("ix_search_history_query_hash", table_name="search_history")
    op.drop_index("ix_search_history_kind_searched_at", table_name="search_history")
    for column in ("retrieval_ms", "parse_ms", "fallback", "degraded", "situation", "query_hash", "language", "kind"):
        op.drop_column("search_history", column)
