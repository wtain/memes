"""P6 runtime checks: P1 (pull freshness), A1 (growth), A3 (disk), S1 (grants), S2 (roles and pg_hba), S4 (gate), S5 (connections and
indexes), S6 (backup), S8 (containment) (design P6 "Checks").

Reading is split from judging. The readers use catalog queries and the class P counts (through `sync_reader`); every query is a
SELECT. The facts of the gate verbs `disk` and `containment` are JSON files that a root timer on the VPS writes (interfaces.md g); their
exact keys are not fixed by any design, so the shapes this module understands are written down in `FACTS` below and a file of another shape
makes the check `skipped: facts_unusable`, never a false green.
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from sync import connect, registry
from sync.model import Klass
from sync.verify import growth, model
from sync.verify.model import Check, fail, ok, skipped, token

FIX_PULL = "python -m sync.pull --env <env> --apply"
FIX_GROWTH = "owner: read prod-abuse-and-growth.md, block the source at Caddy"
FIX_DISK = "owner: free space on the named volume (logs, dumps; never BASE_PATH)"
FIX_GRANTS = "python -m sync.roles --env <env>, apply the roles SQL as the owner role"
FIX_CONN = "owner: look at pg_stat_activity of the stack, raise max_connections or fix the leak"
FIX_INDEX = "owner: rebuild the index as the owner role (REINDEX / CREATE INDEX)"
FIX_BACKUP = "owner: run backup.sh for this environment on the VPS"
FIX_CONTAIN = "owner: fix compose.yaml of the stack and redeploy the backend"

# Log bounds of the observability design (section 6), the same figures the plan's Global Constraints state. Used by A3 and its tests.
# backend: two worker slots (backend-w0/w1.log) of 10 MiB x (4 backups + the current file) = 100 MiB, plus the gunicorn master
# (backend-master.log) of 5 MiB x (2 backups + the current file) = 15 MiB: 115 MiB per environment.
_MIB = 1024 * 1024
BACKEND_LOG_BOUND_BYTES = 2 * 10 * _MIB * (4 + 1) + 5 * _MIB * (2 + 1)
# postgres: deploy/prod/postgres/prune_logs.py MAX_BYTES (a test keeps the two equal).
POSTGRES_LOG_BOUND_BYTES = 200 * _MIB
# caddy: roll_size 10MiB x (roll_keep 5 + the current file) = 60 MiB (deploy/prod/edge/Caddyfile).
CADDY_LOG_BOUND_BYTES = 10 * _MIB * (5 + 1)
LOG_BOUNDS = {"backend_bytes": BACKEND_LOG_BOUND_BYTES, "postgres_bytes": POSTGRES_LOG_BOUND_BYTES, "caddy_bytes": CADDY_LOG_BOUND_BYTES}
LOG_WARN_FRACTION = 0.8

GROWTH_TABLES = ("meme_impressions", "search_history", "search_not_found", "meme_saves")
FACTS = {
    "disk.json": "{ \"<mount>\": {\"free_pct\": n} } or { \"mounts\": { \"<mount>\": {\"free_pct\": n | \"free_bytes\", \"total_bytes\"} } }",
    "containment.json": "{read_only_root: bool, cap_drop: [..], no_new_privileges: bool, user: str, base_path_read_only: bool, "
                        "published_ports: [str]}",
}


# ---------------------------------------------------------------------------------------------------------------------------------
# P1: pull freshness

def check_p1(doc: dict | None, status: str, now: _dt.datetime, stale_days: float = 7) -> Check:
    """Age of `pull_state.json`, its run id, the last bug-report outcome and the ratings mode. Informational beyond the age warning:
    a stale pull is a warning, never a difference."""
    if doc is None:
        return fail("P1", "warn", f"pull_state={status}", FIX_PULL)
    at = growth.parse_time(doc.get("finished_at"))
    if at is None:
        return fail("P1", "warn", "finished_at_unreadable", FIX_PULL)
    age_d = max((now - at).total_seconds() / 86400.0, 0.0)
    detail = (f"age_d={age_d:.1f},ratings={token((doc.get('ratings') or {}).get('mode'))},"
              f"bug={token((doc.get('bug_reports') or {}).get('status'))},run={str(doc.get('run_id', '')).lower()}")
    return fail("P1", "warn", detail + ",stale", FIX_PULL) if age_d > stale_days else ok("P1", "warn", detail)


# ---------------------------------------------------------------------------------------------------------------------------------
# A1: growth of the reader tables

def class_p_counts(conn: Any) -> dict:
    """Exact row counts of the class P tables through a connection that may SELECT them (dev read-only, prod `sync_reader`)."""
    return {t: int(conn.scalar(f'select count(*) from "{t}"')) for t in registry.names(Klass.P)}


def table_bytes(conn: Any, tables: tuple | list) -> dict:
    """`pg_total_relation_size` per table (a catalog function that needs no privilege on the table)."""
    return {t: int(conn.scalar("select coalesce(pg_total_relation_size(to_regclass(%s)), 0)", (t,)) or 0) for t in tables}


def growth_metrics(prod_counts: dict | None, sizes: dict | None, bug: dict | None) -> dict:
    out: dict = {}
    for t in GROWTH_TABLES:
        if prod_counts is not None and t in prod_counts:
            out[f"{t}.rows"] = prod_counts[t]
        if sizes is not None and t in sizes:
            out[f"{t}.bytes"] = sizes[t]
    if bug is not None:
        out["bug_reports.files"] = bug["files"]
        out["bug_reports.bytes"] = bug["bytes"]
    return out


def check_a1(trend: dict) -> Check:
    metrics = trend["trend"]
    if not metrics:
        return skipped("A1", "warn", "no_metrics")
    hard = sorted(k for k, v in metrics.items() if v["status"] == growth.GROWTH_ERROR)
    alarms = sorted(k for k, v in metrics.items() if v["status"] == growth.GROWTH_WARN)
    building = all(v["status"] == growth.BUILDING for v in metrics.values())
    detail = f"metrics={len(metrics)},history={trend['samples']},alarms={len(alarms)},ceiling={len(hard)}" + (",building" if building else "")
    first = (hard or alarms)[:1]
    if first:
        detail += ",first=" + token(first[0])
    if hard:
        return fail("A1", "error", detail, FIX_GROWTH)
    if alarms:
        return fail("A1", "warn", detail, FIX_GROWTH)
    return ok("A1", "warn", detail)


# ---------------------------------------------------------------------------------------------------------------------------------
# A3: disk per mount (gate verb `disk`)

def _free_pct(mount: Any) -> float | None:
    if not isinstance(mount, dict):
        return None
    pct = mount.get("free_pct")
    if isinstance(pct, (int, float)) and not isinstance(pct, bool):
        return float(pct)
    free, total = mount.get("free_bytes"), mount.get("total_bytes")
    if isinstance(free, (int, float)) and isinstance(total, (int, float)) and total > 0:
        return 100.0 * free / total
    return None


def _log_over(facts: Any) -> list | None:
    """Names (backend, postgres, caddy) of the log directories above 80% of their bound; [] when the facts carry no `logs` (older
    writer); None when `logs` is present but not usable."""
    logs = facts.get("logs") if isinstance(facts, dict) else None
    if logs is None:
        return []
    if not isinstance(logs, dict):
        return None
    over = []
    for key, bound in LOG_BOUNDS.items():
        size = logs.get(key)
        if not isinstance(size, (int, float)) or isinstance(size, bool) or size < 0:
            return None
        if size > LOG_WARN_FRACTION * bound:
            over.append(key[:-len("_bytes")])
    return over


def check_a3(facts: Any, warn_pct: float = 20, error_pct: float = 10) -> Check:
    """Free space per mount (warn / error) and, as a warning, any log directory above 80% of its bound (`logs` of disk.json)."""
    log_over = _log_over(facts)  # None = malformed `logs`; judged only after the mounts, so it cannot hide a low disk
    mounts = facts.get("mounts") if isinstance(facts, dict) and isinstance(facts.get("mounts"), dict) else facts
    if not isinstance(mounts, dict) or not mounts:
        return skipped("A3", "error", "facts_unusable")
    pct = {token(name): _free_pct(m) for name, m in mounts.items()}
    if any(v is None for v in pct.values()):
        return skipped("A3", "error", "facts_unusable")
    low_err = sorted(n for n, v in pct.items() if v < error_pct)
    low_warn = sorted(n for n, v in pct.items() if error_pct <= v < warn_pct)
    if log_over is None and not low_err:
        return skipped("A3", "error", "facts_unusable")
    log_over = log_over or []
    detail = f"mounts={len(pct)},min_free_pct={min(pct.values()):.1f}"
    if log_over:
        detail += ",logs_over_80pct=" + ".".join(log_over)
    if low_err:
        return fail("A3", "error", detail + ",error=" + ".".join(low_err[:3]), FIX_DISK)
    if low_warn:
        return fail("A3", "warn", detail + ",low=" + ".".join(low_warn[:3]), FIX_DISK)
    if log_over:
        return fail("A3", "warn", detail, FIX_DISK)
    return ok("A3", "error", detail)


# ---------------------------------------------------------------------------------------------------------------------------------
# S1: grants

def gather_app_facts(conn: Any, app_role: str) -> dict:
    """What `backend_app` may do, read from the catalog by role name (`has_table_privilege(<name>, ...)` answers for another role)."""
    attrs = conn.query("select rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls from pg_roles where rolname = %s", (app_role,))
    if not attrs:
        raise connect.DbError("42704", kind="UndefinedObject")
    table_priv = {}
    for table in [*registry.names(), "alembic_version"]:
        row = conn.query("select to_regclass(%s) is not null, " + ", ".join(
            f"coalesce(has_table_privilege(%s, to_regclass(%s), '{p}'), false)" for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")),
            (table, *[x for _ in range(5) for x in (app_role, table)]))[0]
        table_priv[table] = dict(zip(("exists", "SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"), row))
    return {"attrs": dict(zip(("super", "createrole", "createdb", "replication", "bypassrls"), attrs[0])), "table_priv": table_priv,
            "schema_create": bool(conn.scalar("select has_schema_privilege(%s, current_schema(), 'CREATE')", (app_role,)))}


def evaluate_app(facts: dict) -> list:
    """Problem codes for `backend_app`: SELECT on every class D table and no write there, SELECT/INSERT/UPDATE/DELETE on exactly the six
    class P tables, nothing on class X and `alembic_version`, no role attribute, no CREATE on the schema."""
    problems = [f"role_attr_{a}" for a, v in facts["attrs"].items() if v]
    if facts["schema_create"]:
        problems.append("schema_create")
    tp = facts["table_priv"]
    live = {t for t, v in tp.items() if v["exists"]}
    writes = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")

    def n(tables, privs, want):
        return sum(1 for t in tables if t in live for p in privs if bool(tp[t][p]) is want)

    d, p, x = registry.names(Klass.D), registry.names(Klass.P), registry.names(Klass.X)
    for label, count in (("missing_select_on_class_d", n(d, ("SELECT",), False)), ("write_on_class_d", n(d, writes, True)),
                         ("missing_write_on_class_p", n(p, ("SELECT", "INSERT", "UPDATE", "DELETE"), False)),
                         ("truncate_on_class_p", n(p, ("TRUNCATE",), True)),
                         ("privilege_on_class_x", n(x, ("SELECT", *writes), True))):
        if count:
            problems.append(f"{label}={count}")
    if tp.get("alembic_version", {}).get("exists") and any(tp["alembic_version"][k] for k in ("SELECT", *writes)):
        problems.append("alembic_version_access")
    return problems


def check_s1(writer: list | None, reader: list | None, app: list | None) -> Check:
    """`writer`, `reader` and `app` are problem lists (None: not examined). Any problem is an error."""
    parts = {"writer": writer, "reader": reader, "app": app}
    if all(v is None for v in parts.values()):
        return skipped("S1", "error", "no_connections")
    bad = {k: v for k, v in parts.items() if v}
    examined = [k for k, v in parts.items() if v is not None]
    if bad:
        first = sorted(next(iter(bad.values())))[0]
        return fail("S1", "error", "roles_bad=" + ".".join(sorted(bad)) + ",first=" + token(first) + ",n=" + str(sum(len(v) for v in bad.values())),
                    FIX_GRANTS)
    return ok("S1", "error", "roles=" + ".".join(examined))


# ---------------------------------------------------------------------------------------------------------------------------------
# S2: roles and pg_hba

_PUBLIC_CONNECT_SQL = ("select exists (select 1 from pg_database d, aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a "
                       "where d.datname = current_database() and a.grantee = 0 and a.privilege_type = 'CONNECT')")
_TRUST_SQL = ("select count(*) from pg_hba_file_rules where error is null and auth_method = 'trust' "
              "and type in ('host', 'hostssl', 'hostnossl', 'hostgssenc', 'hostnogssenc')")


def gather_s2(conn: Any, app_facts: dict | None) -> dict:
    """`{public_connect, trust_network (None when pg_hba_file_rules may not be read), app_super, app_create}`."""
    public = bool(conn.scalar(_PUBLIC_CONNECT_SQL))
    try:
        trust = int(conn.scalar(_TRUST_SQL))
    except connect.DbError as exc:
        if exc.sqlstate != "42501":
            raise
        trust = None
    return {"public_connect": public, "trust_network": trust,
            "app_super": bool(app_facts and app_facts["attrs"]["super"]), "app_create": bool(app_facts and app_facts["schema_create"])}


def check_s2(facts: dict) -> Check:
    problems = []
    if facts["public_connect"]:
        problems.append("public_connect")
    if facts["trust_network"]:
        problems.append(f"trust_network={facts['trust_network']}")
    if facts["app_super"]:
        problems.append("app_superuser")
    if facts["app_create"]:
        problems.append("app_can_create")
    if problems:
        return fail("S2", "error", ",".join(problems), FIX_GRANTS)
    return ok("S2", "error", "pg_hba=" + ("unreadable" if facts["trust_network"] is None else "checked"))


# ---------------------------------------------------------------------------------------------------------------------------------
# S5: connections and indexes

_INDEX_SQL = ("select c.relname, i.indisvalid from pg_index i join pg_class c on c.oid = i.indexrelid join pg_am am on am.oid = c.relam "
              "where am.amname in ('hnsw', 'gin', 'ivfflat') and c.relnamespace = (select oid from pg_namespace where nspname = current_schema()) "
              "order by 1")


def gather_indexes(conn: Any) -> dict:
    return {str(n): bool(v) for n, v in conn.query(_INDEX_SQL)}


def gather_connections(conn: Any) -> tuple:
    used = int(conn.scalar("select count(*) from pg_stat_activity where datname = current_database()"))
    return used, int(conn.scalar("select current_setting('max_connections')::int"))


def check_s5(used: int, limit: int, dev_indexes: dict, prod_indexes: dict, seeding: bool = False) -> Check:
    problems, warns = [], []
    pct = 100.0 * used / limit if limit else 0.0
    if pct >= 95:
        problems.append(f"connections_pct={pct:.0f}")
    elif pct >= 80:
        warns.append(f"connections_pct={pct:.0f}")
    missing = sorted(set(dev_indexes) - set(prod_indexes))
    invalid = sorted(n for n, valid in prod_indexes.items() if not valid)
    if missing and not seeding:
        problems.append(f"indexes_missing={len(missing)}")
    if invalid:
        problems.append(f"indexes_invalid={len(invalid)}")
    base = f"conn={used}:{limit},indexes={len(prod_indexes)}" + (",seeding" if seeding and missing else "")
    if problems:
        return fail("S5", "error", base + "," + ",".join(problems), FIX_INDEX if (missing or invalid) else FIX_CONN)
    if warns:
        return model.Check("S5", model.WARN, "error", base + "," + ",".join(warns), FIX_CONN)
    return ok("S5", "error", base)


# ---------------------------------------------------------------------------------------------------------------------------------
# S4: gate facts; S6: backup; S8: containment

def check_s4(reply: dict, expected_version: str) -> Check:
    problems = []
    if reply.get("version") != expected_version:
        problems.append("gate_version_differs")
    devices = reply.get("devices") if isinstance(reply.get("devices"), dict) else {}
    if not devices or devices.get("base") != devices.get("work"):
        problems.append("device_mismatch")
    if problems:
        return fail("S4", "error", ",".join(problems), "owner: install memesync-gate.py from the repo on the VPS")
    return ok("S4", "error", "gate_version=" + token(expected_version) + ",devices=equal")


def check_s4b(probe: dict | None) -> Check:
    """The two ssh accounts as designed: attempts that must fail. They need a real sshd: with no probe the check is skipped, with a
    reason. `probe` is `{upload_refuses_verb, upload_refuses_shell, upload_chrooted, verb_account_no_sftp}` (all booleans, True = as designed)."""
    if probe is None:
        return skipped("S4b", "error", "account_probe_unavailable")
    bad = sorted(k for k, v in probe.items() if v is not True)
    return fail("S4b", "error", "accounts_bad=" + ".".join(token(b) for b in bad[:3]), "owner: fix sshd Match User and authorized_keys") if bad \
        else ok("S4b", "error", "accounts=as_designed")


def check_s6(info: dict | None, now: _dt.datetime, max_age_h: float = 36, prev: dict | None = None, rows: int | None = None) -> Check:
    """`info` is the header of `fetch-backup --meta`: `{name, size, mtime, meta}`; `prev` the earlier run's `{size, rows}`."""
    if info is None:
        return skipped("S6", "warn", "no_backup")
    problems = []
    size = info.get("size")
    if not isinstance(size, int) or size <= 0:
        problems.append("empty")
    mtime = info.get("mtime")
    age_h = (now.timestamp() - mtime) / 3600.0 if isinstance(mtime, (int, float)) else None
    if age_h is None or age_h > max_age_h:
        problems.append("too_old" if age_h is not None else "age_unknown")
    meta = info.get("meta")
    if not isinstance(meta, dict) or meta.get("pg_restore_list") != "ok":
        problems.append("restore_list_not_ok" if isinstance(meta, dict) else "restore_check_missing")
    if prev and isinstance(size, int) and prev.get("size"):
        row_ratio = (rows / prev["rows"]) if rows is not None and prev.get("rows") else 1.0
        if size < prev["size"] * min(row_ratio, 1.0) * 0.8:
            problems.append("smaller_than_rows_lost")
    detail = f"size={size},age_h={age_h:.0f}" if age_h is not None and isinstance(size, int) else "unreadable"
    return fail("S6", "warn", detail + "," + ",".join(problems), FIX_BACKUP) if problems else ok("S6", "warn", detail)


def check_s8(facts: Any) -> Check:
    if not isinstance(facts, dict) or not facts:
        return skipped("S8", "error", "facts_unusable")
    problems = []
    for key in ("read_only_root", "no_new_privileges", "base_path_read_only"):
        if facts.get(key) is not True:
            problems.append(key + ("_missing" if key not in facts else "_off"))
    caps = facts.get("cap_drop")
    if not isinstance(caps, list) or "ALL" not in [str(c).upper() for c in caps]:
        problems.append("cap_drop_all_missing")
    user = str(facts.get("user", "")).strip().lower()
    if user in ("", "0", "root") or user.startswith(("0:", "root:")):
        problems.append("runs_as_root")
    ports = facts.get("published_ports", [])
    if not isinstance(ports, list) or any(not str(p).startswith(("127.0.0.1:", "[::1]:", "localhost:")) for p in ports):
        problems.append("port_not_loopback")
    if problems:
        return fail("S8", "error", ",".join(problems), FIX_CONTAIN)
    return ok("S8", "error", "contained")


def parse_json_reply(out: bytes) -> Any:
    """The JSON a facts verb printed (one document); raises ValueError when it is not."""
    return json.loads(out.decode("utf-8").strip().splitlines()[0])

