"""The meme_saves migration creates exactly what the ORM declares, and downgrades cleanly."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

MIGRATION = (Path(__file__).resolve().parents[2] / "Storage" / "alembic" / "versions"
             / "d5b8e3a07c41_add_meme_saves.py")


def _load():
    spec = importlib.util.spec_from_file_location("migration_d5b8e3a07c41", MIGRATION)
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


def _shape(connection):
    inspector = inspect(connection)
    table = "meme_saves"
    columns = {c["name"]: (str(c["type"]), c["nullable"], _norm(c["default"]))
               for c in inspector.get_columns(table)}
    indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"])) for i in inspector.get_indexes(table)}
    primary = tuple(inspector.get_pk_constraint(table)["constrained_columns"])
    foreign = inspector.get_foreign_keys(table)
    return columns, indexes, primary, foreign


@pytest.mark.asyncio(loop_scope="session")
async def test_the_migration_matches_the_model_and_downgrades(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            model_shape = await connection.run_sync(_shape)
            assert model_shape[3] == []
            assert "ix_meme_saves_image_id" in model_shape[1]

            await connection.execute(text("DROP TABLE meme_saves"))
            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            # The S6 migration (b4c9e1f7a028) adds the search link on top of this table; apply it so the shape
            # is the model's.
            await connection.execute(text("ALTER TABLE meme_saves ADD COLUMN search_history_id uuid"))
            await connection.execute(text("CREATE INDEX ix_meme_saves_search_history_id ON meme_saves (search_history_id)"))
            assert await connection.run_sync(_shape) == model_shape

            await connection.run_sync(lambda sync: _run(sync, module.downgrade))
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            assert "meme_saves" not in tables
        finally:
            await transaction.rollback()


def test_the_migration_continues_the_chain():
    module = _load()
    assert module.revision == "d5b8e3a07c41" and module.down_revision == "e9c4a7b3d812"
