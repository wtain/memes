"""The meme_impressions migration creates exactly what the ORM declares, and downgrades cleanly."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

MIGRATION = (Path(__file__).resolve().parents[2] / "Storage" / "alembic" / "versions"
             / "a2f6d8c3b715_add_meme_impressions.py")


def _load():
    spec = importlib.util.spec_from_file_location("migration_a2f6d8c3b715", MIGRATION)
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
    table = "meme_impressions"
    columns = {c["name"]: (str(c["type"]), c["nullable"], _norm(c["default"]))
               for c in inspector.get_columns(table)}
    indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"])) for i in inspector.get_indexes(table)}
    primary = tuple(inspector.get_pk_constraint(table)["constrained_columns"])
    foreign = inspector.get_foreign_keys(table)
    checks = {c["name"]: _norm(c["sqltext"]) for c in inspector.get_check_constraints(table)}
    return columns, indexes, primary, foreign, checks


@pytest.mark.asyncio(loop_scope="session")
async def test_the_migration_matches_the_model_and_downgrades(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            model_shape = await connection.run_sync(_shape)
            assert model_shape[3] == []
            assert "ix_meme_impressions_image_id_shown_at" in model_shape[1]
            assert "ix_meme_impressions_search_history_id" in model_shape[1]
            assert "ck_meme_impressions_surface" in model_shape[4]

            await connection.execute(text("DROP TABLE meme_impressions"))
            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            assert await connection.run_sync(_shape) == model_shape

            await connection.run_sync(lambda sync: _run(sync, module.downgrade))
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            assert "meme_impressions" not in tables
        finally:
            await transaction.rollback()


def test_the_migration_continues_the_chain():
    module = _load()
    assert module.revision == "a2f6d8c3b715" and module.down_revision == "d5b8e3a07c41"
