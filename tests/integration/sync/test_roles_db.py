"""The generated roles SQL (sync/roles.py, P5d-T1) applied to a real server, and the real H3 role check run against it.

Needs a Postgres SUPERUSER connection (the `ocr` user of the local docker test server is one; CI's service container too). If the
user cannot create roles and databases the whole module is skipped with the reason.

Isolation (roles are CLUSTER-global, so this module is careful):
  * everything is created under the throwaway tag `synctest_<8 hex>`: database `memes_synctest_<hex>` (+ `_other`), roles
    `memes_synctest_<hex>_owner|app|sync_w|sync_r|ro|stranger`; a leftover of a crashed run (same pattern) is swept at the start;
  * the connection to `ocrdb_test` is used only to issue CREATE/DROP DATABASE/ROLE for those names; no object of `ocrdb_test` and
    nothing in its `public` schema or `alembic_version` is ever touched; the generated SQL is applied only inside the scratch databases;
  * everything is dropped in `finally` (DROP DATABASE ... WITH (FORCE), DROP OWNED BY, DROP ROLE) and the teardown asserts that
    nothing with the tag is left in pg_database or pg_roles.
Do not run two pytest processes against ocrdb_test at once.
"""
from __future__ import annotations

import secrets
import uuid

import psycopg2
import pytest
from psycopg2 import errors

from sync import connect, registry
from sync.connect import SyncConnection
from sync.model import Klass
from sync.roles import CONNECTION_LIMITS, make_names, render

from .conftest import sync_dsn_parts

SWEEP_PATTERN = r"memes\_synctest\_%"
PASSWORD = "synctest-" + secrets.token_hex(6)  # test-only credential of throwaway roles on a local test server


class ClosingConnection(psycopg2.extensions.connection):
    """`with conn:` closes it. psycopg2's own context manager neither closes nor leaves an autocommit connection alone (it opens
    a transaction that the close would roll back), so both methods are replaced."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _connect(parts, dbname, user=None, password=None):
    kwargs = dict(host=parts.hostname, port=parts.port or 5432, dbname=dbname, user=user or parts.username,
                  password=password if user else parts.password, connect_timeout=10, connection_factory=ClosingConnection)
    conn = psycopg2.connect(**kwargs)
    conn.autocommit = True
    return conn


def _all(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall() if cur.description else []


def _one(conn, sql, args=None):
    rows = _all(conn, sql, args)
    return rows[0][0] if rows else None


def _acl_snapshot(conn):
    """Every ACL and default ACL of schema public, the schema and the database, as sorted text."""
    return sorted(str(r) for r in _all(conn, """
        select 'rel', c.relname, c.relkind::text, coalesce(c.relacl::text, '') from pg_class c
            where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'S', 'p', 'v')
        union all select 'col', c.relname, a.attname, a.attacl::text from pg_attribute a join pg_class c on c.oid = a.attrelid
            where c.relnamespace = 'public'::regnamespace and a.attacl is not null
        union all select 'schema', nspname, '', coalesce(nspacl::text, '') from pg_namespace where nspname = 'public'
        union all select 'db', datname, '', coalesce(datacl::text, '') from pg_database where datname = current_database()
        union all select 'default', defaclobjtype::text, defaclnamespace::text, defaclacl::text from pg_default_acl
        union all select 'member', roleid::regrole::text, member::regrole::text, '' from pg_auth_members
            where member::regrole::text like 'memes\\_synctest\\_%'
    """))


class RoleDb:
    def __init__(self, parts):
        self.parts = parts
        self.tag = "synctest_" + secrets.token_hex(4)
        self.names = make_names(self.tag)
        self.db = self.names.database
        self.other_db = self.db + "_other"
        self.stranger = f"memes_{self.tag}_stranger"
        self.admin = None
        self.sql = render(self.names)
        self.version = 0

    # -- plumbing ---------------------------------------------------------------------------------------------
    def superuser(self, dbname=None):
        return _connect(self.parts, dbname or self.db)

    def login(self, role, dbname=None):
        return _connect(self.parts, dbname or self.db, user=role, password=PASSWORD)

    def apply(self, conn):
        try:
            with conn.cursor() as cur:
                cur.execute(self.sql)
        except psycopg2.Error:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK")  # the file opens its own transaction; an error leaves it aborted
            raise

    def sync_connection(self, role, kind):
        return SyncConnection(self.login(role), kind)

    # -- lifecycle --------------------------------------------------------------------------------------------
    def sweep(self):
        for (name,) in _all(self.admin, "select datname from pg_database where datname like %s", (SWEEP_PATTERN,)):
            self.admin.cursor().execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        for (name,) in _all(self.admin, "select rolname from pg_roles where rolname like %s", (SWEEP_PATTERN,)):
            self.admin.cursor().execute(f'DROP ROLE IF EXISTS "{name}"')

    def create(self):
        self.admin = _connect(self.parts, "ocrdb_test")
        self.version = int(_one(self.admin, "show server_version_num"))
        self.sweep()
        for db in (self.db, self.other_db):
            self.admin.cursor().execute(f'CREATE DATABASE "{db}"')
        for db in (self.db, self.other_db):
            with self.superuser(db) as su:
                su.cursor().execute("CREATE EXTENSION IF NOT EXISTS vector")
                su.cursor().execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        with self.superuser() as su:
            self.apply(su)  # empty database: creates the roles, owner of the database, skips the table grants with a NOTICE
            for role in self.names.roles:
                su.cursor().execute(f'ALTER ROLE "{role}" PASSWORD %s', (PASSWORD,))
            su.cursor().execute(f'CREATE ROLE "{self.stranger}" LOGIN PASSWORD %s', (PASSWORD,))
        self.build_schema()
        with self.superuser() as su:
            self.apply(su)  # the real run: grants and verification

    def build_schema(self):
        from sqlalchemy import create_engine, pool

        from Storage.models import Base

        engine = create_engine("postgresql+psycopg2://", creator=lambda: self.login(self.names.owner), poolclass=pool.NullPool)
        try:
            with engine.begin() as conn:
                assert conn.exec_driver_sql("select current_user").scalar() == self.names.owner
                Base.metadata.create_all(conn, checkfirst=False)
                conn.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
                conn.exec_driver_sql("INSERT INTO alembic_version VALUES ('synctest')")
        finally:
            engine.dispose()

    def drop(self):
        problems = []
        try:
            if self.admin is None:
                return
            roles = [r[0] for r in _all(self.admin, "select rolname from pg_roles where rolname like %s", (f"memes\\_{self.tag}\\_%",))]
            for db in (self.db, self.other_db):
                self.admin.cursor().execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
            for role in roles:
                self.admin.cursor().execute(f'DROP OWNED BY "{role}"')  # nothing is left in ocrdb_test; kept as the documented cleanup
                self.admin.cursor().execute(f'DROP ROLE IF EXISTS "{role}"')
            left_db = _all(self.admin, "select datname from pg_database where datname like %s", (f"memes\\_{self.tag}%",))
            left_roles = _all(self.admin, "select rolname from pg_roles where rolname like %s", (f"memes\\_{self.tag}\\_%",))
            if left_db or left_roles:
                problems.append((left_db, left_roles))
        finally:
            if self.admin is not None:
                self.admin.close()
        assert not problems, f"scratch objects left behind: {problems}"


@pytest.fixture(scope="module")
def role_db():
    parts, reason = sync_dsn_parts()
    if parts is None:
        pytest.skip(f"role test needs the test database: {reason}")
    probe = _connect(parts, "ocrdb_test")
    try:
        sup = _one(probe, "select rolsuper from pg_roles where rolname = current_user")
    finally:
        probe.close()
    if not sup:
        pytest.skip("role test needs a superuser connection (CREATE ROLE, CREATE DATABASE, CREATE EXTENSION vector); the test user is not one")
    rdb = RoleDb(parts)
    try:
        rdb.create()
        yield rdb
    finally:
        rdb.drop()


def _denied(conn, sql, args=None):
    with pytest.raises(errors.InsufficientPrivilege):
        _all(conn, sql, args)


# ------------------------------------------------------------------------------------------------------- H3, for real

def test_the_real_h3_role_check_passes_for_the_writer_and_the_reader(role_db):
    for role, kind, expected_kind in ((role_db.names.sync_w, connect.PROD_WRITER, "writer"),
                                      (role_db.names.sync_r, connect.PROD_READER, "reader")):
        with role_db.sync_connection(role, kind) as conn:
            facts = connect.gather_role_facts(conn, role_db.tag)
            assert facts["user"] == role
            assert facts["member_of"] == [] and facts["owns_images"] is False
            assert connect.evaluate_role(facts, expected_kind, role) == []
            assert facts["table_priv"]["alembic_version"]["exists"] is True


def test_the_h3_check_is_not_vacuous_it_rejects_the_other_role_and_a_drifted_one(role_db):
    with role_db.sync_connection(role_db.names.sync_w, connect.PROD_WRITER) as conn:
        facts = connect.gather_role_facts(conn, role_db.tag)
        assert connect.evaluate_role(facts, "reader", role_db.names.sync_r)  # the writer is not a valid reader
    with role_db.sync_connection(role_db.names.app, connect.PROD_WRITER) as conn:
        facts = connect.gather_role_facts(conn, role_db.tag)
        problems = connect.evaluate_role(facts, "writer", role_db.names.sync_w)
        assert "wrong_current_user" in problems and any(p.startswith("write_on_class_p_or_x") for p in problems)


# ------------------------------------------------------------------------------------------------ the writer (sync_w)

def test_the_writer_cannot_write_class_p_or_read_rating_text_but_can_read_the_three_columns(role_db):
    with role_db.login(role_db.names.sync_w) as w:
        for table in registry.names(Klass.P):
            _denied(w, f'delete from "{table}"')
            if table != "meme_text_ratings":  # count(*) needs SELECT on just one column, which the writer has on this table only
                _denied(w, f'select count(*) from "{table}"')
        _denied(w, "insert into meme_saves (id, image_id) values (%s, %s)", (str(uuid.uuid4()), str(uuid.uuid4())))
        _denied(w, "select query_text from meme_text_ratings")
        _denied(w, "select * from meme_text_ratings")
        assert _all(w, "select image_id, query_hash, updated_at from meme_text_ratings") == []
        for table in registry.names(Klass.X):
            _denied(w, f'select 1 from "{table}" limit 1')
        _denied(w, "create temp table scratch (a int)")
        _denied(w, "create table scratch (a int)")
        _denied(w, "truncate images")
        _denied(w, "insert into alembic_version values ('x')")
        assert _all(w, "select version_num from alembic_version") == [("synctest",)]


def test_the_writer_loads_class_d_moves_the_sequences_and_deletes_only_replaced_per_image_tables(role_db):
    image = str(uuid.uuid4())
    with role_db.login(role_db.names.sync_w) as w, role_db.login(role_db.names.app) as app:
        _all(w, "insert into images (id, filename) values (%s, %s)", (image, "a.jpg"))
        _all(w, "insert into image_extras (image_id) values (%s)", (image,))
        _all(w, "insert into concepts (id, name) values (7, 'c')")
        assert _one(w, "select setval(pg_get_serial_sequence('concepts', 'id'), 7)") == 7
        _all(app, "insert into meme_text_ratings (id, query_hash, image_id, grade, rank, reading, parser_version, taxonomy_version, language) "
                  "values (%s, %s, %s, 2, 1, 'parsed', 'v', 1, 'en')", (str(uuid.uuid4()), "h" * 64, image))
        _denied(w, "delete from meme_text_ratings")
        # review finding H1: DELETE only on the tables the promote replaces per image; never images, an upsert table or a global set
        for table in sorted(set(registry.names(Klass.D)) - set(registry.prod_delete_set("sync_writer"))):
            _denied(w, f'delete from "{table}"')
        _all(w, "delete from image_extras where image_id = %s", (image,))
        assert _one(w, "select count(*) from image_extras where image_id = %s", (image,)) == 0
        assert _one(app, "select count(*) from meme_text_ratings where image_id = %s", (image,)) == 1  # the image and its ratings stay


def test_the_writer_can_reindex_and_vacuum_class_d_tables_with_maintain(role_db):
    if role_db.version < 170000:
        pytest.skip("MAINTAIN needs PostgreSQL 17 or later (server is older; the generated SQL skips the grant there)")
    with role_db.login(role_db.names.sync_w) as w:
        for sql in ("reindex table embeddings", "reindex table images", "vacuum images", "vacuum (analyze) tags",
                    "reindex table concurrently embeddings", "analyze images"):
            _all(w, sql)
        _denied(w, "reindex table search_history")  # class P: no MAINTAIN
        w.notices.clear()
        _all(w, "vacuum meme_saves")  # not an error: PostgreSQL skips a table the role may not maintain and warns
        assert any("skipping" in note and "meme_saves" in note for note in w.notices), w.notices
        _denied(w, "drop index ix_meme_impressions_image_id_shown_at")  # no DDL: dropping an index needs ownership


# --------------------------------------------------------------------------------------------------- the app (backend)

def test_the_app_reads_class_d_writes_exactly_class_p_and_touches_nothing_else(role_db):
    app_names = role_db.names
    with role_db.login(app_names.app) as app:
        for table in registry.names(Klass.D):
            _all(app, f'select 1 from "{table}" limit 1')
            _denied(app, f'delete from "{table}"')
        _denied(app, "insert into images (id, filename) values (%s, 'x')", (str(uuid.uuid4()),))
        _denied(app, "update concepts set name = 'x'")
        for table in registry.names(Klass.P):
            _all(app, f'select 1 from "{table}" limit 1')
        _all(app, "insert into meme_saves (id, image_id) values (%s, %s)", (str(uuid.uuid4()), str(uuid.uuid4())))
        _all(app, "insert into meme_impressions (image_id, rank, surface) values (%s, 1, 'search')", (str(uuid.uuid4()),))
        assert _one(app, "select count(*) from meme_saves") == 1
        _all(app, "delete from meme_saves")
        _all(app, "delete from meme_impressions")
        for table in registry.names(Klass.X):
            _denied(app, f'select 1 from "{table}" limit 1')
            _denied(app, f'insert into "{table}" default values')
        _denied(app, "select version_num from alembic_version")
        _denied(app, "create table scratch (a int)")
        _denied(app, "create temp table scratch (a int)")
        _denied(app, "truncate meme_saves")
        assert _one(app, "show statement_timeout") == "10s"
        assert _one(app, "show lock_timeout") == "3s"
        assert _one(app, "show idle_in_transaction_session_timeout") == "30s"


def test_the_reader_reads_class_p_and_image_ids_only(role_db):
    with role_db.login(role_db.names.sync_r) as r:
        for table in registry.names(Klass.P):
            _all(r, f'select count(*) from "{table}"')
            _denied(r, f'delete from "{table}"')
        assert all(len(row) == 1 for row in _all(r, "select id from images"))  # ids are readable (rows may exist from earlier tests)
        _denied(r, "select filename from images")
        _denied(r, "select * from images")
        _denied(r, "insert into images (id, filename) values (%s, 'x')", (str(uuid.uuid4()),))
        _denied(r, "select 1 from concepts limit 1")
        _denied(r, "select 1 from batch_runs limit 1")
        _denied(r, "create temp table scratch (a int)")
        assert _all(r, "select version_num from alembic_version") == [("synctest",)]


def test_the_read_only_role_reads_everything_except_the_three_text_tables(role_db):
    with role_db.login(role_db.names.ro) as ro:
        for table in registry.names():
            if table in ("search_history", "search_history_tags", "meme_text_ratings"):
                _denied(ro, f'select 1 from "{table}" limit 1')
            else:
                _all(ro, f'select 1 from "{table}" limit 1')
        _all(ro, "select version_num from alembic_version")
        _denied(ro, "delete from images")
        _denied(ro, "create temp table scratch (a int)")


# --------------------------------------------------------------------------------------------- database-level contract

def test_attributes_connection_limits_role_settings_and_the_database_owner(role_db):
    with role_db.superuser() as su:
        rows = {r[0]: r[1:] for r in _all(su, "select rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, "
                                              "rolcanlogin, rolconnlimit, rolpassword is null from pg_roles where rolname like %s",
                                          (f"memes\\_{role_db.tag}\\_%",))}
        n = role_db.names
        for kind, name in n.kinds().items():
            sup, createdb, createrole, repl, bypass, login, limit, _ = rows[name]
            assert (sup, createdb, createrole, repl, bypass) == (False,) * 5, name
            assert login is True and limit == CONNECTION_LIMITS[kind], name
        assert _one(su, "select pg_get_userbyid(datdba) from pg_database where datname = %s", (role_db.db,)) == n.owner
        assert not _all(su, "select 1 from pg_database d, aclexplode(d.datacl) a where d.datname = %s and a.grantee = 0", (role_db.db,))
        assert not _all(su, "select 1 from pg_auth_members where member::regrole::text like %s", (f"memes\\_{role_db.tag}\\_%",))
    # the stranger (any other login) cannot even connect, and the roles have no TEMP/CREATE on the database
    with pytest.raises(psycopg2.OperationalError):
        role_db.login(role_db.stranger).close()
    with role_db.superuser() as su:
        for name in (n.app, n.sync_w, n.sync_r, n.ro):
            assert _one(su, "select has_database_privilege(%s, %s, 'CONNECT')", (name, role_db.db)) is True
            assert _one(su, "select has_database_privilege(%s, %s, 'TEMPORARY')", (name, role_db.db)) is False
            assert _one(su, "select has_schema_privilege(%s, 'public', 'CREATE')", (name,)) is False


def test_the_file_refuses_another_database_and_rolls_back(role_db):
    with role_db.superuser(role_db.other_db) as su:
        with pytest.raises(psycopg2.Error, match="applied to database"):
            role_db.apply(su)
        assert _one(su, "select count(*) from pg_roles where rolname = %s", (role_db.names.app,)) == 1  # roles are global and existed


def test_reapplying_is_a_no_op_and_removes_drift(role_db):
    with role_db.superuser() as su:
        before = _acl_snapshot(su)
        role_db.apply(su)
        assert _acl_snapshot(su) == before  # idempotent: a second apply changes nothing

        n = role_db.names
        # drift an owner can repair: table, column and sequence grants, schema CREATE, default privileges
        for sql in ('grant delete on concepts to "{app}"', 'grant all on meme_saves to "{w}"', 'grant select on images to "{r}"',
                    'grant select (filename) on images to "{r}"', 'grant update (name) on concepts to "{ro}"',
                    'grant create on schema public to "{app}"', 'grant usage on all sequences in schema public to "{ro}"',
                    'alter default privileges for role "{owner}" in schema public grant select on tables to "{app}"',
                    'alter default privileges for role "{owner}" grant all on sequences to "{w}"'):
            su.cursor().execute(sql.format(app=n.app, w=n.sync_w, r=n.sync_r, ro=n.ro, owner=n.owner))
        assert _acl_snapshot(su) != before
    with role_db.login(n.owner) as owner:  # the release job: connects as the owner, not a superuser
        assert _one(owner, "select rolsuper from pg_roles where rolname = current_user") is False
        role_db.apply(owner)
    with role_db.superuser() as su:
        assert _acl_snapshot(su) == before
    with role_db.sync_connection(n.sync_w, connect.PROD_WRITER) as conn:
        assert connect.evaluate_role(connect.gather_role_facts(conn, role_db.tag), "writer", n.sync_w) == []


def test_a_privilege_the_file_cannot_remove_makes_the_verification_fail_and_the_whole_file_roll_back(role_db):
    n = role_db.names
    with role_db.superuser() as su:
        before = _acl_snapshot(su)
        su.cursor().execute("grant select on concepts to public")  # PUBLIC is not one of the five roles: revoke from them cannot remove it
        drifted = _acl_snapshot(su)
        with pytest.raises(psycopg2.Error, match="differ from the registry"):
            role_db.apply(su)
        assert _acl_snapshot(su) == drifted  # rolled back: the failed apply changed nothing
        su.cursor().execute("revoke select on concepts from public")
        role_db.apply(su)
        assert _acl_snapshot(su) == before


def test_role_drift_only_a_superuser_can_fix_is_detected_for_the_owner_and_fixed_by_the_superuser(role_db):
    n = role_db.names
    with role_db.superuser() as su:
        su.cursor().execute(f'grant "{n.sync_r}" to "{n.app}"')
        su.cursor().execute(f'alter role "{n.ro}" connection limit 50 createdb')
    with role_db.login(n.owner) as owner:
        with pytest.raises(psycopg2.Error, match="role (attributes|memberships)"):
            role_db.apply(owner)
    with role_db.superuser() as su:
        role_db.apply(su)
        assert _one(su, "select rolconnlimit from pg_roles where rolname = %s", (n.ro,)) == CONNECTION_LIMITS["ro"]
        assert _one(su, "select rolcreatedb from pg_roles where rolname = %s", (n.ro,)) is False
        assert _one(su, "select count(*) from pg_auth_members where member::regrole::text = %s", (n.app,)) == 0


def test_the_owner_can_set_the_database_markers_and_the_other_roles_cannot(role_db):
    """P5d-T2 finding: without the SET privilege on the parameter a database owner gets `permission denied to set parameter` (PG 15+)."""
    n = role_db.names
    if role_db.version < 150000:
        pytest.skip("GRANT ... ON PARAMETER needs PostgreSQL 15 or later")
    with role_db.login(n.owner) as owner:
        for name, value in (("memes.environment", "general"), ("memes.state", "seeding"), ("memes.revision", "abc123")):
            owner.cursor().execute(f'alter database "{role_db.db}" set {name} = \'{value}\'')
    for kind in (n.sync_w, n.sync_r, n.app, n.ro):
        with role_db.login(kind) as conn:
            assert _one(conn, "select current_setting('memes.state')") == "seeding"  # any role reads them
    with role_db.superuser() as su:
        su.cursor().execute(f'grant set on parameter memes.state to "{n.app}"')  # drift only a superuser can remove
    with role_db.login(n.owner) as owner:
        with pytest.raises(psycopg2.Error, match="SET privilege on the database markers"):
            role_db.apply(owner)
    with role_db.superuser() as su:
        role_db.apply(su)
        assert _one(su, "select has_parameter_privilege(%s, 'memes.state', 'SET')", (n.app,)) is False
        assert _one(su, "select has_parameter_privilege(%s, 'memes.state', 'SET')", (n.owner,)) is True
        su.cursor().execute(f'alter database "{role_db.db}" reset all')
