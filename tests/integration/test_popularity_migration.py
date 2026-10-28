"""The popularity migration creates exactly what the ORM declares, and downgrades cleanly."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

MIGRATION = (Path(__file__).resolve().parents[2] / "Storage" / "alembic" / "versions"
             / "a4f7c1e92b68_add_image_popularity.py")


def _load():
    spec = importlib.util.spec_from_file_location("migration_a4f7c1e92b68", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, function):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        function()


def _norm(value):
    """Lower-case and drop whitespace/parentheses so cosmetic renderings of the same SQL compare equal."""
    if value is None:
        return None
    return "".join(ch for ch in str(value).lower() if not ch.isspace() and ch not in "()")


def _log_shape(connection):
    inspector = inspect(connection)
    table = "popularity_absorptions"
    columns = {c["name"]: (str(c["type"]), c["nullable"], _norm(c["default"]))
               for c in inspector.get_columns(table)}
    checks = {c["name"]: _norm(c["sqltext"]) for c in inspector.get_check_constraints(table)}
    indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"]),
                           _norm(i.get("dialect_options", {}).get("postgresql_where")))
               for i in inspector.get_indexes(table)}
    uniques = {u["name"]: tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)}
    primary = tuple(inspector.get_pk_constraint(table)["constrained_columns"])
    foreign = {tuple(f["constrained_columns"]): (f["referred_table"], f["options"].get("ondelete"))
               for f in inspector.get_foreign_keys(table)}
    return columns, checks, indexes, uniques, primary, foreign


def _popularity_shape(connection):
    inspector = inspect(connection)
    column = next((c for c in inspector.get_columns("images") if c["name"] == "popularity"), None)
    check = {c["name"]: _norm(c["sqltext"]) for c in inspector.get_check_constraints("images")
             if c["name"] == "ck_images_popularity"}
    if column is None:
        return None, check
    return (str(column["type"]), column["nullable"], _norm(column["default"])), check


def _both(connection):
    return _log_shape(connection), _popularity_shape(connection)


async def _drop_what_the_migration_adds(connection):
    await connection.execute(text("DROP TABLE popularity_absorptions"))
    await connection.execute(text("ALTER TABLE images DROP COLUMN popularity"))


@pytest.mark.asyncio(loop_scope="session")
async def test_the_migration_matches_the_model_and_downgrades(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            model_shape = await connection.run_sync(_both)
            assert model_shape[1][0] is not None and "ck_images_popularity" in model_shape[1][1]

            await _drop_what_the_migration_adds(connection)
            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            assert await connection.run_sync(_both) == model_shape

            await connection.run_sync(lambda sync: _run(sync, module.downgrade))
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            assert "popularity_absorptions" not in tables
            assert await connection.run_sync(_popularity_shape) == (None, {})
        finally:
            await transaction.rollback()


@pytest.mark.asyncio(loop_scope="session")
async def test_existing_images_read_one_after_the_migration(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await _drop_what_the_migration_adds(connection)
            await connection.execute(text("INSERT INTO images (id, filename) VALUES (gen_random_uuid(), 'x.jpg')"))
            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            values = (await connection.execute(text("SELECT popularity FROM images"))).scalars().all()
            assert values == [1]
        finally:
            await transaction.rollback()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("drift", [
    "DROP INDEX uq_popularity_open_loser",
    "ALTER TABLE popularity_absorptions DROP CONSTRAINT ck_popularity_absorptions_weight",
])
async def test_the_shape_guard_catches_drift(db_engine, drift):
    """Negative control: a deliberately altered schema must NOT compare equal to the model-created one."""
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            before = await connection.run_sync(_both)
            await connection.execute(text(drift))
            assert await connection.run_sync(_both) != before
        finally:
            await transaction.rollback()


def test_the_migration_continues_the_chain():
    module = _load()
    assert module.revision == "a4f7c1e92b68" and module.down_revision == "d7a3b9e2c451"
