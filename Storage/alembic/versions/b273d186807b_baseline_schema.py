"""baseline schema

Revision ID: b273d186807b
Revises: 
Create Date: 2026-02-25 11:21:58.154726

"""
from typing import Sequence, Union

from alembic import op
import pathlib


revision = "b273d186807b"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql_path = pathlib.Path(__file__).parent / "baseline.sql"
    with open(sql_path, "r") as f:
        op.execute(f.read())


def downgrade() -> None:
    raise NotImplementedError("Baseline downgrade not supported")
