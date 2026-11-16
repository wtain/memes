"""P2-T4: phase 0 of `sync.promote`, the preconditions (design P2 "Phase 0"; read-only, fail closed, exit 2).

Every function takes already-open connections (`SyncConnection`, `.query` / `.scalar`) and returns a result
`{id, ok, detail}` for the report (`detail` is a lower-case code, at most 80 characters; no row content). H10 (equal
`alembic_version` and `memes.revision`) is a handshake step and is not repeated here. The deferred retire preconditions (pull
freshness, ratings guard) do not exist (decision 27).

Not here, because they need more than a connection: the state-directory lock (taken by the command), the files gate (see
`sync.promote.files_gate`, evaluated by the command because it needs the manifest hash and the clock).
"""
from __future__ import annotations

from typing import Any

from sync import registry
from sync.model import PROMOTABLE_IMAGES_SQL, Klass
from sync.promote import digest as dg

# pg_try_advisory_lock key of a promote on one prod database ("memeprom"); keys are per database, so environments do not collide.
PROMOTE_LOCK_KEY = int.from_bytes(b"memeprom", "big")
SYNC_APPLICATION_NAME = "memes-sync"
EXTENSIONS = ("pg_trgm", "vector")
MAX_DETAIL = 80


def result(check_id: str, ok: bool, detail: str = "") -> dict:
    return {"id": check_id, "ok": bool(ok), "detail": detail[:MAX_DETAIL]}


def _codes(problems: list) -> tuple:
    return (not problems, ",".join(problems) if problems else "ok")


# ---------------------------------------------------------------------------------------------------------------------------------
# session settings

def session_settings(dev: Any, prod: Any) -> dict:
    """Pin the digest settings on both sessions and read them back (a mismatch is exit 2)."""
    problems = [f"{side}_{name}" for side, conn in (("dev", dev), ("prod", prod)) for name in dg.pin_session(conn)]
    ok, detail = _codes(problems)
    return result("session_settings", ok, detail)


# ---------------------------------------------------------------------------------------------------------------------------------
# schema parity

_EXTENSIONS_SQL = "select extname, extversion from pg_extension where extname = any(%s) order by 1"
_VECTOR_DIMS_SQL = (
    "select c.relname, a.attname, a.atttypmod from pg_attribute a join pg_class c on c.oid = a.attrelid "
    "join pg_type t on t.oid = a.atttypid where t.typname = 'vector' and c.relkind = 'r' and a.attnum > 0 and not a.attisdropped "
    "and c.relnamespace = (select oid from pg_namespace where nspname = current_schema()) order by 1, 2"
)


def schema_parity(dev: Any, prod: Any) -> dict:
    """Equal server major version, `vector` and `pg_trgm` versions, UTF8 server encoding, equal vector dimensions."""
    problems = []
    major = [int(c.scalar("select current_setting('server_version_num')::int")) // 10000 for c in (dev, prod)]
    if major[0] != major[1]:
        problems.append("server_major_differs")
    ext = [[tuple(r) for r in c.query(_EXTENSIONS_SQL, (list(EXTENSIONS),))] for c in (dev, prod)]
    if ext[0] != ext[1] or {name for name, _ in ext[0]} != set(EXTENSIONS):
        problems.append("extension_versions_differ")
    if any(str(c.scalar("select current_setting('server_encoding')")).upper() != "UTF8" for c in (dev, prod)):
        problems.append("server_encoding_not_utf8")
    dims = [[tuple(r) for r in c.query(_VECTOR_DIMS_SQL)] for c in (dev, prod)]
    if dims[0] != dims[1]:
        problems.append("vector_dimensions_differ")
    ok, detail = _codes(problems)
    return result("schema_parity", ok, detail)


_COLUMNS_SQL = (
    "select table_name, column_name, data_type, udt_name, is_nullable from information_schema.columns "
    "where table_schema = current_schema() and table_name = any(%s) order by 1, 2"
)


def column_parity(dev: Any, prod: Any, model: dict | None = None) -> dict:
    """For every class D table the `(column, type, nullability)` list is equal on both sides and the column names are the model's.
    `model` (table -> sorted column names) is injectable for tests; the default reads `Storage.models`."""
    tables = registry.names(Klass.D)
    cols = [[tuple(r) for r in c.query(_COLUMNS_SQL, (tables,))] for c in (dev, prod)]
    problems = []
    differing = sorted({r[0] for r in set(cols[0]) ^ set(cols[1])})
    if differing:
        problems.append("dev_prod_differ=" + ",".join(differing))
    if model is None:
        model = {t: [n for n, _ in dg.model_columns(t)] for t in tables}
    for side, rows in (("dev", cols[0]), ("prod", cols[1])):
        have: dict = {}
        for table, column, *_ in rows:
            have.setdefault(table, []).append(column)
        wrong = [t for t in tables if sorted(have.get(t, [])) != sorted(model[t])]
        if wrong:
            problems.append(f"{side}_not_model=" + ",".join(wrong))
    ok, detail = _codes(problems)
    return result("column_parity", ok, detail)


# ---------------------------------------------------------------------------------------------------------------------------------
# model parity

def _promotable_profile_sql(columns: str) -> str:
    return (f"select distinct {columns} from meme_profile_situation_embeddings e "
            f"join images on images.id = e.image_id where {PROMOTABLE_IMAGES_SQL}")


def _model_state(dev: Any, prod: Any) -> tuple:
    """`(dev model pairs, prod model pairs, dev taxonomy versions, prod taxonomy versions)` as sets."""
    dev_pairs = {tuple(r) for r in dev.query(_promotable_profile_sql("e.model_name, e.model_digest"))}
    prod_pairs = {tuple(r) for r in prod.query("select distinct model_name, model_digest from meme_profile_situation_embeddings")}
    sql_dev = ("select distinct p.taxonomy_version from meme_profiles p join images on images.id = p.image_id "
               f"where {PROMOTABLE_IMAGES_SQL}")
    dev_tax = {r[0] for r in dev.query(sql_dev)}
    prod_tax = {r[0] for r in prod.query("select distinct taxonomy_version from meme_profiles")}
    return dev_pairs, prod_pairs, dev_tax, prod_tax


def model_parity(dev: Any, prod: Any) -> dict:
    """The promotable rows carry one consistent set: exactly one `(model_name, model_digest)` among the promotable situation
    embeddings and one `taxonomy_version` (a promote never mixes models). A difference from prod is not checked here: it is
    `model_pair_change` and the `model_change` guard."""
    dev_pairs, _, dev_tax, _ = _model_state(dev, prod)
    problems = []
    if len(dev_pairs) > 1:
        problems.append(f"dev_model_pairs={len(dev_pairs)}")
    if len(dev_tax) > 1:
        problems.append(f"dev_taxonomy_versions={len(dev_tax)}")
    ok, detail = _codes(problems)
    return result("model_parity", ok, detail)


MODEL_CHANGE_ID = "model_pair_change"


def model_pair_change(dev: Any, prod: Any) -> dict:
    """Informational (always ok): does the model pair of the profile embeddings or the taxonomy version on dev differ from prod's?
    Detail `profile_embedding=changed|same,taxonomy=changed|same`. An empty side has nothing to differ from, so it counts as `same`.
    CLIP, SBERT and note vectors carry no marker and are not covered (their parity is the revision check). The affected rows are
    overwrites by digest; the plan's `model_change` guard is what asks for `--allow-model-change`."""
    dev_pairs, prod_pairs, dev_tax, prod_tax = _model_state(dev, prod)
    emb = "changed" if dev_pairs and prod_pairs and dev_pairs != prod_pairs else "same"
    tax = "changed" if dev_tax and prod_tax and dev_tax != prod_tax else "same"
    return result(MODEL_CHANGE_ID, True, f"profile_embedding={emb},taxonomy={tax}")


def model_changed(pre: list) -> bool:
    """True when the `model_pair_change` precondition of a run reports a difference."""
    return any(p["id"] == MODEL_CHANGE_ID and "=changed" in p["detail"] for p in pre)


# ---------------------------------------------------------------------------------------------------------------------------------
# running batches (C1)

RUNNING_BATCHES_SQL = "select count(*) from batch_runs where status = 'started' and kind <> 'ingestion'"


def running_batches(dev: Any, ignore: bool = False) -> dict:
    """No dev batch job is running (the long-lived `ingestion` review row excepted). `--ignore-running-batches` lifts it and
    the report says so."""
    running = int(dev.scalar(RUNNING_BATCHES_SQL) or 0)
    if running and ignore:
        return result("running_batches", True, f"ignored={running}")
    return result("running_batches", running == 0, f"running={running}" if running else "ok")


# ---------------------------------------------------------------------------------------------------------------------------------
# prod lock

def try_prod_lock(prod: Any) -> dict:
    """`pg_try_advisory_lock` on the prod database (held by this session until `release_prod_lock` or the connection closes), plus
    an informational count of other sync sessions on the database (`pg_stat_activity` filtered by `datname`)."""
    got = bool(prod.scalar("select pg_try_advisory_lock(%s)", (PROMOTE_LOCK_KEY,)))
    others = int(prod.scalar("select count(*) from pg_stat_activity where datname = current_database() and application_name = %s "
                             "and pid <> pg_backend_pid()", (SYNC_APPLICATION_NAME,)) or 0)
    if not got:
        return result("prod_lock", False, f"held,other_sync_sessions={others}")
    return result("prod_lock", True, f"other_sync_sessions={others}")


def release_prod_lock(prod: Any) -> None:
    try:
        prod.scalar("select pg_advisory_unlock(%s)", (PROMOTE_LOCK_KEY,))
    except Exception:  # the lock goes with the session anyway
        pass


# ---------------------------------------------------------------------------------------------------------------------------------
# first seed: indexes the owner rebuilds (design "First seed and index state", H7)

REBUILD_ID = "rebuild_indexes"
_REBUILD_METHODS = ("hnsw", "gin")
_PROD_INDEXES_SQL = "select indexname from pg_indexes where schemaname = current_schema()"


def rebuildable_indexes() -> dict:
    """`{table: [index names]}` of the HNSW and GIN indexes `Storage.models` declares on class D tables: the indexes a first seed
    runs without and the owner builds afterwards (the sync role cannot do DDL, decision 8)."""
    from Storage.models import Base

    out: dict = {}
    for table in registry.names(Klass.D):
        names = sorted(ix.name for ix in Base.metadata.tables[table].indexes
                       if str(ix.dialect_options["postgresql"]["using"] or "").lower() in _REBUILD_METHODS)
        if names:
            out[table] = names
    return out


def index_state(prod: Any, expected: dict | None = None) -> list:
    """Informational (always ok), read from the prod catalog: one `rebuild_indexes:<table>` entry (detail `missing=<n>`) per table
    that lacks an HNSW or GIN index of the model, or a single `rebuild_indexes` entry (`all_present`). The report of a first seed
    shows it so the owner knows what to rebuild before setting `memes.state = serving`."""
    expected = rebuildable_indexes() if expected is None else expected
    have = {str(r[0]) for r in prod.query(_PROD_INDEXES_SQL)}
    out = [result(f"{REBUILD_ID}:{t}", True, f"missing={len([n for n in names if n not in have])}")
           for t, names in sorted(expected.items()) if any(n not in have for n in names)]
    return out or [result(REBUILD_ID, True, "all_present")]


def missing_index_tables(pre: list) -> list:
    """The tables a run's `rebuild_indexes:<table>` entries name."""
    prefix = REBUILD_ID + ":"
    return [p["id"][len(prefix):] for p in pre if p["id"].startswith(prefix)]


# ---------------------------------------------------------------------------------------------------------------------------------
# configuration

def required_tables_config(required: list) -> dict:
    """`sync.promote.required_tables` must name image-scoped class D tables."""
    known = set(dg.image_scoped_tables())
    bad = [t for t in required if t not in known]
    return result("required_tables", not bad, "ok" if not bad else "unknown=" + ",".join(bad))
