"""Generator of the prod roles and grants SQL (epic environment-data-sync, project P5d-T1).

    python -m sync.roles --env metal            # write deploy/prod/sql/roles.metal.sql
    python -m sync.roles --env metal --check    # exit 1 when the committed file differs from a regeneration
    python -m sync.roles --env metal --stdout   # print it

The contract is interfaces.md section h; the table lists come from sync/registry.py (class D / P / X, `prod_write_set`,
`setval_tables`), so a new table is classified once and the grants follow. The output is plain SQL (no psql meta commands, so a
test can run it through any driver), deterministic (no timestamp), contains no password, and is built to be **re-applied at every
release**: it first revokes everything the five roles hold in schema public and then grants exactly the list, and ends with a
verification block that raises when the effective privileges differ from the registry (an extra grant, a PUBLIC grant, a role
attribute, a membership, a default privilege). The whole file is one transaction: a failure leaves the database as it was.

Two ways to run it (the file works for both, nothing to configure):
  * as the bootstrap superuser (provision.sh, once, then at releases if wanted): also creates missing roles, sets attributes,
    connection limits, role settings, removes role memberships and makes `<owner>` the database owner;
  * as `<owner>` (the migrate job, release step 2b): the role part is skipped with a NOTICE (an owner cannot alter roles) and
    the grants and the verification run.
On a database with no schema yet (no `alembic_version`) the table grants are skipped with a NOTICE: apply the file again after the
first migration.

Standard library plus sync.registry only.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from sync import registry
from sync.model import Klass

GENERATOR_VERSION = 1
ENVS = ("metal", "general", "it")
SQL_DIR = Path(__file__).resolve().parents[1] / "deploy" / "prod" / "sql"

# interfaces.md h: connection limits per role kind (owner 3, app 24, sync_w 3, sync_r 3, ro 2).
CONNECTION_LIMITS = {"owner": 3, "app": 24, "sync_w": 3, "sync_r": 3, "ro": 2}
# P5 design, "Pools": role settings of the backend role.
APP_SETTINGS = (("statement_timeout", "10s"), ("idle_in_transaction_session_timeout", "30s"), ("lock_timeout", "3s"))
# Tables `ro` must not read (interfaces.md h).
RO_EXCLUDED = ("search_history", "search_history_tags", "meme_text_ratings")
# The single exception to "sync_w has no privilege on any class P table" (interfaces.md h).
WRITER_RATINGS_COLUMNS = ("image_id", "query_hash", "updated_at")
READER_IMAGES_COLUMNS = ("id",)
SCHEMA = "public"
MAINTAIN_MIN_VERSION_NUM = 170000  # PostgreSQL 17: the MAINTAIN privilege exists from there on
PARAMETER_ACL_MIN_VERSION_NUM = 150000  # PostgreSQL 15: GRANT ... ON PARAMETER
# Database settings of interfaces.md e that the owner role sets (verified in a PG 18 container, P5d-T2: not allowed without this grant).
MARKERS = ("memes.environment", "memes.state", "memes.revision")


@dataclass(frozen=True)
class Names:
    """Concrete role and database names. `tag` is the environment (metal, general, it); tests pass a throwaway tag."""

    tag: str
    database: str
    owner: str
    app: str
    sync_w: str
    sync_r: str
    ro: str

    @property
    def roles(self) -> tuple[str, ...]:
        return (self.owner, self.app, self.sync_w, self.sync_r, self.ro)

    @property
    def grantees(self) -> tuple[str, ...]:
        """The four roles that receive grants (the owner owns everything)."""
        return (self.app, self.sync_w, self.sync_r, self.ro)

    def limit(self, role: str) -> int:
        return CONNECTION_LIMITS[{v: k for k, v in self.kinds().items()}[role]]

    def kinds(self) -> dict[str, str]:
        """Role kind (owner, app, sync_w, sync_r, ro) to concrete name."""
        return {"owner": self.owner, "app": self.app, "sync_w": self.sync_w, "sync_r": self.sync_r, "ro": self.ro}


def names_for(env: str) -> Names:
    """interfaces.md d: database `memes_<env>`, roles `memes_<env>_owner|app|sync_w|sync_r|ro`."""
    return make_names(env)


def make_names(tag: str, database: str | None = None) -> Names:
    prefix = f"memes_{tag}"
    return Names(tag, database or prefix, f"{prefix}_owner", f"{prefix}_app", f"{prefix}_sync_w", f"{prefix}_sync_r", f"{prefix}_ro")


# ---------------------------------------------------------------------------------------------------------- the plan

@dataclass(frozen=True)
class Plan:
    """What every role holds, derived from the registry. The SQL is a rendering of this."""

    d_tables: tuple[str, ...]
    p_tables: tuple[str, ...]
    x_tables: tuple[str, ...]
    app_write: tuple[str, ...]  # prod_write_set("backend_app")
    writer_write: tuple[str, ...]  # prod_write_set("sync_writer"): SELECT, INSERT, UPDATE
    writer_delete: tuple[str, ...]  # prod_delete_set("sync_writer"): additionally DELETE (only the per-image replaced tables)
    writer_sequence_tables: tuple[str, ...]  # integer-id D tables (registry setval)
    app_sequence_tables: tuple[str, ...]  # the class P table with a serial id (meme_impressions)
    ro_tables: tuple[str, ...]


def plan() -> Plan:
    d, p, x = (tuple(registry.names(k)) for k in (Klass.D, Klass.P, Klass.X))
    setval = registry.setval_tables()
    return Plan(
        d_tables=d,
        p_tables=p,
        x_tables=x,
        app_write=tuple(registry.prod_write_set("backend_app")),
        writer_write=tuple(registry.prod_write_set("sync_writer")),
        writer_delete=tuple(registry.prod_delete_set("sync_writer")),
        writer_sequence_tables=tuple(t for t in setval if t in d),
        app_sequence_tables=tuple(t for t in setval if t in p),
        ro_tables=tuple(t for t in sorted(registry.names()) if t not in RO_EXCLUDED),
    )


def registry_digest() -> str:
    """Short digest of everything in the registry that the grants depend on (a header line; it changes when a class does)."""
    lines = [
        f"{e.name}|{e.klass.value}|{e.prod_writer}|{int(e.setval)}|{int(e.delete_ok)}"
        for e in sorted(registry.REGISTRY.values(), key=lambda e: e.name)
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------------------------------------------- rendering

def _q(identifier: str) -> str:
    if not identifier or not all(c.isalnum() or c == "_" for c in identifier):
        raise ValueError(f"unsafe identifier {identifier!r}")
    return f'"{identifier}"'


def _lit(text: str) -> str:
    if "'" in text:
        raise ValueError(f"unsafe literal {text!r}")
    return f"'{text}'"


def _qs(items) -> str:
    return ", ".join(_q(i) for i in items)


def _array(items) -> str:
    return "ARRAY[" + ", ".join(_lit(i) for i in items) + "]"


def _wrapped(items, indent: str) -> str:
    """A comma list wrapped over several lines (quoted identifiers)."""
    out, line = [], ""
    for item in (_q(i) for i in items):
        piece = item + ", "
        if len(line) + len(piece) > 100:
            out.append(line.rstrip())
            line = ""
        line += piece
    out.append(line.rstrip().rstrip(","))
    return ("\n" + indent).join(out)


def _wrapped_array(items, indent: str) -> str:
    out, line = [], ""
    for item in (_lit(i) for i in items):
        piece = item + ", "
        if len(line) + len(piece) > 100:
            out.append(line.rstrip())
            line = ""
        line += piece
    out.append(line.rstrip().rstrip(","))
    return "ARRAY[\n" + indent + ("\n" + indent).join(out) + "\n" + indent[:-4] + "]"


def _table_grant(privs: str, tables, role: str) -> str:
    return f"GRANT {privs} ON TABLE\n        {_wrapped(tables, '        ')}\n        TO {_q(role)};"


def _expected_items(role: str, tables, privs) -> list[tuple[str, str, str]]:
    return [(role, t, p) for t in tables for p in privs]


@dataclass(frozen=True)
class Expected:
    """The privileges the registry says each role holds in schema public (the one derivation: the SQL verification block in
    `render` and `sync.release verify-grants` both read it). Tuples are (role, table, privilege) and, for columns,
    (role, table, column, privilege); sequence items are named by their owning table (the serial `id` sequence)."""

    table: tuple[tuple[str, str, str], ...]
    maintain: tuple[tuple[str, str, str], ...]  # PostgreSQL 17+ only
    column: tuple[tuple[str, str, str, str], ...]
    sequence: tuple[tuple[str, str, str], ...]


def expected(names: Names) -> Expected:
    pl = plan()
    n = names
    rw = ["SELECT", "INSERT", "UPDATE", "DELETE"]
    table: list[tuple[str, str, str]] = []
    table += _expected_items(n.app, pl.d_tables, ["SELECT"])
    table += _expected_items(n.app, pl.app_write, rw)
    table += _expected_items(n.sync_w, pl.writer_write, ["SELECT", "INSERT", "UPDATE"])
    table += _expected_items(n.sync_w, pl.writer_delete, ["DELETE"])
    table += _expected_items(n.sync_w, ["alembic_version"], ["SELECT"])
    table += _expected_items(n.sync_r, pl.p_tables, ["SELECT"])
    table += _expected_items(n.sync_r, ["alembic_version"], ["SELECT"])
    table += _expected_items(n.ro, [*pl.ro_tables, "alembic_version"], ["SELECT"])
    maintain = _expected_items(n.sync_w, pl.writer_write, ["MAINTAIN"])
    column = [(n.sync_w, "meme_text_ratings", c, "SELECT") for c in WRITER_RATINGS_COLUMNS]
    column += [(n.sync_r, "images", c, "SELECT") for c in READER_IMAGES_COLUMNS]
    sequence = _expected_items(n.sync_w, pl.writer_sequence_tables, ["USAGE", "SELECT", "UPDATE"])
    sequence += _expected_items(n.app, pl.app_sequence_tables, ["USAGE", "SELECT"])
    return Expected(tuple(table), tuple(maintain), tuple(column), tuple(sequence))


def render(names: Names) -> str:
    pl = plan()
    n = names
    db = _q(n.database)
    g4 = _qs(n.grantees)
    all5 = _qs(n.roles)
    rw = "SELECT, INSERT, UPDATE, DELETE"

    exp = expected(n)
    expected_table = ["|".join(i) for i in exp.table]
    expected_maintain = ["|".join(i) for i in exp.maintain]
    expected_column = ["|".join(i) for i in exp.column]

    limit_case = "\n".join(
        f"                WHEN {_lit(role)} THEN {n.limit(role)}" for role in n.roles
    )

    o: list[str] = []
    w = o.append
    w(f"-- roles and grants of the prod database {n.database} (environment {n.tag}).")
    w(f"-- GENERATED by `python -m sync.roles --env {n.tag}` (generator version {GENERATOR_VERSION}, registry digest {registry_digest()}).")
    w("-- Do not edit: tests/sync/test_roles.py fails when this file is not what the generator produces from sync/registry.py.")
    w("--")
    w("-- Contract: docs/superpowers/specs/2026-11-12-environment-data-sync/interfaces.md section h.")
    w("-- Re-applied at every release (idempotent, one transaction): everything the five roles hold in schema public is revoked and the")
    w("-- exact list granted again, then a verification block raises when the effective privileges differ from the registry.")
    w("-- Run it with:  psql -X -v ON_ERROR_STOP=1 -d <database> -f roles.<env>.sql   (as the bootstrap superuser, or as the owner role).")
    w("-- No credential is in this file. A role created here has no credential and cannot log in until provision.sh sets one with an")
    w("-- ALTER ROLE statement fed through a psql variable; a re-apply never touches credentials.")
    w("-- MAINTAIN needs PostgreSQL 17 or later and is skipped on older servers (the local CI service container is 16).")
    w("")
    w("BEGIN;")
    w("")
    w("-- 1. Guard: this file must never run against another database.")
    w("DO $guard$")
    w("BEGIN")
    w(f"    IF current_database() <> {_lit(n.database)} THEN")
    w(f"        RAISE EXCEPTION 'roles file of database {n.database} applied to database %', current_database();")
    w("    END IF;")
    w("END")
    w("$guard$;")
    w("")
    w("-- 2. Roles (superuser only). Attributes, connection limits, role settings, no memberships, owner of the database.")
    w("DO $roles$")
    w("DECLARE")
    w("    r text;")
    w("    m record;")
    w("BEGIN")
    w("    IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN")
    w("        RAISE NOTICE 'not a superuser: roles, attributes and the database owner are verified below, not changed';")
    w("        RETURN;")
    w("    END IF;")
    w(f"    FOREACH r IN ARRAY {_array(n.roles)} LOOP")
    w("        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN")
    w("            EXECUTE format('CREATE ROLE %I', r);")
    w("        END IF;")
    w("    END LOOP;")
    for role in n.roles:
        w(f"    ALTER ROLE {_q(role)} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT"
          f" CONNECTION LIMIT {n.limit(role)};")
    for role in n.roles:
        w(f"    ALTER ROLE {_q(role)} RESET ALL;")
    for key, value in APP_SETTINGS:
        w(f"    ALTER ROLE {_q(n.app)} SET {key} = {_lit(value)};")
    w("    FOR m IN")
    w("        SELECT granted.rolname AS granted, member.rolname AS member, grantor.rolname AS grantor")
    w("        FROM pg_auth_members am")
    w("        JOIN pg_roles granted ON granted.oid = am.roleid")
    w("        JOIN pg_roles member ON member.oid = am.member")
    w("        JOIN pg_roles grantor ON grantor.oid = am.grantor")
    w(f"        WHERE member.rolname = ANY({_array(n.roles)})")
    w("    LOOP")
    w("        EXECUTE format('REVOKE %I FROM %I GRANTED BY %I', m.granted, m.member, m.grantor);")
    w("    END LOOP;")
    w(f"    ALTER DATABASE {db} OWNER TO {_q(n.owner)};")
    w("    -- The database markers (provision.sh and the release runbook set them as the owner): since PostgreSQL 15 a database owner may not")
    w("    -- ALTER DATABASE ... SET a custom parameter without the SET privilege on that parameter, and only a superuser can grant it.")
    w(f"    IF current_setting('server_version_num')::int >= {PARAMETER_ACL_MIN_VERSION_NUM} THEN")
    w(f"        EXECUTE 'REVOKE ALL ON PARAMETER {', '.join(MARKERS)} FROM {_qs(n.grantees)}';")
    w(f"        EXECUTE 'GRANT SET ON PARAMETER {', '.join(MARKERS)} TO {_q(n.owner)}';")
    w("    END IF;")
    w("END")
    w("$roles$;")
    w("")
    w("-- 3. Database and schema level: no PUBLIC access, per-role CONNECT only, nobody but the owner creates objects.")
    w(f"REVOKE ALL ON DATABASE {db} FROM PUBLIC;")
    w(f"REVOKE ALL ON DATABASE {db} FROM {g4};")
    w(f"GRANT CONNECT ON DATABASE {db} TO {g4};")
    w(f"REVOKE CREATE ON SCHEMA {SCHEMA} FROM PUBLIC;")
    w(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM {g4};")
    w(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {g4};")
    w("-- No default privileges for any role except the owner's own: a new table is unreachable until this file runs again.")
    for kind in ("TABLES", "SEQUENCES"):
        w(f"ALTER DEFAULT PRIVILEGES FOR ROLE {_q(n.owner)} REVOKE ALL ON {kind} FROM {g4};")
        w(f"ALTER DEFAULT PRIVILEGES FOR ROLE {_q(n.owner)} IN SCHEMA {SCHEMA} REVOKE ALL ON {kind} FROM {g4};")
    w("")
    w("-- 4. Object grants: revoke everything first, then grant exactly the list (this is what removes drift).")
    w(f"REVOKE ALL ON ALL TABLES IN SCHEMA {SCHEMA} FROM {g4};")
    w(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {SCHEMA} FROM {g4};")
    w("DO $grants$")
    w("DECLARE")
    w("    t text;")
    w("    s text;")
    w("BEGIN")
    w(f"    IF to_regclass('{SCHEMA}.alembic_version') IS NULL THEN")
    w("        RAISE NOTICE 'no schema yet (no alembic_version): table grants skipped, apply this file again after the first migration';")
    w("        RETURN;")
    w("    END IF;")
    w(f"    -- {n.app}: reads class D, reads and writes exactly the class P tables, nothing on class X or alembic_version.")
    w("    " + _table_grant("SELECT", pl.d_tables, n.app).replace("\n", "\n    "))
    w("    " + _table_grant(rw, pl.app_write, n.app).replace("\n", "\n    "))
    w(f"    -- {n.sync_w}: writes class D, reads alembic_version, three ratings columns (no text), no TEMP, nothing on class P or X.")
    w("    " + _table_grant("SELECT, INSERT, UPDATE", pl.writer_write, n.sync_w).replace("\n", "\n    "))
    w("    -- DELETE only where the promote replaces rows per image (never images, an upsert table or a global set).")
    w("    " + _table_grant("DELETE", pl.writer_delete, n.sync_w).replace("\n", "\n    "))
    w(f"    GRANT SELECT ON TABLE alembic_version TO {_q(n.sync_w)};")
    w(f"    GRANT SELECT ({', '.join(WRITER_RATINGS_COLUMNS)}) ON TABLE meme_text_ratings TO {_q(n.sync_w)};")
    w(f"    IF current_setting('server_version_num')::int >= {MAINTAIN_MIN_VERSION_NUM} THEN")
    w("        EXECUTE format('GRANT MAINTAIN ON TABLE %s TO %I',")
    w("            (SELECT string_agg(quote_ident(x), ', ') FROM unnest(" + _wrapped_array(pl.writer_write, "                ") + ") x),")
    w(f"            {_lit(n.sync_w)});")
    w("    END IF;")
    w(f"    -- {n.sync_r}: reads the class P tables, alembic_version and images.id only.")
    w("    " + _table_grant("SELECT", pl.p_tables, n.sync_r).replace("\n", "\n    "))
    w(f"    GRANT SELECT ON TABLE alembic_version TO {_q(n.sync_r)};")
    w(f"    GRANT SELECT ({', '.join(READER_IMAGES_COLUMNS)}) ON TABLE images TO {_q(n.sync_r)};")
    w(f"    -- {n.ro}: reads everything except the reader-activity text tables.")
    w("    " + _table_grant("SELECT", [*pl.ro_tables, "alembic_version"], n.ro).replace("\n", "\n    "))
    w(f"    -- Sequences. {n.sync_w}: the integer-id class D tables (setval after a load). {n.app}: the serial id of a class P table.")
    w(f"    FOREACH t IN ARRAY {_wrapped_array(pl.writer_sequence_tables, '        ')} LOOP")
    w(f"        s := pg_get_serial_sequence(format('{SCHEMA}.%I', t), 'id');")
    w("        IF s IS NULL THEN RAISE EXCEPTION 'table % has no serial id sequence', t; END IF;")
    w(f"        EXECUTE format('GRANT USAGE, SELECT, UPDATE ON SEQUENCE %s TO %I', s, {_lit(n.sync_w)});")
    w("    END LOOP;")
    w(f"    FOREACH t IN ARRAY {_wrapped_array(pl.app_sequence_tables, '        ')} LOOP")
    w(f"        s := pg_get_serial_sequence(format('{SCHEMA}.%I', t), 'id');")
    w("        IF s IS NULL THEN RAISE EXCEPTION 'table % has no serial id sequence', t; END IF;")
    w(f"        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO %I', s, {_lit(n.app)});")
    w("    END LOOP;")
    w("END")
    w("$grants$;")
    w("")
    w("-- 5. Verification: the effective privileges (PUBLIC and memberships included) must equal the registry; otherwise the whole file rolls back.")
    w("DO $verify$")
    w("DECLARE")
    w("    bad text;")
    w(f"    five text[] := {_array(n.roles)};")
    w(f"    four text[] := {_array(n.grantees)};")
    w("    privs text[] := ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'];")
    w(f"    can_maintain boolean := current_setting('server_version_num')::int >= {MAINTAIN_MIN_VERSION_NUM};")
    w("BEGIN")
    w("    SELECT string_agg(r, ', ') INTO bad FROM unnest(five) r WHERE NOT EXISTS (")
    w("        SELECT 1 FROM pg_roles x WHERE x.rolname = r AND x.rolcanlogin AND NOT x.rolsuper AND NOT x.rolcreatedb")
    w("            AND NOT x.rolcreaterole AND NOT x.rolreplication AND NOT x.rolbypassrls AND x.rolinherit")
    w("            AND x.rolconnlimit = CASE r")
    w(limit_case)
    w("            END);")
    w("    IF bad IS NOT NULL THEN RAISE EXCEPTION 'role attributes or connection limit differ from the contract: %', bad; END IF;")
    w("    SELECT string_agg(member.rolname || ' in ' || granted.rolname, ', ') INTO bad FROM pg_auth_members am")
    w("        JOIN pg_roles granted ON granted.oid = am.roleid JOIN pg_roles member ON member.oid = am.member")
    w("        WHERE member.rolname = ANY(five);")
    w("    IF bad IS NOT NULL THEN RAISE EXCEPTION 'role memberships found: %', bad; END IF;")
    w(f"    IF (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()) <> {_lit(n.owner)} THEN")
    w(f"        RAISE EXCEPTION 'the owner of the database is not {n.owner}';")
    w("    END IF;")
    w("    SELECT string_agg(x.rolname, ', ') INTO bad FROM unnest(five) r JOIN pg_roles x ON x.rolname = r")
    w("        WHERE NOT has_database_privilege(x.oid, current_database(), 'CONNECT');")
    w("    IF bad IS NOT NULL THEN RAISE EXCEPTION 'role without CONNECT: %', bad; END IF;")
    w("    SELECT string_agg(x.rolname, ', ') INTO bad FROM unnest(four) r JOIN pg_roles x ON x.rolname = r")
    w("        WHERE has_database_privilege(x.oid, current_database(), 'CREATE')")
    w("            OR has_database_privilege(x.oid, current_database(), 'TEMPORARY')")
    w(f"            OR has_schema_privilege(x.oid, '{SCHEMA}', 'CREATE');")
    w("    IF bad IS NOT NULL THEN RAISE EXCEPTION 'role with CREATE or TEMP: %', bad; END IF;")
    w(f"    IF EXISTS (SELECT 1 FROM pg_namespace n, aclexplode(n.nspacl) a WHERE n.nspname = '{SCHEMA}' AND a.grantee = 0"
      f" AND a.privilege_type = 'CREATE') THEN")
    w(f"        RAISE EXCEPTION 'PUBLIC can create in schema {SCHEMA}';")
    w("    END IF;")
    w("    IF EXISTS (SELECT 1 FROM pg_database d, aclexplode(d.datacl) a WHERE d.datname = current_database() AND a.grantee = 0) THEN")
    w("        RAISE EXCEPTION 'PUBLIC holds a privilege on the database';")
    w("    END IF;")
    w("    SELECT string_agg(a.privilege_type || ' for ' || x.rolname, ', ') INTO bad")
    w("        FROM pg_default_acl d, aclexplode(d.defaclacl) a JOIN pg_roles x ON x.oid = a.grantee")
    w("        WHERE x.rolname = ANY(four);")
    w("    IF bad IS NOT NULL THEN RAISE EXCEPTION 'default privileges found: %', bad; END IF;")
    w(f"    IF current_setting('server_version_num')::int >= {PARAMETER_ACL_MIN_VERSION_NUM} THEN")
    w("        SELECT string_agg(x || ' on ' || m, ', ') INTO bad")
    w(f"        FROM unnest({_array(MARKERS)}) m CROSS JOIN unnest(five) x")
    w(f"        WHERE (x = {_lit(n.owner)}) <> has_parameter_privilege(x, m, 'SET');")
    w("        IF bad IS NOT NULL THEN RAISE EXCEPTION 'SET privilege on the database markers differs from the contract (owner only): %', bad; END IF;")
    w("    END IF;")
    w(f"    IF to_regclass('{SCHEMA}.alembic_version') IS NULL THEN")
    w("        RAISE NOTICE 'no schema yet: object privileges not verified';")
    w("        RETURN;")
    w("    END IF;")
    w("    IF can_maintain THEN privs := privs || ARRAY['MAINTAIN']; END IF;")
    w("    WITH expected(item) AS (")
    w("        SELECT unnest(" + _wrapped_array(expected_table, "            ") + ")")
    w("        UNION ALL")
    w("        SELECT unnest(" + _wrapped_array(expected_maintain, "            ") + ") WHERE can_maintain")
    w("    ), actual(item) AS (")
    w("        SELECT r.rolname || '|' || c.relname || '|' || p")
    w("        FROM pg_roles r CROSS JOIN pg_class c CROSS JOIN unnest(privs) p")
    w(f"        WHERE r.rolname = ANY(four) AND c.relnamespace = '{SCHEMA}'::regnamespace AND c.relkind IN ('r', 'p', 'v', 'm', 'f')")
    w("            AND has_table_privilege(r.oid, c.oid, p)")
    w("    )")
    w("    SELECT string_agg(item, ', ' ORDER BY item) INTO bad FROM (")
    w("        (SELECT 'extra ' || item AS item FROM actual EXCEPT SELECT 'extra ' || item FROM expected)")
    w("        UNION ALL")
    w("        (SELECT 'missing ' || item FROM expected EXCEPT SELECT 'missing ' || item FROM actual)")
    w("    ) d;")
    w("    IF bad IS NOT NULL THEN RAISE EXCEPTION 'table privileges differ from the registry: %', left(bad, 600); END IF;")
    w("    WITH expected(item) AS (")
    w("        SELECT unnest(" + _wrapped_array(expected_column, "            ") + ")")
    w("    ), actual(item) AS (")
    w("        SELECT coalesce(r.rolname, 'PUBLIC') || '|' || c.relname || '|' || a.attname || '|' || x.privilege_type")
    w("        FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid")
    w("        CROSS JOIN LATERAL aclexplode(a.attacl) x LEFT JOIN pg_roles r ON r.oid = x.grantee")
    w(f"        WHERE a.attacl IS NOT NULL AND c.relnamespace = '{SCHEMA}'::regnamespace AND NOT a.attisdropped")
    w("            AND (x.grantee = 0 OR r.rolname = ANY(five))")
    w("    )")
    w("    SELECT string_agg(item, ', ' ORDER BY item) INTO bad FROM (")
    w("        (SELECT 'extra ' || item AS item FROM actual EXCEPT SELECT 'extra ' || item FROM expected)")
    w("        UNION ALL")
    w("        (SELECT 'missing ' || item FROM expected EXCEPT SELECT 'missing ' || item FROM actual)")
    w("    ) d;")
    w("    IF bad IS NOT NULL THEN RAISE EXCEPTION 'column privileges differ from the registry: %', left(bad, 600); END IF;")
    w("    WITH expected(item) AS (")
    w("        SELECT " + _lit(n.sync_w) + " || '|' || pg_get_serial_sequence('" + SCHEMA + ".' || quote_ident(t), 'id') || '|' || p")
    w("        FROM unnest(" + _wrapped_array(pl.writer_sequence_tables, "            ") + ") t CROSS JOIN unnest(ARRAY['USAGE', 'SELECT', 'UPDATE']) p")
    w("        UNION ALL")
    w("        SELECT " + _lit(n.app) + " || '|' || pg_get_serial_sequence('" + SCHEMA + ".' || quote_ident(t), 'id') || '|' || p")
    w("        FROM unnest(" + _wrapped_array(pl.app_sequence_tables, "            ") + ") t CROSS JOIN unnest(ARRAY['USAGE', 'SELECT']) p")
    w("    ), actual(item) AS (")
    w(f"        SELECT r.rolname || '|{SCHEMA}.' || c.relname || '|' || p")
    w("        FROM pg_roles r CROSS JOIN pg_class c CROSS JOIN unnest(ARRAY['USAGE', 'SELECT', 'UPDATE']) p")
    w(f"        WHERE r.rolname = ANY(four) AND c.relnamespace = '{SCHEMA}'::regnamespace AND c.relkind = 'S'")
    w("            AND has_sequence_privilege(r.oid, c.oid, p)")
    w("    )")
    w("    SELECT string_agg(item, ', ' ORDER BY item) INTO bad FROM (")
    w("        (SELECT 'extra ' || item AS item FROM actual EXCEPT SELECT 'extra ' || item FROM expected)")
    w("        UNION ALL")
    w("        (SELECT 'missing ' || item FROM expected EXCEPT SELECT 'missing ' || item FROM actual)")
    w("    ) d;")
    w("    IF bad IS NOT NULL THEN RAISE EXCEPTION 'sequence privileges differ from the registry: %', left(bad, 600); END IF;")
    w("END")
    w("$verify$;")
    w("")
    w("COMMIT;")
    return "\n".join(o) + "\n"


def output_path(env: str) -> Path:
    return SQL_DIR / f"roles.{env}.sql"


def generate(env: str) -> str:
    if env not in ENVS:
        raise ValueError(f"unknown environment {env!r}")
    return render(names_for(env))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sync.roles", description="Generate deploy/prod/sql/roles.<env>.sql from the registry.")
    parser.add_argument("--env", required=True, choices=ENVS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="exit 1 when the committed file differs from a regeneration")
    mode.add_argument("--stdout", action="store_true", help="print instead of writing")
    args = parser.parse_args(argv)
    text = generate(args.env)
    path = output_path(args.env)
    if args.stdout:
        sys.stdout.write(text)
        return 0
    if args.check:
        current = path.read_bytes().decode("utf-8") if path.exists() else ""
        if current != text:
            print(f"deploy/prod/sql/{path.name} is stale: run `python -m sync.roles --env {args.env}`", file=sys.stderr)
            return 1
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))  # LF only (.gitattributes: deploy/prod/** text eol=lf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
