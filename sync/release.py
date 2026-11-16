"""Release step 2 and 2b of a production environment (epic environment-data-sync, project P5d-T8).

    python -m sync.release migrate         # alembic upgrade head, as the owner role
    python -m sync.release grants          # apply deploy/prod/sql/roles.<env>.sql, as the owner role (idempotent)
    python -m sync.release verify-grants   # compare the effective privileges with the registry; exit 3 on any mismatch
    python -m sync.release all             # migrate, grants, verify-grants; stops at the first failure

The `migrate` service of deploy/prod/compose.yaml runs `all` in the memes-migrate image. The environment is `--env` or APP_ENV. The
connection string is read only from DATABASE_URL (the owner role over the unix socket) and is never printed; nothing here prints a
password, a URL or a row of data. The grants the check expects come from `sync.roles.expected`, the same derivation that writes the
verification block of roles.<env>.sql, so there is one source (sync/registry.py).

Exit codes: 0 ok, 1 a step failed (migration or grants error), 2 a precondition refused (wrong environment marker, wrong role, stale
roles file, no DATABASE_URL), 3 verify-grants found a mismatch.

The planning and the diff are pure (no database); the I/O is the `fetch_*` and `run_*` functions at the bottom.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from sync import registry, roles

ROOT = Path(__file__).resolve().parents[1]
EXIT_OK, EXIT_FAILED, EXIT_REFUSED, EXIT_MISMATCH = 0, 1, 2, 3
MAX_LINES = 200  # a runaway diff (a table renamed, say) prints this many lines and a count
TABLE_PRIVS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
SEQUENCE_PRIVS = ("USAGE", "SELECT", "UPDATE")
RELKINDS = ("r", "p", "v", "m", "f")
SCHEMA_TABLE_KINDS = ("r", "p")


class Refused(Exception):
    """A precondition failed: nothing was changed."""


# ----------------------------------------------------------------------------------------------------------------- pure

@dataclass(frozen=True)
class Actual:
    """What the database says (all in schema public, for the five roles of the environment)."""

    roles: frozenset[str]
    tables: frozenset[str]  # ordinary and partitioned tables
    table: frozenset[tuple[str, str, str]]  # (role, relation, privilege), effective (PUBLIC and memberships included)
    column: frozenset[tuple[str, str, str, str]]  # (role, table, column, privilege), explicit column ACLs
    sequence: frozenset[tuple[str, str, str]]  # (role, owning table, privilege)
    server_version_num: int


@dataclass(frozen=True, order=True)
class Mismatch:
    kind: str  # missing | extra | missing role | missing table | unclassified table
    role: str
    object: str
    privilege: str


def can_maintain(server_version_num: int) -> bool:
    return server_version_num >= roles.MAINTAIN_MIN_VERSION_NUM


def privilege_names(server_version_num: int) -> tuple[str, ...]:
    return TABLE_PRIVS + (("MAINTAIN",) if can_maintain(server_version_num) else ())


def evaluate_identity(env: str, marker: str | None, database: str, user: str, *, require_owner: bool) -> list[str]:
    """Reasons to refuse (empty when fine). `marker` is current_setting('memes.environment', true)."""
    names = roles.names_for(env)
    problems = []
    if marker is None or marker == "":
        problems.append("the database has no memes.environment marker (provision.sh sets it)")
    elif marker != env:
        problems.append(f"APP_ENV is {env!r} but the database marker memes.environment is {marker!r}")
    if database != names.database:
        problems.append(f"connected to database {database!r}, expected {names.database!r}")
    if require_owner and user != names.owner:
        problems.append(f"connected as {user!r}, expected the owner role {names.owner!r}")
    return problems


def diff(env: str, actual: Actual, exp: roles.Expected | None = None) -> list[Mismatch]:
    """Every difference between what the registry says the roles hold and what the database says. Pure."""
    names = roles.names_for(env)
    exp = exp or roles.expected(names)
    maintain = can_maintain(actual.server_version_num)
    out: list[Mismatch] = []

    classified = set(registry.names()) | {"alembic_version"}
    for role in names.roles:
        if role not in actual.roles:
            out.append(Mismatch("missing role", role, "-", "-"))
    for table in sorted(classified - actual.tables):
        out.append(Mismatch("missing table", "-", table, "-"))
    for table in sorted(actual.tables - classified):
        out.append(Mismatch("unclassified table", "-", table, "-"))

    present_roles = {r for r in names.roles if r in actual.roles}

    def keep(role: str, table: str) -> bool:
        return role in present_roles and table in actual.tables  # a missing role or table is reported once, above

    want_table = {i for i in exp.table if keep(i[0], i[1])}
    if maintain:
        want_table |= {i for i in exp.maintain if keep(i[0], i[1])}
    for role, table, priv in sorted(want_table - actual.table):
        out.append(Mismatch("missing", role, table, priv))
    for role, table, priv in sorted(actual.table - want_table):
        if role in present_roles:
            out.append(Mismatch("extra", role, table, priv))

    want_column = {i for i in exp.column if keep(i[0], i[1])}
    for role, table, column, priv in sorted(want_column - actual.column):
        out.append(Mismatch("missing", role, f"{table}.{column}", priv))
    for role, table, column, priv in sorted(actual.column - want_column):
        out.append(Mismatch("extra", role, f"{table}.{column}", priv))

    want_sequence = {i for i in exp.sequence if keep(i[0], i[1])}
    for role, table, priv in sorted(want_sequence - actual.sequence):
        out.append(Mismatch("missing", role, f"sequence of {table}", priv))
    for role, table, priv in sorted(actual.sequence - want_sequence):
        if role in present_roles:
            out.append(Mismatch("extra", role, f"sequence of {table}", priv))
    return out


def render_mismatches(mismatches: list[Mismatch]) -> str:
    """The table printed on a failure. Names of roles, tables and privileges only."""
    if not mismatches:
        return "grants match the registry"
    rows = [("kind", "role", "object", "privilege"), *[(m.kind, m.role, m.object, m.privilege) for m in mismatches[:MAX_LINES]]]
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    lines = ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip() for r in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    if len(mismatches) > MAX_LINES:
        lines.append(f"... and {len(mismatches) - MAX_LINES} more")
    lines.append(f"{len(mismatches)} mismatch(es)")
    return "\n".join(lines)


def exit_code_for(mismatches: list[Mismatch]) -> int:
    return EXIT_MISMATCH if mismatches else EXIT_OK


def normalise_url(url: str) -> str:
    """A libpq URI. The dev tools carry a SQLAlchemy driver suffix in DATABASE_URL; the driver here is psycopg2 either way."""
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    return url


def roles_file(env: str, directory: Path | None = None) -> Path:
    return (directory or roles.SQL_DIR) / f"roles.{env}.sql"


def stale_reason(env: str, file_text: str) -> str | None:
    """The committed file must be exactly what the generator writes from the registry in this image."""
    return None if file_text == roles.generate(env) else (
        f"roles.{env}.sql differs from what sync.roles generates from the registry of this image (rebuild the image from the same commit)"
    )


# ------------------------------------------------------------------------------------------------------------------ I/O

def _connect():
    import psycopg2

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise Refused("DATABASE_URL is not set")
    conn = psycopg2.connect(normalise_url(url), connect_timeout=15)
    conn.autocommit = True
    return conn


def _scalar(conn, sql, args=None):
    with conn.cursor() as cur:
        cur.execute(sql, args)
        row = cur.fetchone()
        return row[0] if row else None


def _check_identity(conn, env: str, *, require_owner: bool) -> None:
    marker = _scalar(conn, "select current_setting('memes.environment', true)")
    problems = evaluate_identity(env, marker, _scalar(conn, "select current_database()"), _scalar(conn, "select current_user"),
                                 require_owner=require_owner)
    if problems:
        raise Refused("; ".join(problems))


def fetch_actual(conn, env: str) -> Actual:
    names = roles.names_for(env)
    version = int(_scalar(conn, "show server_version_num"))
    four, five = list(names.grantees), list(names.roles)
    with conn.cursor() as cur:
        cur.execute("select rolname from pg_roles where rolname = any(%s)", (five,))
        present = frozenset(r[0] for r in cur.fetchall())
        cur.execute("select relname from pg_class where relnamespace = 'public'::regnamespace and relkind = any(%s)",
                    (list(SCHEMA_TABLE_KINDS),))
        tables = frozenset(r[0] for r in cur.fetchall())
        cur.execute(
            """select r.rolname, c.relname, p from pg_roles r cross join pg_class c cross join unnest(%s::text[]) p
               where r.rolname = any(%s) and c.relnamespace = 'public'::regnamespace and c.relkind = any(%s)
                 and has_table_privilege(r.oid, c.oid, p)""",
            (list(privilege_names(version)), four, list(RELKINDS)))
        table = frozenset(tuple(r) for r in cur.fetchall())
        cur.execute(
            """select r.rolname, c.relname, a.attname, x.privilege_type
               from pg_attribute a join pg_class c on c.oid = a.attrelid
               cross join lateral aclexplode(a.attacl) x join pg_roles r on r.oid = x.grantee
               where a.attacl is not null and c.relnamespace = 'public'::regnamespace and not a.attisdropped
                 and r.rolname = any(%s)""", (four,))
        column = frozenset(tuple(r) for r in cur.fetchall())
        cur.execute(
            """select r.rolname, t.relname, p
               from pg_roles r cross join pg_class s
               join pg_depend d on d.objid = s.oid and d.classid = 'pg_class'::regclass and d.refclassid = 'pg_class'::regclass
                                and d.deptype in ('a', 'i')
               join pg_class t on t.oid = d.refobjid
               cross join unnest(%s::text[]) p
               where s.relkind = 'S' and s.relnamespace = 'public'::regnamespace and r.rolname = any(%s)
                 and has_sequence_privilege(r.oid, s.oid, p)""", (list(SEQUENCE_PRIVS), four))
        sequence = frozenset(tuple(r) for r in cur.fetchall())
    return Actual(present, tables, table, column, sequence, version)


def _alembic_revision(conn) -> str | None:
    if _scalar(conn, "select to_regclass('public.alembic_version')") is None:
        return None
    return _scalar(conn, "select version_num from public.alembic_version limit 1")


def code_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ROOT / "Storage" / "alembic.ini"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def run_migrate(env: str) -> int:
    conn = _connect()
    try:
        _check_identity(conn, env, require_owner=True)
        before = _alembic_revision(conn)
    finally:
        conn.close()
    head = code_head()
    print(f"migrate: database revision {before or '(no schema yet)'}, code head {head}", flush=True)
    # A subprocess so alembic's own logging setup and sys.path handling (Storage/alembic/env.py expects cwd = Storage) stay out of
    # this process. The environment, DATABASE_URL included, is inherited and never printed.
    child = os.environ.copy()
    child["PYTHONPATH"] = str(ROOT) + os.pathsep + child.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"], cwd=ROOT / "Storage", env=child)
    if result.returncode != 0:
        print(f"migrate: alembic upgrade head failed (exit {result.returncode}); see the lines above", file=sys.stderr)
        return EXIT_FAILED
    conn = _connect()
    try:
        after = _alembic_revision(conn)
    finally:
        conn.close()
    if after != head:
        print(f"migrate: revision after the upgrade is {after}, expected the code head {head}", file=sys.stderr)
        return EXIT_FAILED
    print(f"migrate: database is at {after} (the code head); set memes.revision after the backend restart (runbook step 3)", flush=True)
    return EXIT_OK


def run_grants(env: str, directory: Path | None = None) -> int:
    import psycopg2

    path = roles_file(env, directory)
    text = path.read_bytes().decode("utf-8")
    reason = stale_reason(env, text)
    if reason:
        raise Refused(reason)
    conn = _connect()
    try:
        _check_identity(conn, env, require_owner=True)
        try:
            with conn.cursor() as cur:
                cur.execute(text)
        except psycopg2.Error as exc:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK")  # the file opens its own transaction; an error leaves it aborted
            print(f"grants: {path.name} failed (sqlstate {exc.pgcode}): {(exc.diag.message_primary or '').strip()}", file=sys.stderr)
            return EXIT_FAILED
    finally:
        conn.close()
    print(f"grants: {path.name} applied", flush=True)
    return EXIT_OK


def run_verify(env: str) -> int:
    conn = _connect()
    try:
        _check_identity(conn, env, require_owner=False)
        mismatches = diff(env, fetch_actual(conn, env))
    finally:
        conn.close()
    text = render_mismatches(mismatches)
    print(f"verify-grants: {text}", file=sys.stderr if mismatches else sys.stdout, flush=True)
    return exit_code_for(mismatches)


def run_all(env: str) -> int:
    for name, step in (("migrate", run_migrate), ("grants", run_grants), ("verify-grants", run_verify)):
        code = step(env)
        if code != EXIT_OK:
            print(f"release: stopped at {name} (exit {code})", file=sys.stderr)
            return code
    print("release: migrate, grants and verify-grants are done", flush=True)
    return EXIT_OK


COMMANDS = {"migrate": run_migrate, "grants": run_grants, "verify-grants": run_verify, "all": run_all}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sync.release", description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--env", choices=roles.ENVS, help="default: APP_ENV")
    args = parser.parse_args(argv)
    env = args.env or os.environ.get("APP_ENV")
    if env not in roles.ENVS:
        print(f"release: APP_ENV or --env must be one of {', '.join(roles.ENVS)}", file=sys.stderr)
        return EXIT_REFUSED
    if os.environ.get("APP_ENV") and os.environ["APP_ENV"] != env:
        print(f"release: --env {env} differs from APP_ENV {os.environ['APP_ENV']}", file=sys.stderr)
        return EXIT_REFUSED
    try:
        return COMMANDS[args.command](env)
    except Refused as exc:
        print(f"release: refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as exc:  # noqa: BLE001 - a driver message of a connection failure holds no credential (libpq never echoes one)
        code = getattr(exc, "pgcode", None)
        primary = getattr(getattr(exc, "diag", None), "message_primary", None)
        first = (primary or (str(exc).splitlines() or [""])[0] if type(exc).__module__.startswith("psycopg2") else "").strip()
        print(f"release: {type(exc).__name__}{f' (sqlstate {code})' if code else ''}{': ' + first if first else ''}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
