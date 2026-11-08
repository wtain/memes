"""The comics migrations: shape of C1's tables, and the C4 `kept` column (up, down, up; single head)."""
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
KEPT_REVISION = "f3a8c1d6b952"
KEPT_MIGRATION = VERSIONS / f"{KEPT_REVISION}_comics_panels_kept.py"


def _load():
    spec = importlib.util.spec_from_file_location("migration_kept", KEPT_MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, function):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        function()


def _kept(connection):
    column = next((c for c in inspect(connection).get_columns("comics_panels") if c["name"] == "kept"), None)
    if column is None:
        return None
    return str(column["type"]), column["nullable"], str(column["default"]).lower()


def test_kept_migration_chains_after_the_comics_tables():
    config = Config(str(ROOT / "Storage" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "Storage" / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert len(script.get_heads()) == 1  # the head itself is asserted by the newest migration's test
    assert script.get_revision(KEPT_REVISION) is not None
    assert _load().down_revision == "d7a3b9c5e214"


@pytest.mark.asyncio(loop_scope="session")
async def test_kept_migration_up_down_up(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            model_shape = await connection.run_sync(_kept)
            assert model_shape is not None
            assert model_shape[1] is False and "false" in model_shape[2]

            await connection.run_sync(lambda sync: _run(sync, module.downgrade))
            assert await connection.run_sync(_kept) is None

            # a row that exists before the upgrade reads false afterwards
            await connection.execute(text("INSERT INTO comics (id, name, name_key) VALUES "
                                          "('00000000-0000-0000-0000-0000000000c1', 'k', 'k')"))
            await connection.execute(text(
                "INSERT INTO comics_panels (id, comics_id, position, source_image_id, source_filename) VALUES "
                "(gen_random_uuid(), '00000000-0000-0000-0000-0000000000c1', 0, gen_random_uuid(), 'a.jpg')"))
            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            assert await connection.run_sync(_kept) == model_shape
            values = (await connection.execute(text("SELECT kept FROM comics_panels"))).scalars().all()
            assert values == [False]
        finally:
            await transaction.rollback()


def _shape(connection):
    inspector = inspect(connection)
    comics = {c["name"] for c in inspector.get_columns("comics")}
    panels = {c["name"]: c["nullable"] for c in inspector.get_columns("comics_panels")}
    indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"])) for i in inspector.get_indexes("comics_panels")}
    uniques = {u["name"]: tuple(u["column_names"]) for u in inspector.get_unique_constraints("comics_panels")}
    fks = {(t, tuple(f["constrained_columns"])): (f["referred_table"], f["options"].get("ondelete"))
           for t in ("comics", "comics_panels") for f in inspector.get_foreign_keys(t)}
    return comics, panels, indexes, uniques, fks


@pytest.mark.asyncio(loop_scope="session")
async def test_migration_shape_of_d7a3b9c5e214(db_engine):
    async with db_engine.connect() as connection:
        comics, panels, indexes, uniques, fks = await connection.run_sync(_shape)
        assert {"id", "name", "name_key", "state", "built_image_id", "built_content_hash", "created_at",
                "built_at"} <= comics
        assert {"id", "comics_id", "image_id", "position", "source_image_id", "source_filename",
                "source_content_hash"} <= set(panels)
        assert panels["source_image_id"] is False and panels["image_id"] is True
        assert indexes["uq_comics_panels_image_id"] == (("image_id",), True)
        assert uniques["uq_comics_panels_position"] == ("comics_id", "position")
        deferrable = (await connection.execute(text(
            "SELECT condeferrable, condeferred FROM pg_constraint WHERE conname = 'uq_comics_panels_position'"
        ))).one()
        assert tuple(deferrable) == (True, True)
        assert fks[("comics_panels", ("comics_id",))] == ("comics", "CASCADE")
        assert fks[("comics_panels", ("image_id",))] == ("images", "SET NULL")
        assert fks[("comics", ("built_image_id",))] == ("images", "SET NULL")
