"""The process_events migration creates exactly what the ORM declares, and downgrades cleanly."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

MIGRATION = (Path(__file__).resolve().parents[2] / "Storage" / "alembic" / "versions"
             / "c5e2a9d7b140_add_process_events.py")


def _load():
    spec = importlib.util.spec_from_file_location("migration_c5e2a9d7b140", MIGRATION)
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


def _shape(connection):
    inspector = inspect(connection)
    columns = {c["name"]: (str(c["type"]), c["nullable"], _norm(c["default"]))
               for c in inspector.get_columns("process_events")}
    uniques = {u["name"]: tuple(u["column_names"]) for u in inspector.get_unique_constraints("process_events")}
    indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"]))
               for i in inspector.get_indexes("process_events") if not i.get("duplicates_constraint")}
    primary = tuple(inspector.get_pk_constraint("process_events")["constrained_columns"])
    foreign = {tuple(f["constrained_columns"]): (f["referred_table"], f["options"].get("ondelete"))
               for f in inspector.get_foreign_keys("process_events")}
    return columns, uniques, indexes, primary, foreign


@pytest.mark.asyncio(loop_scope="session")
async def test_the_migration_matches_the_model_and_downgrades(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            model_shape = await connection.run_sync(_shape)
            assert model_shape[1] == {"uq_process_events_subject_event": ("process", "event", "subject_id")}
            assert model_shape[4] == {("batch_run_id",): ("batch_runs", "SET NULL")}

            await connection.execute(text("DROP TABLE process_events"))
            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            assert await connection.run_sync(_shape) == model_shape

            await connection.run_sync(lambda sync: _run(sync, module.downgrade))
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            assert "process_events" not in tables
        finally:
            await transaction.rollback()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("drift", [
    "ALTER TABLE process_events DROP CONSTRAINT uq_process_events_subject_event",
    "DROP INDEX ix_process_events_process_run_event",
    "ALTER TABLE process_events ALTER COLUMN occurred_at DROP DEFAULT",
])
async def test_the_shape_guard_catches_drift(db_engine, drift):
    """Negative control: a deliberately altered schema must NOT compare equal to the model-created one."""
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            before = await connection.run_sync(_shape)
            await connection.execute(text(drift))
            assert await connection.run_sync(_shape) != before
        finally:
            await transaction.rollback()


def test_the_migration_continues_the_chain():
    module = _load()
    assert module.revision == "c5e2a9d7b140" and module.down_revision == "b8d4f1a6c935"
