"""The search-outcomes migration creates exactly what the ORM declares, and downgrades cleanly."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

MIGRATION = (Path(__file__).resolve().parents[2] / "Storage" / "alembic" / "versions"
             / "b4c9e1f7a028_add_search_outcomes.py")


def _load():
    spec = importlib.util.spec_from_file_location("migration_b4c9e1f7a028", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, function):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        function()


def _norm(value):
    if value is None:
        return None
    return "".join(ch for ch in str(value).lower() if not ch.isspace() and ch not in "()")


def _shape(connection, table):
    inspector = inspect(connection)
    columns = {c["name"]: (str(c["type"]), c["nullable"], _norm(c["default"]))
               for c in inspector.get_columns(table)}
    indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"])) for i in inspector.get_indexes(table)}
    uniques = sorted(tuple(u["column_names"]) for u in inspector.get_unique_constraints(table))
    return columns, indexes, uniques, inspector.get_foreign_keys(table)


@pytest.mark.asyncio(loop_scope="session")
async def test_the_migration_matches_the_models_and_downgrades(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            saves = await connection.run_sync(lambda s: _shape(s, "meme_saves"))
            outcomes = await connection.run_sync(lambda s: _shape(s, "search_not_found"))
            assert saves[3] == [] and outcomes[3] == []
            assert saves[0]["search_history_id"][1] is True  # nullable
            assert "ix_meme_saves_search_history_id" in saves[1]

            await connection.execute(text("DROP TABLE search_not_found"))
            await connection.execute(text("DROP INDEX ix_meme_saves_search_history_id"))
            await connection.execute(text("ALTER TABLE meme_saves DROP COLUMN search_history_id"))
            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            assert await connection.run_sync(lambda s: _shape(s, "meme_saves")) == saves
            assert await connection.run_sync(lambda s: _shape(s, "search_not_found")) == outcomes

            await connection.run_sync(lambda sync: _run(sync, module.downgrade))
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            columns = await connection.run_sync(
                lambda sync: {c["name"] for c in inspect(sync).get_columns("meme_saves")})
            assert "search_not_found" not in tables and "search_history_id" not in columns
        finally:
            await transaction.rollback()


def test_the_migration_continues_the_chain():
    module = _load()
    assert module.revision == "b4c9e1f7a028" and module.down_revision == "a2f6d8c3b715"
