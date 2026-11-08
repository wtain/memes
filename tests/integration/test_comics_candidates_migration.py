"""The C9 migration: shape of the candidate tables, downgrade/upgrade round trip, single head."""
import importlib.util
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "Storage" / "alembic" / "versions"
REVISION = "a9d3e6b1c847"
MIGRATION = VERSIONS / f"{REVISION}_add_comics_candidates.py"


def _load():
    spec = importlib.util.spec_from_file_location("migration_candidates", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, function):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        function()


def _shape(connection):
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if not {"comics_candidates", "comics_candidate_images"} <= tables:
        return None
    candidates = {c["name"]: c["nullable"] for c in inspector.get_columns("comics_candidates")}
    images = {c["name"]: c["nullable"] for c in inspector.get_columns("comics_candidate_images")}
    indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"]))
               for t in ("comics_candidates", "comics_candidate_images") for i in inspector.get_indexes(t)}
    fks = {(t, tuple(f["constrained_columns"])): (f["referred_table"], f["options"].get("ondelete"))
           for t in ("comics_candidates", "comics_candidate_images") for f in inspector.get_foreign_keys(t)}
    pk = tuple(inspector.get_pk_constraint("comics_candidate_images")["constrained_columns"])
    return candidates, images, indexes, fks, pk


def test_candidates_migration_is_the_single_head():
    config = Config(str(ROOT / "Storage" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "Storage" / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [REVISION]
    assert _load().down_revision == "f3a8c1d6b952"


@pytest.mark.asyncio(loop_scope="session")
async def test_candidates_migration_shape_and_round_trip(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            model_shape = await connection.run_sync(_shape)
            assert model_shape is not None
            candidates, images, indexes, fks, pk = model_shape
            assert set(candidates) == {"id", "key", "state", "detector_version", "max_gap_seconds", "max_distance",
                                       "comics_id", "created_at", "decided_at"}
            assert candidates["comics_id"] is True and candidates["decided_at"] is True
            assert candidates["key"] is False and candidates["state"] is False
            assert set(images) == {"candidate_id", "image_id", "position"}
            assert set(pk) == {"candidate_id", "image_id"}
            assert indexes["uq_comics_candidates_key"] == (("key",), True)
            assert fks[("comics_candidates", ("comics_id",))] == ("comics", "SET NULL")
            assert fks[("comics_candidate_images", ("candidate_id",))] == ("comics_candidates", "CASCADE")
            assert fks[("comics_candidate_images", ("image_id",))] == ("images", "CASCADE")

            await connection.run_sync(lambda sync: _run(sync, module.downgrade))
            assert await connection.run_sync(_shape) is None

            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            migrated = await connection.run_sync(_shape)
            # the migration and the models agree on columns, nullability, keys and foreign keys
            assert migrated[0] == candidates and migrated[1] == images
            assert migrated[3] == fks and migrated[4] == pk
            assert migrated[2]["uq_comics_candidates_key"] == (("key",), True)
        finally:
            await transaction.rollback()
