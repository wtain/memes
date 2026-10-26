"""The image_cuts migration creates exactly what the ORM declares, and downgrades cleanly."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

MIGRATION = (Path(__file__).resolve().parents[2] / "Storage" / "alembic" / "versions"
             / "b8d4f1a6c935_add_image_cuts_and_stale_columns.py")
R3A_MIGRATION = (Path(__file__).resolve().parents[2] / "Storage" / "alembic" / "versions"
                 / "d7a3b9e2c451_add_image_cuts_failure_reason.py")
STALE_TABLES = ("ocr_texts", "ocr_lemmas", "image_descriptions", "tags")


def _load():
    spec = importlib.util.spec_from_file_location("migration_b8d4f1a6c935", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_r3a():
    spec = importlib.util.spec_from_file_location("migration_d7a3b9e2c451", R3A_MIGRATION)
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


def _cut_shape(connection):
    inspector = inspect(connection)
    columns = {c["name"]: (str(c["type"]), c["nullable"], _norm(c["default"]))
               for c in inspector.get_columns("image_cuts")}
    checks = {c["name"]: _norm(c["sqltext"]) for c in inspector.get_check_constraints("image_cuts")}
    indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"]),
                           _norm(i.get("dialect_options", {}).get("postgresql_where")))
               for i in inspector.get_indexes("image_cuts")}
    primary = tuple(inspector.get_pk_constraint("image_cuts")["constrained_columns"])
    foreign = {tuple(f["constrained_columns"]): (f["referred_table"], f["options"].get("ondelete"))
               for f in inspector.get_foreign_keys("image_cuts")}
    return columns, checks, indexes, primary, foreign


def _stale_shape(connection):
    inspector = inspect(connection)
    shape = {}
    for table in STALE_TABLES:
        column = next((c for c in inspector.get_columns(table) if c["name"] == "stale"), None)
        shape[table] = None if column is None else (str(column["type"]), column["nullable"], _norm(column["default"]))
    return shape


def _both(connection):
    return _cut_shape(connection), _stale_shape(connection)


async def _drop_what_the_migration_adds(connection):
    await connection.execute(text("DROP TABLE image_cuts"))
    for table in STALE_TABLES:
        await connection.execute(text(f"ALTER TABLE {table} DROP COLUMN stale"))


@pytest.mark.asyncio(loop_scope="session")
async def test_the_migration_matches_the_model_and_downgrades(db_engine):
    module, r3a = _load(), _load_r3a()  # image_cuts is created by b8d4f1a6c935, then extended by d7a3b9e2c451
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            model_shape = await connection.run_sync(_both)
            assert all(model_shape[1].values()), "the model must put stale on all four tables"

            await _drop_what_the_migration_adds(connection)
            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            await connection.run_sync(lambda sync: _run(sync, r3a.upgrade))
            assert await connection.run_sync(_both) == model_shape

            await connection.run_sync(lambda sync: _run(sync, r3a.downgrade))
            await connection.run_sync(lambda sync: _run(sync, module.downgrade))
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            assert "image_cuts" not in tables
            assert await connection.run_sync(_stale_shape) == {table: None for table in STALE_TABLES}
        finally:
            await transaction.rollback()


@pytest.mark.asyncio(loop_scope="session")
async def test_existing_rows_read_false_after_the_migration(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await _drop_what_the_migration_adds(connection)
            await connection.execute(text("INSERT INTO images (id, filename) VALUES (gen_random_uuid(), 'x.jpg')"))
            await connection.execute(text(
                "INSERT INTO ocr_texts (id, image_id, text) SELECT gen_random_uuid(), id, 'hello' FROM images"))
            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            flags = (await connection.execute(text("SELECT stale FROM ocr_texts"))).scalars().all()
            assert flags == [False]
        finally:
            await transaction.rollback()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("drift", [
    "DROP INDEX uq_image_cuts_live_source",
    "ALTER TABLE image_cuts DROP CONSTRAINT ck_image_cuts_box",
    "ALTER TABLE tags ALTER COLUMN stale SET DEFAULT true",
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
    assert module.revision == "b8d4f1a6c935" and module.down_revision == "f3a9c27b5d14"


def test_the_failure_reason_migration_follows_the_metrics_one():
    module = _load_r3a()
    assert module.revision == "d7a3b9e2c451" and module.down_revision == "c5e2a9d7b140"
