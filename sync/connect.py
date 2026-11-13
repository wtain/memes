"""Connections and the identity handshake of the sync tools (interfaces.md c, e, f, h).

Driver: psycopg2 (already pinned in requirements.txt as psycopg2-binary, and used by the integration fixtures). It is
synchronous, which suits the COPY-based later tasks (P2 promote, P4 pull); asyncpg would need a second event-loop layer for
no gain. psycopg2 is imported at module level on purpose; the pure registry modules (sync.model/registry/render) stay free of it.

Redaction: every database error is converted to `DbError` carrying only SQLSTATE, constraint name and table name (psycopg2's
message and `pgerror` can quote row values, so they are never logged and the original exception is not chained). Handshake
results are `{step, ok, detail}` with a lower-case code-like detail, the shape sync/report.py accepts.

Secrets: dev URLs come from environments/.env.<env> through config.settings.load_env at runtime only; the prod password comes
from sync.config.load_secrets (a dict, never os.environ). Nothing here prints a URL or password.
"""
from __future__ import annotations

import contextlib
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

import psycopg2

from sync import registry
from sync.config import REPO_ROOT, ConfigError, PreconditionError, SyncError, SyncSettings, load_secrets
from sync.model import Klass

# ------------------------------------------------------------------------------------------------------ errors

class DbError(SyncError):
    """A database failure reduced to SQLSTATE, constraint and table names (exit 1)."""

    def __init__(self, sqlstate: str | None, constraint: str | None = None, table: str | None = None, kind: str = "error"):
        self.sqlstate, self.constraint, self.table, self.kind = sqlstate, constraint, table, kind
        parts = [f"database {kind}", f"sqlstate={sqlstate or 'none'}"]
        if constraint:
            parts.append(f"constraint={constraint}")
        if table:
            parts.append(f"table={table}")
        super().__init__(" ".join(parts))

    @property
    def code(self) -> str:
        return f"db_error:{(self.sqlstate or 'none').lower()}"


def describe_db_error(exc: BaseException) -> DbError:
    diag = getattr(exc, "diag", None)
    constraint = getattr(diag, "constraint_name", None) if diag is not None else None
    table = getattr(diag, "table_name", None) if diag is not None else None
    return DbError(getattr(exc, "pgcode", None), constraint, table, kind=type(exc).__name__)


@contextlib.contextmanager
def guard_db() -> Iterator[None]:
    """Turn any psycopg2 error into DbError. `from None` and no chaining: the original message may quote row values."""
    try:
        yield
    except psycopg2.Error as exc:
        err = describe_db_error(exc)
        exc.__traceback__ = None
        raise err from None


class HandshakeError(PreconditionError):
    """A handshake step failed (exit 2). `results` holds every step run so far (detail codes only)."""

    def __init__(self, message: str, results: list[dict]):
        super().__init__(message)
        self.results = results


# ---------------------------------------------------------------------------------------------------- connections

DEV_RO, DEV_RW = "dev_ro", "dev_rw"
PROD_WRITER, PROD_READER, PROD_WRITER_RO = "prod_writer", "prod_reader", "prod_writer_readonly"
LOOPBACK = "127.0.0.1"


class SyncConnection:
    """A psycopg2 connection (autocommit, so reads never leave a transaction open) that records what it was asked to reach."""

    def __init__(self, raw, kind: str, host: str | None = None, port: int | None = None, app_env: str | None = None):
        self.raw, self.kind, self.host, self.port, self.app_env = raw, kind, host, port, app_env

    def query(self, sql: str, args: Any = None) -> list[tuple]:
        with guard_db(), self.raw.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() if cur.description is not None else []

    def scalar(self, sql: str, args: Any = None) -> Any:
        rows = self.query(sql, args)
        return rows[0][0] if rows else None

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.raw.close()

    def __enter__(self) -> "SyncConnection":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _libpq(url: str) -> str:
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url)


def _open(connector: Callable[..., Any], read_only: bool, *args: Any, **kwargs: Any):
    with guard_db():
        raw = connector(*args, **kwargs)
        raw.autocommit = True
        if read_only:
            with raw.cursor() as cur:
                cur.execute("SET default_transaction_read_only = on")
    return raw


@dataclass
class DevUrls:
    app_env: str | None
    rw: str | None = field(default=None, repr=False)
    ro: str | None = field(default=None, repr=False)


def load_dev_urls(env: str, base_dir: Path | None = None) -> DevUrls:
    """Load environments/.env.<env> through config.settings.load_env and read the three values. Runtime only; nothing is printed."""
    import os

    from config.settings import load_env

    try:
        load_env(env, base_dir or (REPO_ROOT / "environments"))
    except Exception as exc:  # a missing file or env: the type is enough, never the message
        raise ConfigError(f"cannot load the dev environment file for {env!r} ({type(exc).__name__})") from None
    return DevUrls(os.environ.get("APP_ENV"), os.environ.get("DATABASE_URL"), os.environ.get("DATABASE_URL_READONLY"))


def _dev(kind: str, env: str, dev_urls: DevUrls | None, connector: Callable[..., Any]) -> SyncConnection:
    urls = dev_urls if dev_urls is not None else load_dev_urls(env)
    if not urls.ro:
        raise PreconditionError("H1 dev_ro_url_missing")
    if kind == DEV_RO:
        url = urls.ro
        if urls.rw and urls.rw == urls.ro:  # the read-only tools must not be handed the write-capable URL
            raise PreconditionError("H1 dev_url_kind_wrong")
    else:
        url = urls.rw
        if not url:
            raise PreconditionError("H1 dev_rw_url_missing")
        if url == urls.ro:  # pull writes the class P mirror: the read-only URL is refused
            raise PreconditionError("H1 dev_url_kind_wrong")
    raw = _open(connector, kind == DEV_RO, _libpq(url))
    return SyncConnection(raw, kind, None, None, urls.app_env)


def dev_ro(env: str, *, dev_urls: DevUrls | None = None, connector: Callable[..., Any] = psycopg2.connect) -> SyncConnection:
    """Dev database through DATABASE_URL_READONLY, session read-only (promote, files, verify, doctor)."""
    return _dev(DEV_RO, env, dev_urls, connector)


def dev_rw(env: str, *, dev_urls: DevUrls | None = None, connector: Callable[..., Any] = psycopg2.connect) -> SyncConnection:
    """Dev database through DATABASE_URL (write-capable); only sync.pull uses it."""
    return _dev(DEV_RW, env, dev_urls, connector)


_SECRET_KEYS = {PROD_WRITER: "SYNC_WRITER_PASSWORD", PROD_WRITER_RO: "SYNC_WRITER_PASSWORD", PROD_READER: "SYNC_READER_PASSWORD"}


def _prod(kind: str, settings: SyncSettings, secrets: dict | None, connector: Callable[..., Any]) -> SyncConnection:
    role = str(settings.require("prod.reader_role" if kind == PROD_READER else "prod.writer_role"))
    database = str(settings.require("prod.database"))
    port = int(settings.require("prod.local_port"))
    secrets = secrets if secrets is not None else load_secrets(settings.env, settings.root)
    password = secrets.get(_SECRET_KEYS[kind])
    if not password:
        raise ConfigError(f"{_SECRET_KEYS[kind]} is not set in environments/.env.{settings.env}.sync")
    raw = _open(connector, kind != PROD_WRITER, host=LOOPBACK, port=port, dbname=database, user=role, password=password,
                connect_timeout=15, application_name="memes-sync")
    return SyncConnection(raw, kind, LOOPBACK, port)


def prod_writer(settings: SyncSettings, secrets: dict | None = None, *, connector: Callable[..., Any] = psycopg2.connect):
    return _prod(PROD_WRITER, settings, secrets, connector)


def prod_reader(settings: SyncSettings, secrets: dict | None = None, *, connector: Callable[..., Any] = psycopg2.connect):
    return _prod(PROD_READER, settings, secrets, connector)


def prod_writer_readonly(settings: SyncSettings, secrets: dict | None = None, *, connector: Callable[..., Any] = psycopg2.connect):
    """The writer role in a read-only session (verify opens the class D digests this way)."""
    return _prod(PROD_WRITER_RO, settings, secrets, connector)


# ------------------------------------------------------------------------------------------------ capabilities (H5)

CAPABILITIES_PATH = "/api/diagnostics/capabilities"
_MAX_BODY = 65536


class CapabilitiesError(PreconditionError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def fetch_capabilities(api_url: str, *, timeout: float = 10.0, opener: Callable[..., Any] | None = None) -> dict:
    """GET <api_url>/api/diagnostics/capabilities (public, standard library). https only, except for a loopback host (tests)."""
    parts = urllib.parse.urlsplit(api_url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise CapabilitiesError("api_url_invalid")
    if parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "::1", "localhost"):
        raise CapabilitiesError("api_url_not_https")
    url = api_url.rstrip("/") + CAPABILITIES_PATH
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "memes-sync"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            body = response.read(_MAX_BODY + 1)
    except urllib.error.HTTPError as exc:
        raise CapabilitiesError(f"http_{exc.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise CapabilitiesError("unreachable") from None
    if status != 200:
        raise CapabilitiesError(f"http_{status}")
    if len(body) > _MAX_BODY:
        raise CapabilitiesError("body_too_large")
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise CapabilitiesError("not_json") from None
    if not isinstance(data, dict) or not isinstance(data.get("stage"), str) or not isinstance(data.get("environment"), str):
        raise CapabilitiesError("payload_shape")
    return data  # only `stage` and `environment` are ever compared (interfaces.md f)


# --------------------------------------------------------------------------------------------- role facts (H3)

_WRITE_PRIVS = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")
_ATTRS = ("rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls")
_RATINGS_COLUMNS = {"image_id", "query_hash", "updated_at"}


def gather_role_facts(conn: SyncConnection, env: str) -> dict:
    """Catalog facts about current_user (read-only queries; no row content): attributes, membership in the owner/app/ro roles,
    table privileges for every registry table plus alembic_version, column privileges on meme_text_ratings and images."""
    me, *attrs = conn.query(
        "select current_user, " + ", ".join(_ATTRS) + " from pg_roles where rolname = current_user")[0]
    names = [f"memes_{env}_owner", f"memes_{env}_app", f"memes_{env}_ro"]
    member_of = [r[0] for r in conn.query(
        "select rolname from pg_roles where rolname = any(%s) and pg_has_role(current_user, oid, 'MEMBER')", (names,))]
    owns_images = conn.scalar("select pg_has_role(current_user, c.relowner, 'MEMBER') from pg_class c where c.oid = to_regclass('images')")
    table_priv: dict[str, dict[str, bool | None]] = {}
    for table in [*registry.names(), "alembic_version"]:
        row = conn.query(
            "select to_regclass(%s) is not null, "
            "has_table_privilege(current_user, to_regclass(%s), 'SELECT'), has_table_privilege(current_user, to_regclass(%s), 'INSERT'), "
            "has_table_privilege(current_user, to_regclass(%s), 'UPDATE'), has_table_privilege(current_user, to_regclass(%s), 'DELETE'), "
            "has_table_privilege(current_user, to_regclass(%s), 'TRUNCATE')", (table,) * 6)[0]
        table_priv[table] = {"exists": row[0], "SELECT": row[1], "INSERT": row[2], "UPDATE": row[3], "DELETE": row[4], "TRUNCATE": row[5]}
    col_priv: dict[str, dict[str, bool]] = {}
    for table in ("meme_text_ratings", "images"):
        col_priv[table] = {r[0]: bool(r[1]) for r in conn.query(
            "select a.attname, has_column_privilege(current_user, a.attrelid, a.attnum, 'SELECT') from pg_attribute a "
            "where a.attrelid = to_regclass(%s) and a.attnum > 0 and not a.attisdropped", (table,))}
    return {"user": me, "attrs": dict(zip(_ATTRS, attrs)), "member_of": member_of, "owns_images": bool(owns_images),
            "table_priv": table_priv, "col_priv": col_priv}


def evaluate_role(facts: dict, role: str, expected_user: str) -> list[str]:
    """Problem codes for one role; `role` is `writer` or `reader`. Pure: works on gather_role_facts output."""
    problems: list[str] = []
    if facts["user"] != expected_user:
        problems.append("wrong_current_user")
    problems += [f"role_attr_{a[3:]}" for a, v in facts["attrs"].items() if v]
    if facts["owns_images"]:
        problems.append("member_of_table_owner")
    problems += [f"member_of_{n.rsplit('_', 1)[1]}_role" for n in facts["member_of"]]
    tp, cp = facts["table_priv"], facts["col_priv"]
    d_tables, p_tables, x_tables = (registry.names(k) for k in (Klass.D, Klass.P, Klass.X))
    missing = [t for t, v in tp.items() if not v["exists"]]
    if missing:
        problems.append(f"missing_tables={len(missing)}")
    live = [t for t in tp if tp[t]["exists"]]

    def count(tables, privs, want):
        return sum(1 for t in tables if t in live for p in privs if bool(tp[t][p]) is want)

    if role == "writer":
        bad_write = count(p_tables + x_tables, _WRITE_PRIVS, True)
        if bad_write:
            problems.append(f"write_on_class_p_or_x={bad_write}")
        if count(p_tables + x_tables, ("SELECT",), True):
            problems.append(f"select_on_class_p_or_x={count(p_tables + x_tables, ('SELECT',), True)}")
        lacking = count(d_tables, ("SELECT", "INSERT", "UPDATE", "DELETE"), False)
        if lacking:
            problems.append(f"missing_write_on_class_d={lacking}")
        ratings = cp.get("meme_text_ratings", {})
        if {c for c, v in ratings.items() if v} != _RATINGS_COLUMNS:
            problems.append("ratings_column_select_wrong")
    else:
        if count(p_tables, ("SELECT",), False):
            problems.append(f"missing_select_on_class_p={count(p_tables, ('SELECT',), False)}")
        if count(d_tables + x_tables, ("SELECT",), True):
            problems.append(f"select_on_class_d_or_x={count(d_tables + x_tables, ('SELECT',), True)}")
        if count(registry.names(), _WRITE_PRIVS, True):
            problems.append(f"write_privilege={count(registry.names(), _WRITE_PRIVS, True)}")
        images = cp.get("images", {})
        if {c for c, v in images.items() if v} != {"id"}:
            problems.append("images_column_select_wrong")
    av = tp.get("alembic_version", {})
    if av.get("exists") and not av["SELECT"]:
        problems.append("alembic_version_unreadable")
    if av.get("exists") and any(av[p] for p in _WRITE_PRIVS):
        problems.append("alembic_version_writable")
    return sorted(set(problems))


# ------------------------------------------------------------------------------------------------ handshake (e)

class Transport(Protocol):
    """The gate transport (P3-T4). The handshake only needs `handshake()`, which returns {environment, version, ...}."""

    def handshake(self) -> dict: ...


STEP_TOOLS = {  # interfaces.md e, last column; R and D run what their children need
    "H1": "PFLVRD", "H2": "PLVRD", "H3": "PLVRD", "H4": "PLVRD", "H5": "PLVRD",
    "H6": "FLVRD", "H7": "PVRD", "H8": "PVRD", "H9": "PRD", "H10": "PVRD",
}
TOOLS = "PFLVRD"
_DEV_KIND = {"L": DEV_RW}  # every other tool reads dev through the read-only URL
_PROD_KIND = {"P": PROD_WRITER, "L": PROD_READER, "V": PROD_WRITER_RO}  # R and D take the writer and a reader
_PROD_KINDS_ALLOWED = {"R": (PROD_WRITER, PROD_WRITER_RO), "D": (PROD_WRITER, PROD_WRITER_RO)}
_IDENTITY_SQL = ("select inet_server_addr()::text, inet_server_port(), pg_postmaster_start_time()::text, "
                 "(select oid::bigint from pg_database where datname = current_database())")


def _git_sha(root: Path = REPO_ROOT) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip().lower() if out.returncode == 0 and out.stdout.strip() else None


def _identity(conn: SyncConnection) -> tuple:
    row = conn.query(_IDENTITY_SQL)[0]
    try:
        system_id = conn.scalar("select system_identifier::text from pg_control_system()")
    except DbError:
        system_id = None  # not allowed for this role: the other four facts remain
    return (*row, system_id)


def _alembic(conn: SyncConnection) -> tuple:
    return tuple(sorted(r[0] for r in conn.query("select version_num from alembic_version")))


def _setting(conn: SyncConnection, name: str) -> str | None:
    value = conn.scalar("select current_setting(%s, true)", (name,))
    return value if value else None


def handshake(env: str, tool: str, mode: str = "normal", *, settings: SyncSettings, dev: SyncConnection,
              prod: SyncConnection | None = None, prod_reader_conn: SyncConnection | None = None,
              capabilities: Callable[[str], dict] | None = None, transport: Transport | None = None,
              git_sha: str | None = None, fail_fast: bool = True) -> list[dict]:
    """Run the steps of interfaces.md e for `tool` (P, F, L, V, R or D). Returns the `{step, ok, detail}` list.

    With fail_fast (default) the first failure raises HandshakeError (a PreconditionError, exit 2, one-line reason, `.results`
    holding the steps so far); with fail_fast=False every step runs (doctor). `mode` is `normal` or `first-seed` (H9).
    `capabilities` is a callable api_url -> payload (default fetch_capabilities); `transport` supplies the gate step H6.
    """
    if tool not in TOOLS:
        raise ValueError(f"unknown tool {tool!r}")
    if mode not in ("normal", "first-seed"):
        raise ValueError(f"unknown mode {mode!r}")
    results: list[dict] = []

    def record(step: str, ok: bool, detail: str) -> None:
        results.append({"step": step, "ok": ok, "detail": detail})
        if not ok and fail_fast:
            raise HandshakeError(f"handshake {step} failed: {detail}", list(results))

    def run(step: str, fn: Callable[[], tuple[bool, str]]) -> None:
        if tool not in STEP_TOOLS[step]:
            return
        try:
            ok, detail = fn()
        except DbError as exc:
            ok, detail = False, exc.code
        except CapabilitiesError as exc:
            ok, detail = False, f"capabilities:{exc.code}"
        except (ConfigError, KeyError) as exc:
            ok, detail = False, "config_missing" if isinstance(exc, ConfigError) else "internal_missing_key"
        record(step, ok, detail)

    def codes(problems: list[str]) -> tuple[bool, str]:
        return (not problems, ",".join(problems) if problems else "ok")

    def h1():
        problems = []
        if dev.app_env != env:
            problems.append("app_env_mismatch")
        if dev.kind != _DEV_KIND.get(tool, DEV_RO):
            problems.append("dev_url_kind_wrong")
        if dev.query("select current_database()")[0][0] != str(settings.require("dev.database")):
            problems.append("dev_db_mismatch")
        if _setting(dev, "memes.environment") is not None:
            problems.append("dev_has_memes_environment")
        return codes(problems)

    def prod_conn() -> SyncConnection:
        if prod is None:
            raise PreconditionError("prod connection missing")
        return prod

    def h2():
        problems = []
        conn = prod_conn()
        if (conn.host, conn.port) != (LOOPBACK, int(settings.require("prod.local_port"))):
            problems.append("not_via_tunnel")
        if conn.query("select current_database()")[0][0] != str(settings.require("prod.database")):
            problems.append("prod_db_mismatch")
        return codes(problems)

    def h3():
        conn = prod_conn()
        allowed = _PROD_KINDS_ALLOWED.get(tool, (_PROD_KIND.get(tool),))
        problems = [] if conn.kind in allowed else ["prod_connection_kind_wrong"]
        role_kind = "reader" if conn.kind == PROD_READER else "writer"
        problems += evaluate_role(gather_role_facts(conn, env), role_kind, str(settings.require(f"prod.{role_kind}_role")))
        if tool in "RD":  # these run the children of both: the reader as well
            if prod_reader_conn is None:
                problems.append("reader_connection_missing")
            else:
                problems += [f"reader:{p}" for p in evaluate_role(gather_role_facts(prod_reader_conn, env), "reader",
                                                                  str(settings.require("prod.reader_role")))]
        return codes(problems)

    def h4():
        return (True, "ok") if _setting(prod_conn(), "memes.environment") == env else (False, "prod_environment_mismatch")

    def h5():
        payload = (capabilities or fetch_capabilities)(str(settings.require("prod.api_url")))
        problems = []
        if payload.get("environment") != env:
            problems.append("environment_mismatch")
        if payload.get("stage") != "prod":
            problems.append("stage_not_prod")
        return codes(problems)

    def h6():
        if transport is None:
            if tool == "D":  # the gate transport is built by P3-T4; the doctor reports the step as skipped
                return True, "skipped:no_transport"
            return False, "transport_missing"
        reply = transport.handshake()
        return (True, "ok") if isinstance(reply, dict) and reply.get("environment") == env else (False, "gate_environment_mismatch")

    def h7():
        same = _identity(dev) == _identity(prod_conn())
        return (False, "same_server") if same else (True, "ok")

    def h8():
        conn = prod_conn()
        ids = [r[0] for r in conn.query("select id::text from images")]
        if not ids:
            return True, "prod_empty"
        found = 0
        for i in range(0, len(ids), 5000):
            found += dev.scalar("select count(*) from images where id = any(%s::uuid[])", (ids[i:i + 5000],))
        return (True, f"overlap={found}/{len(ids)}") if found * 2 >= len(ids) else (False, f"overlap_low={found}/{len(ids)}")

    def h9():
        want = "seeding" if mode == "first-seed" else "serving"
        state = _setting(prod_conn(), "memes.state")
        return (True, "ok") if state == want else (False, f"state_{state or 'unset'}_want_{want}")

    def h10():
        problems = []
        if _alembic(dev) != _alembic(prod_conn()):
            problems.append("alembic_version_differs")
        sha = git_sha if git_sha is not None else _git_sha()
        revision = _setting(prod_conn(), "memes.revision")
        if not sha:
            problems.append("dev_git_sha_unknown")
        elif (revision or "").strip().lower() != sha.strip().lower():
            problems.append("revision_unset" if revision is None else "revision_mismatch")
        return codes(problems)

    for step, fn in (("H1", h1), ("H2", h2), ("H3", h3), ("H4", h4), ("H5", h5), ("H6", h6), ("H7", h7), ("H8", h8),
                     ("H9", h9), ("H10", h10)):
        run(step, fn)
    return results


def banner(env: str, settings: SyncSettings, dev_db: str, writer_role: str | None = None, read_only: bool = True) -> str:
    role = writer_role or str(settings.get("prod.writer_role"))
    return (f"dev {env} ({dev_db} @ localhost, {'read-only' if read_only else 'read-write'}) -> prod "
            f"{settings.get('prod.database')} as {role} via ssh alias {settings.get('prod.ssh_alias')}")


@contextlib.contextmanager
def open_session(env: str, tool: str, mode: str = "normal", *, settings: SyncSettings, dev_urls: DevUrls | None = None,
                 secrets: dict | None = None, open_tunnel_fn: Callable[..., Any] | None = None,
                 connector: Callable[..., Any] = psycopg2.connect) -> Iterator[dict]:
    """Open the tunnel and the connections `tool` needs; yields {dev, prod, prod_reader_conn} and closes everything on exit.
    The caller holds the state-directory lock (sync.config.exclusive_lock) and runs `handshake` with these."""
    from sync.tunnel import open_tunnel

    with contextlib.ExitStack() as stack:
        stack.enter_context((open_tunnel_fn or open_tunnel)(settings))
        dev = stack.enter_context(_dev(_DEV_KIND.get(tool, DEV_RO), env, dev_urls, connector))
        prod_kind = _PROD_KIND.get(tool, PROD_WRITER)
        factory = {PROD_WRITER: prod_writer, PROD_READER: prod_reader, PROD_WRITER_RO: prod_writer_readonly}[prod_kind]
        prod = stack.enter_context(factory(settings, secrets, connector=connector)) if tool != "F" else None
        reader = stack.enter_context(prod_reader(settings, secrets, connector=connector)) if tool in "RD" else None
        yield {"dev": dev, "prod": prod, "prod_reader_conn": reader}
