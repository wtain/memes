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


def _norm(text_value):
    """Lower-case and drop whitespace/parentheses so cosmetic renderings of the same SQL compare equal."""
    if text_value is None:
        return None
    return "".join(ch for ch in str(text_value).lower() if not ch.isspace() and ch not in "()")


def _shape(connection):
    inspector = inspect(connection)
    # Server defaults and check expressions come back from the reflected catalog text, which is the same for both
    # creation paths once whitespace/case/parentheses are normalised (_norm hides only that cosmetic difference).
    columns = {c["name"]: (str(c["type"]), c["nullable"], _norm(c["default"]))
               for c in inspector.get_columns("meme_text_ratings")}
    uniques = {u["name"]: tuple(u["column_names"]) for u in inspector.get_unique_constraints("meme_text_ratings")}
    checks = {c["name"]: _norm(c["sqltext"]) for c in inspector.get_check_constraints("meme_text_ratings")}
    indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"])) for i in inspector.get_indexes("meme_text_ratings")}
    primary = tuple(inspector.get_pk_constraint("meme_text_ratings")["constrained_columns"])
    foreign = {tuple(f["constrained_columns"]): (f["referred_table"], f["options"].get("ondelete"))
               for f in inspector.get_foreign_keys("meme_text_ratings")}
    comment = inspector.get_table_comment("meme_text_ratings")["text"]
    return columns, uniques, checks, indexes, primary, foreign, comment


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


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("drift", [
    "ALTER TABLE meme_text_ratings ALTER COLUMN fallback SET DEFAULT 'nope'",
    "ALTER TABLE meme_text_ratings DROP CONSTRAINT ck_meme_text_ratings_rank",
    "ALTER TABLE meme_text_ratings DROP CONSTRAINT ck_meme_text_ratings_grade, "
    "ADD CONSTRAINT ck_meme_text_ratings_grade CHECK (grade IN (0, 1, 2, 3))",
    "DROP INDEX ix_meme_text_ratings_rated_at",
    "COMMENT ON TABLE meme_text_ratings IS 'something else'",
])
async def test_the_shape_guard_catches_drift(db_engine, drift):
    """Negative control: a deliberately altered table must NOT compare equal to the model-created one."""
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            before = await connection.run_sync(_shape)
            await connection.execute(text(drift))
            after = await connection.run_sync(_shape)
            assert after != before
        finally:
            await transaction.rollback()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_table_records_the_normalisation_rule(db_engine):
    async with db_engine.connect() as connection:
        shape = await connection.run_sync(_shape)
    assert "normalised like the parser" in shape[-1]


def test_the_migration_continues_the_chain():
    module = _load()
    assert module.revision == "e6b1c4d8f203" and module.down_revision == "d5a9f3c1e7b2"
