"""P2-T6: the apply phase of `sync.promote` for image batches (design P2 "Apply" steps 1 to 3 and 5; the ADR's transfer technique).

This is the code that WRITES to prod. What it does, per batch of `sync.promote.batch_size` images in plan order (`created_at, id`):

1. dev: one transaction `REPEATABLE READ READ ONLY` for the batch. Inside it the batch's promotable rows and per-table digests are read; an
   image that is no longer promotable, or whose digests differ from the plan's, is deferred (`deferred_changed_since_plan`, picked up by
   the next run); the files gate (`files_gate.classify`) is applied to the dev rows about to be copied (`files_gate_miss` and the other
   gate reasons); the remaining images are copied.
2. prod: one transaction. Changed images: the derived rows of the changed digest groups' tables are deleted children first
   (`registry.delete_order`), `images` is upserted (`execute_values`, `ON CONFLICT (id) DO UPDATE`: no staging table, the writer has no
   TEMP) for new images and for changed images whose `core` group changed, and every other table is filled by binary `COPY` from a
   filtered `SELECT` on the dev snapshot to `COPY FROM STDIN` on prod, in registry load order, with explicit alphabetical column lists
   (`nullify` columns are selected as NULL). Then the digests of the batch are recomputed on prod inside the transaction and compared with
   the dev snapshot's; any difference rolls the batch back (`digest_mismatch`). Only then COMMIT.
3. `committed.json` gets the batch (atomic write). A failed batch stops the run (exit 1); committed batches stay and a re-run re-plans.

After the batches (only when the whole run succeeded): the global sets (T7, below), `setval` of the sequences of integer-id class D
tables (never lowered) and an `ANALYZE` of the touched tables (best effort). Then verify (`sync.promote.verify`, T8): prod is re-read in a
read-only transaction and compared with what `committed.json` promised.

Global sets (T7), after all image batches and never with `--limit-images` (the plan has none then): each table in registry level order, one
dev snapshot for the phase and one prod transaction per table. Concepts and their set tables are UPSERT only (`ON CONFLICT (id) DO UPDATE`;
nothing is pruned, decision 27; a row only prod has is left behind). `popularity_absorptions` is insert-or-close: a row new on dev is
inserted, a row dev has closed that prod still has open gains its reverse row and `reversed_by_id`, a row prod already closed is never
reopened, a row only prod has is never touched. The `uq_popularity_open_loser` index allows one open absorb per loser, so the rows are
written in this order: (a) close the absorbs prod has open, (b) new closed absorbs one round at a time (at most one per loser per round:
absorb with NULL, its reverse row, then the update), (c) the new open absorbs. A dev row is re-hashed in the dev snapshot first; one that
differs from the plan is deferred (`deferred_changed_since_plan`); after the writes the hashes of the written rows are recomputed on prod
inside the transaction and any difference rolls the table back (`digest_mismatch`).

Safety rules kept by construction: no statement names a class P or X table or `alembic_version`; there is no `DELETE FROM images`
(decision 27), no TRUNCATE and no DDL; a database error is reduced to SQLSTATE, constraint and table (`SyncConnection`), any other
unexpected exception to its type name; nothing here prints or stores a row value (ids, counts and filenames of renamed images only,
the filenames only in `committed.json`).
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sync import connect, registry
from sync.config import EXIT_FAILURE, SyncError, atomic_write
from sync.model import PROMOTABLE_IMAGES_SQL, Klass, ScopeKind
from sync.promote import digest as dg
from sync.promote import files_gate
from sync.promote import verify as verify_mod

COMMITTED = "committed.json"
SPOOL_BYTES = 32 * 1024 * 1024  # a per-table, per-batch COPY stream stays in memory up to this size, then spills to a temp file
LOCK_TIMEOUT = "30s"
STATEMENT_TIMEOUT = "30min"
DEFERRED = "deferred_changed_since_plan"
MISMATCH = "digest_mismatch"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


class ApplyError(SyncError):
    """A failure of the apply that is not a database error (exit 1). The message is a code, never a value."""

    exit_code = EXIT_FAILURE


class DigestMismatch(ApplyError):
    def __init__(self, ids: list):
        super().__init__(MISMATCH)
        self.ids = list(ids)


# ---------------------------------------------------------------------------------------------------------------------------------
# SQL builders (pure; every name comes from the registry or the model and is checked, ids are checked uuids)

def _ident(name: str) -> str:
    if not _NAME.match(name):
        raise ApplyError("bad_identifier")
    return f'"{name}"'


def uuid_array(ids: list) -> str:
    """A `'{...}'::uuid[]` literal of checked, lower-case uuids (COPY takes no parameters)."""
    for i in ids:
        if not _UUID.match(str(i)):
            raise ApplyError("bad_image_id")
    return "'{" + ",".join(ids) + "}'::uuid[]"


def column_names(table: str) -> list:
    """The table's columns, alphabetical: the one order of every COPY list, the upsert and the digests."""
    return [name for name, _ in dg.model_columns(table)]


def select_sql(table: str, ids: list) -> str:
    """The filtered dev `SELECT` of one image-scoped table; a `nullify` column is selected as NULL (prod stores NULL there)."""
    entry = registry.get(table)
    exprs = ", ".join("NULL" if name in entry.nullify else f't."{name}"' for name in column_names(table))
    image, join = dg.image_expr(table)
    return f"SELECT {exprs} FROM {_ident(table)} AS t{join} WHERE {image} = ANY({uuid_array(ids)})"


def copy_out_sql(table: str, ids: list) -> str:
    return f"COPY ({select_sql(table, ids)}) TO STDOUT (FORMAT binary)"


def copy_in_sql(table: str) -> str:
    cols = ", ".join(_ident(c) for c in column_names(table))
    return f"COPY {_ident(table)} ({cols}) FROM STDIN (FORMAT binary)"


def delete_sql(table: str, ids: list) -> str:
    """Delete the rows of the images `ids` in one derived table (never `images`: decision 27)."""
    entry = registry.get(table)
    scope = entry.scope
    arr = uuid_array(ids)
    if scope.kind is ScopeKind.IMAGE_FK:
        return f"DELETE FROM {_ident(table)} WHERE {_ident(scope.column)} = ANY({arr})"
    if scope.kind is ScopeKind.VIA:
        parent = registry.get(scope.parent)
        if parent.scope.kind is not ScopeKind.IMAGE_FK:
            raise ApplyError("bad_scope")
        return (f"DELETE FROM {_ident(table)} AS t USING {_ident(parent.name)} AS p "
                f"WHERE p.{_ident(parent.key[0])} = t.{_ident(scope.column)} AND p.{_ident(parent.scope.column)} = ANY({arr})")
    raise ApplyError("bad_scope")


def images_select_sql() -> str:
    entry = registry.get("images")
    exprs = ", ".join("NULL" if name in entry.nullify else _ident(name) for name in column_names("images"))
    return f"SELECT {exprs} FROM images WHERE images.id = ANY(%s::uuid[]) AND {PROMOTABLE_IMAGES_SQL}"


def images_upsert_sql() -> str:
    """The one image-scoped upsert: `images` by primary key, every non-key column updated."""
    cols = column_names("images")
    key = set(registry.get("images").key)
    updates = ", ".join(f"{_ident(c)} = EXCLUDED.{_ident(c)}" for c in cols if c not in key)
    conflict = ", ".join(_ident(k) for k in registry.get("images").key)
    return f"INSERT INTO images ({', '.join(_ident(c) for c in cols)}) VALUES %s ON CONFLICT ({conflict}) DO UPDATE SET {updates}"


def load_sequence(tables: list) -> list:
    """The tables in registry load order (parents first), flat."""
    want = set(tables)
    return [t for level in registry.load_order(Klass.D) for t in level if t in want]


def delete_sequence(tables: list) -> list:
    """The tables in registry delete order (children first), flat."""
    want = set(tables)
    return [t for level in registry.delete_order(Klass.D) for t in level if t in want]


# ---------------------------------------------------------------------------------------------------------------------------------
# committed.json

class CommittedLog:
    """`runs/<runid>/committed.json`: written atomically after every committed batch, never before the first one. An existing file of
    the same run id and environment is continued (resume); a foreign one is refused."""

    def __init__(self, folder: Path, run_id: str, env: str, tables: list, global_tables: list | None = None):
        self.path = Path(folder) / COMMITTED
        self.doc = {"run_id": run_id, "env": env, "tables": list(tables), "batches": [],
                    "globals": {"concepts": False, "absorptions": False}}
        if global_tables:
            self.doc["global_tables"] = list(global_tables)  # the global sets in scope of the run (verify compares their parity)
        if self.path.is_file():
            try:
                old = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ApplyError("committed_json_unreadable") from None
            if old.get("run_id") != run_id or old.get("env") != env or not isinstance(old.get("batches"), list):
                raise ApplyError("committed_json_foreign")
            self.doc.update({"batches": old["batches"], "globals": old.get("globals", self.doc["globals"])})
            if old.get("global_rows"):
                self.doc["global_rows"] = old["global_rows"]

    @property
    def next_n(self) -> int:
        return len(self.doc["batches"]) + 1

    @property
    def committed_ids(self) -> list:
        return [i for b in self.doc["batches"] for i in b["ids"]]

    def append(self, n: int, ids: list, digests: dict, renamed: list, committed_at: str) -> None:
        self.doc["batches"].append({"n": n, "committed_at": committed_at, "ids": list(ids), "digests": digests, "renamed": renamed})
        self._write()

    def record_global(self, table: str, rows: dict) -> None:
        """The rows of a global table this run committed: `{key: row hash of the dev snapshot}` (keys as text), written after the commit.
        `global_rows` is absent until a global set is committed."""
        self.doc.setdefault("global_rows", {}).setdefault(table, {}).update({str(k): h for k, h in rows.items()})
        self._write()

    def flip_global(self, name: str) -> None:
        self.doc["globals"][name] = True
        self._write()

    def _write(self) -> None:
        atomic_write(self.path, json.dumps(self.doc, indent=2, ensure_ascii=True) + "\n")


# ---------------------------------------------------------------------------------------------------------------------------------
# result

@dataclass
class ApplyOutcome:
    added: list = field(default_factory=list)  # image ids committed as new
    overwritten: list = field(default_factory=list)  # image ids committed as replaced
    skipped: dict = field(default_factory=dict)  # reason code -> ids (apply time only: files gate, deferred, digest mismatch)
    failed: list = field(default_factory=list)  # ids of the batch that was rolled back
    failure: str | None = None  # code of the failure that stopped the run
    batches: int = 0  # batches committed by this run
    rows: dict = field(default_factory=dict)  # table -> rows copied to prod by this run
    notes: list = field(default_factory=list)  # code-like notes for the console (global sets not applied, ...)
    inflight: list = field(default_factory=list)  # ids inside the prod transaction that is open right now
    setvals: dict = field(default_factory=dict)  # table -> new sequence value
    globals: dict = field(default_factory=dict)  # global table -> GlobalOutcome (only tables the apply reached)
    verify: Any = None  # verify.VerifyResult, set when the run committed something

    @property
    def ok(self) -> bool:
        return self.failure is None

    def skip(self, reason: str, ids: list) -> None:
        if ids:
            self.skipped.setdefault(reason, []).extend(ids)


@dataclass
class GlobalOutcome:
    """What one global table got: keys written (split as the plan categorised them), keys skipped per reason, keys of the failed write."""

    added: list = field(default_factory=list)
    overwritten: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)
    failed: list = field(default_factory=list)
    failure: str | None = None


@dataclass
class Context:
    dev: Any
    prod: Any
    inp: Any  # plan.PlanInput
    plan: Any  # plan.Plan
    log: CommittedLog
    batch_size: int
    out: Any
    now: Callable[[], _dt.datetime] = lambda: _dt.datetime.now(_dt.timezone.utc)
    group_tables: dict = field(default_factory=dict)
    tables: list = field(default_factory=list)  # image-scoped tables in scope (load order), `images` included


# ---------------------------------------------------------------------------------------------------------------------------------
# one batch

def copy_table(ctx: Context, table: str, ids: list) -> int:
    """Binary COPY of one table for `ids` from the dev snapshot to prod (inside the open prod transaction); rows copied."""
    with tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES, mode="w+b") as spool:
        n = ctx.dev.copy_out(copy_out_sql(table, ids), spool)
        if n == 0:
            return 0
        spool.seek(0)
        return ctx.prod.copy_in(copy_in_sql(table), spool)


def _before_commit(n: int, ids: list) -> None:
    """Test hook: called inside the open prod transaction after the digests matched, right before COMMIT."""


def _hashes(per_image: dict, ids: list, group_tables: dict) -> dict:
    return {i: dg.image_group_hashes(per_image.get(i, {}), group_tables) for i in ids}


def _tokens(per_table: dict, tables: list) -> dict:
    return {t: per_table.get(t, dg.EMPTY).token for t in tables}


def _mismatching(dev_digests: dict, prod_digests: dict, ids: list, tables: list) -> list:
    return [i for i in ids if _tokens(dev_digests.get(i, {}), tables) != _tokens(prod_digests.get(i, {}), tables)]


def _rollback(conn: Any) -> None:
    with contextlib.suppress(Exception):
        conn.query("ROLLBACK")


def apply_batch(ctx: Context, n: int, ids: list, outcome: ApplyOutcome) -> list:
    """Apply one batch; returns the ids committed. Raises on a failure after rolling prod back; deferred and gated ids are recorded
    in `outcome` and are not an error."""
    inp, plan = ctx.inp, ctx.plan
    dev, prod = ctx.dev, ctx.prod
    tables = ctx.tables
    outcome.inflight = []
    dev.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    committing = False
    try:
        rows = {str(r[0]): dg.DevImage(str(r[0]), r[1], r[2], None) for r in dev.query(
            f"SELECT images.id::text, images.filename, images.content_hash FROM images WHERE images.id = ANY(%s::uuid[]) "
            f"AND {PROMOTABLE_IMAGES_SQL}", (list(ids),))}
        snapshot = dg.fetch_image_digests(dev, tables, "ids", list(ids))
        planned = _hashes(inp.dev_digests, ids, ctx.group_tables)
        now = _hashes(snapshot, ids, ctx.group_tables)
        deferred = [i for i in ids if i not in rows or now[i] != planned[i]]
        outcome.skip(DEFERRED, deferred)
        out_set = set(deferred)
        live = [i for i in ids if i not in out_set]
        gated = files_gate.classify(inp.files, [rows[i] for i in live]) if live else {}
        gated_ids = {i for v in gated.values() for i in v}
        for reason, gone in gated.items():
            outcome.skip(reason, gone)
        live = [i for i in live if i not in gated_ids]
        if not live:
            return []
        outcome.inflight = list(live)

        new_ids = [i for i in live if i not in plan.changed]
        changed = {i: plan.changed[i] for i in live if i in plan.changed}
        replace: dict = {}  # table -> changed ids whose groups include it
        for i, groups in changed.items():
            for g in groups:
                for t in ctx.group_tables.get(g, []):
                    replace.setdefault(t, []).append(i)
        core_changed = [i for i, g in changed.items() if "core" in g]
        image_rows_ids = new_ids + core_changed

        prod.query("BEGIN")
        try:
            old_names = {str(r[0]): r[1] for r in prod.query(
                "SELECT id::text, filename FROM images WHERE id = ANY(%s::uuid[])", (list(changed),))} if changed else {}
            for t in delete_sequence([t for t in replace if t != "images"]):
                prod.query(delete_sql(t, replace[t]))
            if image_rows_ids:
                values = dev.query(images_select_sql(), (image_rows_ids,))
                if len(values) != len(image_rows_ids):
                    raise ApplyError("image_rows_changed")
                prod.execute_values(images_upsert_sql(), values)
            for t in load_sequence([t for t in tables if t != "images"]):
                copy_ids = new_ids + replace.get(t, [])
                if copy_ids:
                    outcome.rows[t] = outcome.rows.get(t, 0) + copy_table(ctx, t, copy_ids)
            prod_digests = dg.fetch_image_digests(prod, tables, "ids", live)
            bad = _mismatching(snapshot, prod_digests, live, tables)
            if bad:
                raise DigestMismatch(bad)
            _before_commit(n, live)
            committing = True
            prod.query("COMMIT")
            committing = False
        except BaseException:
            if not committing:
                _rollback(prod)
            raise
        outcome.inflight = []
        digests = {i: now[i] for i in live}
        renamed = [{"id": i, "old_filename": old_names[i], "new_filename": rows[i].filename}
                   for i in changed if i in old_names and old_names[i] != rows[i].filename]
        ctx.log.append(n, live, digests, renamed, ctx.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
        return live
    finally:
        with contextlib.suppress(Exception):
            dev.query("COMMIT")


# ---------------------------------------------------------------------------------------------------------------------------------
# after the batches

def setval_tables(prod: Any, tables: list | None = None) -> dict:
    """Move the sequence of every integer-id class D table with `setval` past `max(id)` of prod, never lower. Returns table -> value set.
    (`registry.setval_tables` also lists class P tables, which a promote never touches: they are filtered out.)"""
    done: dict = {}
    wanted = tables if tables is not None else registry.setval_tables()
    for table in wanted:
        entry = registry.get(table)
        if entry.klass is not Klass.D or not entry.setval or len(entry.key) != 1:
            continue
        key = entry.key[0]
        seq = prod.scalar("SELECT pg_get_serial_sequence(%s, %s)", (_ident(table), key))
        if not seq:
            continue
        max_id = prod.scalar(f"SELECT max({_ident(key)}) FROM {_ident(table)}")
        if max_id is None:
            continue
        last, called = prod.query(f"SELECT last_value, is_called FROM {seq}")[0]
        next_value = last + 1 if called else last
        if max_id >= next_value:
            prod.query("SELECT setval(%s::regclass, %s, true)", (seq, int(max_id)))
            done[table] = int(max_id)
    return done


def analyze_tables(prod: Any, tables: list) -> int:
    """Best effort `ANALYZE` of the touched tables (needs MAINTAIN or ownership; a refusal is not a failure). Returns the failures."""
    failed = 0
    for t in tables:
        try:
            prod.query(f"ANALYZE {_ident(t)}")
        except connect.DbError:
            failed += 1
    return failed


# ---------------------------------------------------------------------------------------------------------------------------------
# global sets (T7)

SETS = {"popularity_absorptions": "absorptions"}  # every other global table belongs to the "concepts" set of committed.json
DEV_SNAPSHOT = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"


def set_of(table: str) -> str:
    return SETS.get(table, "concepts")


def global_columns(table: str) -> list:
    """`(name, kind)` of the table's columns, alphabetical (the order of every select and insert of a global table)."""
    return dg.model_columns(table)


def global_select_sql(table: str) -> str:
    """The dev `SELECT` of full rows by key: `nullify` columns read NULL, `vector` and `json` columns as text (re-cast on insert)."""
    entry = registry.get(table)
    exprs = ", ".join("NULL" if n in entry.nullify else (f't."{n}"::text' if k in ("vector", "json") else f't."{n}"')
                      for n, k in global_columns(table))
    key = entry.key[0]
    return f'SELECT {exprs} FROM {_ident(table)} AS t WHERE t.{_ident(key)} = ANY(%s::{dg.key_sql_type(table)}[]) ORDER BY t.{_ident(key)}'


def global_template(table: str) -> str:
    return "(" + ", ".join(f"%s::{k}" if k in ("vector", "json") else "%s" for _, k in global_columns(table)) + ")"


def global_upsert_sql(table: str) -> str:
    """`INSERT ... VALUES %s ON CONFLICT (key) DO UPDATE`: every non-key column is updated; no delete anywhere."""
    entry = registry.get(table)
    cols = [n for n, _ in global_columns(table)]
    key = set(entry.key)
    updates = ", ".join(f"{_ident(c)} = EXCLUDED.{_ident(c)}" for c in cols if c not in key)
    return (f"INSERT INTO {_ident(table)} ({', '.join(_ident(c) for c in cols)}) VALUES %s "
            f"ON CONFLICT ({', '.join(_ident(k) for k in entry.key)}) DO UPDATE SET {updates}")


def absorption_insert_sql() -> str:
    cols = [n for n, _ in global_columns(dg.ABSORPTIONS)]
    return f"INSERT INTO {_ident(dg.ABSORPTIONS)} ({', '.join(_ident(c) for c in cols)}) VALUES %s"


def absorption_close_sql() -> str:
    """Set `reversed_by_id` of absorb rows that are still open: a closed row is never touched (never reopened, never re-pointed)."""
    t = _ident(dg.ABSORPTIONS)
    return (f"UPDATE {t} AS t SET reversed_by_id = v.r FROM (VALUES %s) AS v(a, r) "
            f"WHERE t.id = v.a AND t.reversed_by_id IS NULL AND t.kind = 'absorb'")


def fetch_global_rows(dev: Any, table: str, keys: list) -> dict:
    """key -> row tuple (alphabetical columns) from the dev snapshot."""
    ix = [n for n, _ in global_columns(table)].index(registry.get(table).key[0])
    out: dict = {}
    for i in range(0, len(keys), dg.KEY_CHUNK):
        for row in dev.query(global_select_sql(table), (keys[i:i + dg.KEY_CHUNK],)):
            out[row[ix]] = tuple(row)
    return out


def _sorted_rows(rows: dict, keys: list, created: int, ident: int) -> list:
    return sorted(keys, key=lambda k: (rows[k][created], str(rows[k][ident])))


def write_absorptions(prod: Any, dev_rows: dict, live: list, prod_state: dict) -> set:
    """Insert-or-close (see the module docstring). `dev_rows`: dev rows of every key in `live` and of the reverse rows they point at;
    `prod_state`: key -> (hash, closed) of those keys on prod now. Returns the keys written (inserted or closed)."""
    cols = [n for n, _ in global_columns(dg.ABSORPTIONS)]
    ix = {c: i for i, c in enumerate(cols)}
    kind, rev_by, reverses, loser = ix["kind"], ix["reversed_by_id"], ix["reverses_id"], ix["loser_image_id"]
    on_prod = set(prod_state)

    def row_of(k):
        return dev_rows[k]

    def without_close(k):
        r = list(row_of(k))
        r[rev_by] = None
        return tuple(r)

    def reverse_of(a):
        r = row_of(a)[rev_by]
        if r is None:
            return None
        if r not in dev_rows or dev_rows[r][kind] != "reverse" or str(dev_rows[r][reverses]) != str(a) or dev_rows[r][rev_by] is not None:
            raise ApplyError("absorption_dangling_reverse")
        return r

    new = [k for k in live if k not in on_prod]
    close_existing = [k for k in live if k in on_prod and row_of(k)[kind] == "absorb" and row_of(k)[rev_by] is not None
                      and prod_state[k][1] is False]
    new_abs = [k for k in new if row_of(k)[kind] == "absorb"]
    claimed: set = set()
    closed_new, open_new = [], []
    for a in _sorted_rows(dev_rows, new_abs, ix["created_at"], ix["id"]):
        (closed_new if row_of(a)[rev_by] is not None else open_new).append(a)
    written: set = set()

    # (a) the absorbs prod has open and dev has closed: their reverse rows, then the update
    pending_rev = []
    ordered_close = _sorted_rows(dev_rows, close_existing, ix["created_at"], ix["id"])
    for a in ordered_close:
        r = reverse_of(a)
        claimed.add(r)
        if r not in on_prod:
            pending_rev.append(r)
    if pending_rev:
        prod.execute_values(absorption_insert_sql(), [row_of(r) for r in pending_rev])
        written.update(pending_rev)
    if ordered_close:
        prod.execute_values(absorption_close_sql(), [(a, reverse_of(a)) for a in ordered_close], template="(%s::uuid, %s::uuid)")
        written.update(ordered_close)

    # (b) new absorbs dev has closed, in rounds: at most one absorb per loser is open at any moment
    per_loser: dict = {}
    for a in closed_new:
        group = row_of(a)[loser] if row_of(a)[loser] is not None else ("own", a)
        per_loser.setdefault(group, []).append(a)
    for n in range(max((len(v) for v in per_loser.values()), default=0)):
        batch = [v[n] for v in per_loser.values() if len(v) > n]
        revs = [reverse_of(a) for a in batch]
        prod.execute_values(absorption_insert_sql(), [without_close(a) for a in batch])
        fresh = [r for r in revs if r not in on_prod]
        if fresh:
            prod.execute_values(absorption_insert_sql(), [row_of(r) for r in fresh])
        prod.execute_values(absorption_close_sql(), list(zip(batch, revs)), template="(%s::uuid, %s::uuid)")
        claimed.update(revs)
        written.update(batch)
        written.update(revs)

    # (c) the new open absorbs, last
    if open_new:
        prod.execute_values(absorption_insert_sql(), [row_of(a) for a in open_new])
        written.update(open_new)

    if [k for k in new if row_of(k)[kind] == "reverse" and k not in claimed]:
        raise ApplyError("absorption_unclaimed_reverse")  # a reverse row whose absorb is neither new nor open on prod
    return written


def apply_global_table(ctx: Context, table: str, res: GlobalOutcome, log: CommittedLog) -> bool:
    """One global table: re-hash the planned dev rows in the dev snapshot, write them in one prod transaction, check their hashes on
    prod before the commit. Returns True when the table was written. Raises on a failure after rolling prod back."""
    dev, prod, plan = ctx.dev, ctx.prod, ctx.plan.globals[table]
    planned = ctx.inp.dev_globals.get(table, {})
    want = list(plan["added"]) + list(plan["overwritten"])
    now = dg.fetch_global_hashes(dev, table, want)
    deferred = [k for k in want if k not in now or now[k][0] != planned[k][0]]
    if deferred:
        res.skipped.setdefault(DEFERRED, []).extend(deferred)
    gone = set(deferred)
    live = [k for k in want if k not in gone]
    if not live:
        return False
    absorptions = table == dg.ABSORPTIONS
    rows = fetch_global_rows(dev, table, live)
    if absorptions:  # the reverse rows the live absorbs point at
        ix = [n for n, _ in global_columns(table)].index("reversed_by_id")
        extra = sorted({r[ix] for r in rows.values() if r[ix] is not None} - set(rows))
        if extra:
            rows.update(fetch_global_rows(dev, table, extra))
            now.update(dg.fetch_global_hashes(dev, table, extra))
    prod.query("BEGIN")
    committing = False
    try:
        if absorptions:
            written = write_absorptions(prod, rows, [k for k in live if k in rows], dg.fetch_global_hashes(prod, table, list(rows)))
        else:
            prod.execute_values(global_upsert_sql(table), [rows[k] for k in live if k in rows], template=global_template(table))
            written = {k for k in live if k in rows}
        got = dg.fetch_global_hashes(prod, table, sorted(written)) if written else {}
        bad = [k for k in sorted(written) if got.get(k, (None,))[0] != now[k][0]]
        if bad:
            raise DigestMismatch(bad)
        committing = True
        prod.query("COMMIT")
        committing = False
    except BaseException:
        if not committing:
            _rollback(prod)
        raise
    if not written:
        return False
    log.record_global(table, {k: now[k][0] for k in written})
    res.added += [k for k in plan["added"] if k in written]
    res.overwritten += [k for k in plan["overwritten"] if k in written]
    return True


def apply_globals(ctx: Context, outcome: ApplyOutcome) -> None:
    """The global sets (T7): every table with planned changes, in registry level order, after the image batches. A failure stops the
    phase (the earlier tables stay committed); `committed.json["globals"]` flips per set once every table of the set that had work
    is committed."""
    glob = ctx.plan.globals
    todo = [t for t in dg.table_order(ctx.inp.global_tables) if t in glob and (glob[t]["added"] or glob[t]["overwritten"])]
    if not todo:
        return
    wrote: dict = {}
    ctx.dev.query(DEV_SNAPSHOT)
    try:
        for table in todo:
            res = outcome.globals.setdefault(table, GlobalOutcome())
            planned = list(glob[table]["added"]) + list(glob[table]["overwritten"])
            try:
                wrote[table] = apply_global_table(ctx, table, res, ctx.log)
            except KeyboardInterrupt:
                res.failed, res.failure = planned, "interrupted"
                outcome.failure = "interrupted"
                break
            except DigestMismatch as exc:
                res.skipped.setdefault(MISMATCH, []).extend(exc.ids)
                res.failed, res.failure = [k for k in planned if k not in set(exc.ids)], MISMATCH
                outcome.failure = MISMATCH
                break
            except connect.DbError as exc:
                res.failed, res.failure = planned, exc.code
                outcome.failure = exc.code
                break
            except ApplyError as exc:
                res.failed, res.failure = planned, str(exc)[:60]
                outcome.failure = res.failure
                break
            except Exception as exc:  # anything else: its type only, never its message (it may quote a value)
                res.failed, res.failure = planned, f"unexpected:{type(exc).__name__.lower()}"[:60]
                outcome.failure = res.failure
                break
    finally:
        with contextlib.suppress(Exception):
            ctx.dev.query("COMMIT")
    if outcome.failure is None:
        for name in sorted({set_of(t) for t in todo}):
            mine = [t for t in todo if set_of(t) == name]
            if any(wrote.get(t) for t in mine):
                ctx.log.flip_global(name)
                _say(ctx.out, f"global set {name}: committed")


# ---------------------------------------------------------------------------------------------------------------------------------
# the run

def _chunks(items: list, size: int) -> list:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _say(out: Any, text: str) -> None:
    if out is not None:
        out.write(text + "\n")


def run_apply(*, settings: Any, inp: Any, plan: Any, dev: Any, prod: Any, folder: Path, run_id: str, out: Any = None,
              now: Callable[[], _dt.datetime] | None = None) -> ApplyOutcome:
    """Apply the image batches of `plan` and the post steps. Never raises for a failed batch: the failure is in the outcome (the caller
    writes the report and exits 1); a precondition of the apply itself (unpinned session, unusable committed.json) raises."""
    outcome = ApplyOutcome()
    tables = dg.table_order(inp.tables)
    ctx = Context(dev=dev, prod=prod, inp=inp, plan=plan, log=None, batch_size=max(1, int(inp.batch_size)), out=out,
                  group_tables=dg.groups_for(tables), tables=tables)
    if now is not None:
        ctx.now = now
    todo = list(plan.write_order)
    image_work = bool(todo and tables)
    global_work = any(v["added"] or v["overwritten"] for v in plan.globals.values())
    if image_work or global_work:
        if image_work and inp.files is None:
            raise ApplyError("files_report_missing")
        for side in (dev, prod):
            if dg.pin_session(side):
                raise ApplyError("session_not_pinned")
        prod.query(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        prod.query(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
        ctx.log = CommittedLog(folder, run_id, settings.env, tables, inp.global_tables if not inp.limit_images else [])
    if image_work:
        already = set(ctx.log.committed_ids)
        todo = [i for i in todo if i not in already]
        new = set(plan.new)
        for chunk in _chunks(todo, ctx.batch_size):
            n = ctx.log.next_n
            try:
                done = apply_batch(ctx, n, chunk, outcome)
            except KeyboardInterrupt:
                outcome.failed, outcome.failure = list(outcome.inflight or chunk), "interrupted"
                break
            except DigestMismatch as exc:
                outcome.skip(MISMATCH, exc.ids)
                outcome.failed = [i for i in outcome.inflight if i not in set(exc.ids)]
                outcome.failure = MISMATCH
                break
            except connect.DbError as exc:
                outcome.failed, outcome.failure = list(outcome.inflight or chunk), exc.code
                break
            except ApplyError as exc:
                outcome.failed, outcome.failure = list(outcome.inflight or chunk), str(exc)[:60]
                break
            except Exception as exc:  # anything else: its type only, never its message (it may quote a value)
                outcome.failed, outcome.failure = list(outcome.inflight or chunk), f"unexpected:{type(exc).__name__.lower()}"[:60]
                break
            outcome.batches += 1
            outcome.added += [i for i in done if i in new]
            outcome.overwritten += [i for i in done if i not in new]
            _say(out, f"batch {n}: {len(done)} of {len(chunk)} images committed")
    if outcome.ok:
        apply_globals(ctx, outcome)
    if outcome.ok:
        try:
            outcome.setvals = setval_tables(prod, [t for t in inp.global_tables if t in registry.setval_tables()])
            touched = [t for t in tables + list(outcome.globals) if outcome.rows.get(t) or (t in outcome.globals and outcome.globals[t].added + outcome.globals[t].overwritten)]
            if touched:
                analyze_tables(prod, touched)
        except connect.DbError as exc:
            outcome.failure = exc.code
    if ctx.log is not None and outcome.failure != "interrupted" and verify_mod.has_committed(ctx.log.doc):
        outcome.verify = verify_mod.verify_after_apply(dev, prod, ctx.log.doc)
    return outcome
