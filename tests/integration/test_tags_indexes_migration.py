"""ALG-7: indexes on tags for the smart-search tag tiers (exact upper(value), fuzzy %) and the facet filter (key, value)."""
import importlib.util
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "Storage" / "alembic" / "versions"
REVISION = "b7e2d4a9c615"
PARENT = "a9d3e6b1c847"
MIGRATION = VERSIONS / f"{REVISION}_add_tags_search_indexes.py"
NAMES = {"ix_tags_value_upper", "ix_tags_value_trgm", "ix_tags_key_value"}


def _load():
    spec = importlib.util.spec_from_file_location("migration_tags_indexes", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, function):
    with Operations.context(MigrationContext.configure(connection)):
        function()


def _tag_indexes(connection):
    return {i["name"] for i in inspect(connection).get_indexes("tags")} & NAMES


def test_tags_indexes_migration_is_on_the_single_chain():
    config = Config(str(ROOT / "Storage" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "Storage" / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert len(script.get_heads()) == 1
    assert REVISION in {r.revision for r in script.walk_revisions()}
    assert _load().down_revision == PARENT


@pytest.mark.asyncio(loop_scope="session")
async def test_models_declare_the_indexes_and_migration_round_trips(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            assert await connection.run_sync(_tag_indexes) == NAMES  # created from the models
            await connection.run_sync(lambda sync: _run(sync, module.downgrade))
            assert await connection.run_sync(_tag_indexes) == set()
            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            assert await connection.run_sync(_tag_indexes) == NAMES
        finally:
            await transaction.rollback()


@pytest.mark.asyncio(loop_scope="session")
async def test_planner_uses_the_indexes(db_engine):
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text("SET LOCAL enable_seqscan = off"))
            plans = {}
            for name, sql in {
                "upper": "SELECT image_id FROM tags WHERE upper(value) = 'CAT'",
                "trgm": "SELECT image_id FROM tags WHERE value % 'cat'",
                "facet": "SELECT image_id FROM tags WHERE key = 'k' AND value IN ('a','b')",
            }.items():
                rows = (await connection.execute(text("EXPLAIN " + sql))).all()
                plans[name] = "\n".join(r[0] for r in rows)
            assert "ix_tags_value_upper" in plans["upper"], plans["upper"]
            assert "ix_tags_value_trgm" in plans["trgm"], plans["trgm"]
            assert "ix_tags_key_value" in plans["facet"], plans["facet"]
        finally:
            await transaction.rollback()
