"""The ratings migration creates exactly what the ORM model declares, and downgrades cleanly."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

MIGRATION = Path(__file__).resolve().parents[2] / "Storage" / "alembic" / "versions" / "e6b1c4d8f203_add_meme_text_ratings.py"


def _load():
    spec = importlib.util.spec_from_file_location("migration_e6b1c4d8f203", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, function):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        function()


def _shape(connection):
    inspector = inspect(connection)
    columns = {c["name"]: (str(c["type"]), c["nullable"]) for c in inspector.get_columns("meme_text_ratings")}
    uniques = {u["name"]: tuple(u["column_names"]) for u in inspector.get_unique_constraints("meme_text_ratings")}
    checks = {c["name"] for c in inspector.get_check_constraints("meme_text_ratings")}
    indexes = {i["name"] for i in inspector.get_indexes("meme_text_ratings")}
    foreign = {tuple(f["constrained_columns"]): (f["referred_table"], f["options"].get("ondelete"))
               for f in inspector.get_foreign_keys("meme_text_ratings")}
    return columns, uniques, checks, indexes, foreign


@pytest.mark.asyncio(loop_scope="session")
async def test_the_migration_matches_the_model_and_downgrades(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            # the session fixture created the table from the model: capture its shape, then rebuild it from the migration
            model_shape = await connection.run_sync(_shape)
            await connection.execute(text("DROP TABLE meme_text_ratings"))

            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            migrated_shape = await connection.run_sync(_shape)
            assert migrated_shape == model_shape

            await connection.run_sync(lambda sync: _run(sync, module.downgrade))
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            assert "meme_text_ratings" not in tables
        finally:
            await transaction.rollback()


def test_the_migration_continues_the_chain():
    module = _load()
    assert module.revision == "e6b1c4d8f203" and module.down_revision == "d5a9f3c1e7b2"
