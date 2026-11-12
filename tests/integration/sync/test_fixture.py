"""Smoke tests for the two-schema `sync_pair` fixture (see conftest.py for the design)."""
import asyncio
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from .conftest import sync_dsn_parts

HERE = Path(__file__).resolve().parent


def _scalar(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()[0]


def test_fixture_rejects_other_databases(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb")
    parts, reason = sync_dsn_parts()
    assert parts is None and "ocrdb_test" in reason
    monkeypatch.delenv("DATABASE_URL")
    assert sync_dsn_parts()[0] is None


def test_both_sides_at_same_alembic_head(sync_pair):
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[3] / "Storage" / "alembic"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    for open_conn in (sync_pair.dev_conn, sync_pair.prod_conn):
        conn = open_conn()
        try:
            assert _scalar(conn, "select version_num from alembic_version") == head
            assert _scalar(conn, "select count(*) from alembic_version") == 1
            from Storage.models import Base
            assert _scalar(conn, "select count(*) from information_schema.tables where table_schema = current_schema()"
                           ) == len(Base.metadata.tables) + 1  # + alembic_version
        finally:
            conn.close()
    assert sync_pair.alembic_head == head


def test_extensions_usable_on_both_sides(sync_pair):
    vec = "[" + ",".join(["0.5"] * 512) + "]"
    for open_conn in (sync_pair.dev_conn, sync_pair.prod_conn):
        conn = open_conn()
        try:
            image_id = str(uuid.uuid4())
            with conn.cursor() as cur:
                cur.execute("insert into images (id, filename) values (%s, 'v.jpg')", (image_id,))
                cur.execute("insert into embeddings (id, image_id, embedding) values (%s, %s, %s::vector)",
                            (str(uuid.uuid4()), image_id, vec))
                cur.execute("select embedding <=> %s::vector from embeddings where image_id = %s", (vec, image_id))
                assert cur.fetchone()[0] == pytest.approx(0.0, abs=1e-6)
                cur.execute("select similarity('hello world', 'hello wor1d') > 0.3, 'hello' % 'hallo'")
                assert cur.fetchone()[0] is True
            conn.rollback()
        finally:
            conn.close()


def test_sides_are_independent_and_reset_empties(sync_pair):
    sync_pair.reset()
    dev, prod = sync_pair.dev_conn(), sync_pair.prod_conn()
    try:
        with dev.cursor() as cur:
            cur.execute("insert into images (id, filename) values (%s, 'only-dev.jpg')", (str(uuid.uuid4()),))
            cur.execute("insert into tmp_clusters (cluster_id) values (1), (2), (3)")
        dev.commit()
        assert _scalar(dev, "select count(*) from images") == 1
        assert _scalar(prod, "select count(*) from images") == 0
        # serial sequence: dev advanced, prod untouched (first value 1)
        assert _scalar(dev, "select max(id) from tmp_clusters") == 3
        with prod.cursor() as cur:
            cur.execute("insert into tmp_clusters (cluster_id) values (9) returning id")
            assert cur.fetchone()[0] == 1
        prod.commit()
        # the version table and indexes are per schema
        assert _scalar(prod, "select count(*) from pg_indexes where schemaname = %s and tablename = 'images'",
                       (sync_pair.prod_schema,)) >= 1
        assert _scalar(dev, "select current_schema()") == sync_pair.dev_schema
        assert _scalar(prod, "select current_schema()") == sync_pair.prod_schema
    finally:
        dev.close()
        prod.close()

    sync_pair.reset()
    dev, prod = sync_pair.dev_conn(), sync_pair.prod_conn()
    try:
        for conn in (dev, prod):
            assert _scalar(conn, "select count(*) from images") == 0
            assert _scalar(conn, "select count(*) from tmp_clusters") == 0
            assert _scalar(conn, "select count(*) from alembic_version") == 1  # kept
        with prod.cursor() as cur:  # identity restarted
            cur.execute("insert into tmp_clusters (cluster_id) values (1) returning id")
            assert cur.fetchone()[0] == 1
        prod.rollback()
    finally:
        dev.close()
        prod.close()
    sync_pair.reset()


def test_async_connect_args_point_at_the_right_schema(sync_pair):
    async def current(url, args):
        engine = create_async_engine(url, connect_args=args)
        try:
            async with engine.connect() as conn:
                return (await conn.execute(text("select current_schema()"))).scalar()
        finally:
            await engine.dispose()

    assert asyncio.run(current(sync_pair.async_url, sync_pair.dev_connect_args)) == sync_pair.dev_schema
    assert asyncio.run(current(sync_pair.async_url, sync_pair.prod_connect_args)) == sync_pair.prod_schema


def test_public_schema_untouched(sync_pair):
    assert sync_pair.public_snapshot() == sync_pair.public_before


def test_teardown_leaves_nothing_behind(sync_pair):
    """Run create + drop with a different prefix in a subprocess and check the catalog afterwards."""
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "import conftest, psycopg2\n"
        "parts, reason = conftest.sync_dsn_parts(); assert parts, reason\n"
        "pair = conftest.SyncPair(parts, prefix='sync_t_chk_')\n"
        "try:\n"
        "    pair.create()\n"
        "finally:\n"
        "    pair.drop()\n"
        "c = psycopg2.connect(host=parts.hostname, port=parts.port, user=parts.username, password=parts.password, dbname='ocrdb_test')\n"
        "cur = c.cursor()\n"
        "cur.execute(\"select count(*) from pg_namespace where nspname like 'sync_t_chk_%%'\"); print('schemas', cur.fetchone()[0])\n"
        "cur.execute(\"select count(*) from pg_class cl join pg_namespace n on n.oid = cl.relnamespace where n.nspname like 'sync_t_chk_%%'\"); print('relations', cur.fetchone()[0])\n"
    ) % str(HERE)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "schemas 0" in out.stdout and "relations 0" in out.stdout
    # and the main pair is still there while its session runs
    conn = sync_pair.dev_conn()
    try:
        assert _scalar(conn, "select count(*) from pg_namespace where nspname like 'sync_t_%'") == 2
    finally:
        conn.close()
