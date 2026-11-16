"""
Two-database fixture for the dev/prod data-sync tools (epic task T0).

README
------
Choice: TWO SCHEMAS (`sync_t_dev`, `sync_t_prod`) inside the existing `ocrdb_test` database, selected per
connection with `options=-csearch_path=<schema>,public`. The extensions (`vector`, `pg_trgm`) stay in `public`
(already there locally and in CI), so both schemas see the types and operators through the fallback to `public`.

Why not the alternatives:
  * Two databases: role `ocr` does have CREATEDB (it is a superuser), but `ocrdb_test` lives on the same server
    as the owner's live `ocrdb`; this project forbids CREATE/DROP DATABASE there. Not used.
  * Throw-away docker Postgres: local only, CI would need an extra container. Not needed.

Schema source: `Base.metadata.create_all` (the ORM models, incl. HNSW/GIN/partial indexes and check constraints)
followed by an alembic STAMP to the real head, so `alembic_version` holds the true head revision in each schema.
Why not `alembic upgrade head` here: `baseline.sql` hardcodes `public.` in 24 places, so replaying the chain would
write into the shared `public` schema of `ocrdb_test`. (Correction, P5d-T8: the chain itself DOES run from an empty
database on PG 18 and matches create_all in tables and columns, see tests/integration/sync/test_release_db.py, which
runs it in a throwaway container; an earlier note here said it was not runnable.) Limit: the schema is the models' view of head, not the result of replaying the migrations;
migration-vs-model drift is covered by the existing *_migration.py tests, not here.

Run time: printed by the fixture (use `pytest -s`); see the hand-off for the measured figure. Per-test cost
after that is milliseconds (reset() is one TRUNCATE per side).

CI: `.github/workflows/integration-tests.yml` already runs `pytest tests/integration/`, which collects this
directory; the service container is pgvector/pgvector:pg16 with db `ocrdb_test`, user/password `ocr`, and the
workflow pre-creates both extensions in public. No workflow change is needed. The fixture skips (not errors)
unless DATABASE_URL points at a database named `ocrdb_test`.

Safety: only schemas named `sync_t_*` are created and dropped (DROP SCHEMA ... CASCADE cannot touch `public`
or the extensions). A leftover pair from a crashed run is dropped at the start of the next session.
Scope is `package` (not session) on purpose: while the schemas exist, any other test that reads the catalog
without a schema filter (e.g. test_comics_migration.py's `pg_constraint WHERE conname = ...`) sees duplicate rows
and fails (observed). With package scope the pair lives only while tests/integration/sync runs, so a serial run
never overlaps; a concurrent run of other integration tests in the same database during those few seconds can
still be affected, so do not run two pytest processes against ocrdb_test at once while these tests run.
Note for code under test: connect with `dev_url`/`prod_url` (libpq URL with `options`) or
`async_url` + `dev_connect_args`/`prod_connect_args` (asyncpg server_settings), never the plain
DATABASE_URL, or it will see `public`.
"""
import os
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import psycopg2
import pytest

ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_SRC = ROOT / "Storage" / "alembic"
REQUIRED_DB = "ocrdb_test"
PREFIX = "sync_t_"

def sync_dsn_parts():
    """(parsed URL of the test database, None) or (None, reason to skip)."""
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        return None, "DATABASE_URL is not set"
    parts = urlsplit(raw.replace("postgresql+asyncpg", "postgresql", 1))
    if parts.path.lstrip("/") != REQUIRED_DB:
        return None, f"DATABASE_URL must point at the database '{REQUIRED_DB}' (got '{parts.path.lstrip('/')}')"
    return parts, None


def _admin_connect(parts):
    conn = psycopg2.connect(host=parts.hostname, port=parts.port or 5432, user=parts.username,
                            password=parts.password, dbname=REQUIRED_DB)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("select current_database()")
        if cur.fetchone()[0] != REQUIRED_DB:  # verified before any DDL
            conn.close()
            raise RuntimeError("refusing DDL: not connected to ocrdb_test")
    return conn


class SyncPair:
    def __init__(self, parts, prefix=PREFIX):
        self._parts = parts
        self.dev_schema = f"{prefix}dev"
        self.prod_schema = f"{prefix}prod"
        self.alembic_head = None
        self.public_before = None

    # -- urls -----------------------------------------------------------------
    def _url(self, schema, driver):
        p = self._parts
        auth = f"{p.username}:{quote(p.password or '', safe='')}"
        base = f"postgresql{driver}://{auth}@{p.hostname}:{p.port or 5432}/{REQUIRED_DB}"
        return f"{base}?options=-csearch_path%3D{schema},public"

    @property
    def dev_url(self):
        return self._url(self.dev_schema, "")

    @property
    def prod_url(self):
        return self._url(self.prod_schema, "")

    @property
    def async_url(self):
        """SQLAlchemy asyncpg URL of the test database; asyncpg cannot take the schema from the URL, so pass
        `create_async_engine(pair.async_url, connect_args=pair.dev_connect_args)` (or prod_connect_args)."""
        p = self._parts
        return (f"postgresql+asyncpg://{p.username}:{quote(p.password or '', safe='')}@{p.hostname}:"
                f"{p.port or 5432}/{REQUIRED_DB}")

    @property
    def dev_connect_args(self):
        return {"server_settings": {"search_path": f"{self.dev_schema},public"}}

    @property
    def prod_connect_args(self):
        return {"server_settings": {"search_path": f"{self.prod_schema},public"}}

    def _conn(self, schema):
        p = self._parts
        return psycopg2.connect(host=p.hostname, port=p.port or 5432, user=p.username, password=p.password,
                                dbname=REQUIRED_DB, options=f"-csearch_path={schema},public")

    def dev_conn(self):
        return self._conn(self.dev_schema)

    def prod_conn(self):
        return self._conn(self.prod_schema)

    # -- lifecycle ------------------------------------------------------------
    def _schemas(self):
        return (self.dev_schema, self.prod_schema)

    def public_snapshot(self):
        """Tables of `public` and the public alembic_version rows, to prove the fixture never touched them."""
        admin = _admin_connect(self._parts)
        try:
            with admin.cursor() as cur:
                cur.execute("select tablename from pg_tables where schemaname = 'public' order by 1")
                tables = [r[0] for r in cur.fetchall()]
                versions = None
                if "alembic_version" in tables:
                    cur.execute("select version_num from public.alembic_version order by 1")
                    versions = cur.fetchall()
                return tables, versions
        finally:
            admin.close()

    def create(self):
        self.public_before = self.public_snapshot()
        admin = _admin_connect(self._parts)
        try:
            with admin.cursor() as cur:
                for s in self._schemas():
                    assert s.startswith(PREFIX)
                    cur.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')
                    cur.execute(f'CREATE SCHEMA "{s}"')
        finally:
            admin.close()
        for s in self._schemas():
            self._build(s)
        conn = self.dev_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("select version_num from alembic_version")
                self.alembic_head = cur.fetchone()[0]
        finally:
            conn.close()

    def _build(self, schema):
        from alembic.config import Config
        from alembic.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from sqlalchemy import create_engine, pool

        from Storage.models import Base

        cfg = Config()
        cfg.set_main_option("script_location", str(ALEMBIC_SRC))
        script = ScriptDirectory.from_config(cfg)
        engine = create_engine(self._url(schema, "").replace("postgresql://", "postgresql+psycopg2://"),
                               poolclass=pool.NullPool)
        try:
            with engine.begin() as conn:
                assert conn.exec_driver_sql("select current_schema()").scalar() == schema
                Base.metadata.create_all(conn, checkfirst=False)  # True would skip tables visible in public
                # Create the version table in OUR schema first: alembic's has_table() would otherwise see
                # public.alembic_version through the search_path and stamp THAT one (found the hard way).
                conn.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
                MigrationContext.configure(conn).stamp(script, "head")
        finally:
            engine.dispose()

    def drop(self):
        admin = _admin_connect(self._parts)
        try:
            with admin.cursor() as cur:
                for s in self._schemas():
                    assert s.startswith(PREFIX)
                    cur.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')
        finally:
            admin.close()

    def reset(self):
        """Empty every table except alembic_version in both schemas (identity/serial sequences restart)."""
        for schema in self._schemas():
            conn = self._conn(schema)
            try:
                with conn.cursor() as cur:
                    cur.execute("select tablename from pg_tables where schemaname = %s "
                                "and tablename <> 'alembic_version'", (schema,))
                    names = [f'"{schema}"."{r[0]}"' for r in cur.fetchall()]
                    if names:
                        cur.execute("TRUNCATE " + ", ".join(names) + " RESTART IDENTITY CASCADE")
                conn.commit()
            finally:
                conn.close()


@pytest.fixture(scope="package")
def sync_pair():
    parts, reason = sync_dsn_parts()
    if parts is None:
        pytest.skip(f"sync_pair needs the test database: {reason}")
    pair = SyncPair(parts)
    started = time.perf_counter()
    try:
        pair.create()
        print(f"\n[sync_pair] two schemas migrated to {pair.alembic_head} in {time.perf_counter() - started:.1f}s")
        yield pair
    finally:
        pair.drop()
