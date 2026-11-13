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

After the batches (only when the whole run succeeded): `setval` of the sequences of integer-id class D tables (never lowered) and an
`ANALYZE` of the touched tables (best effort). Global sets (T7) and verify (T8) have hooks here and are not built.

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

    def __init__(self, folder: Path, run_id: str, env: str, tables: list):
        self.path = Path(folder) / COMMITTED
        self.doc = {"run_id": run_id, "env": env, "tables": list(tables), "batches": [],
                    "globals": {"concepts": False, "absorptions": False}}
        if self.path.is_file():
            try:
                old = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ApplyError("committed_json_unreadable") from None
            if old.get("run_id") != run_id or old.get("env") != env or not isinstance(old.get("batches"), list):
                raise ApplyError("committed_json_foreign")
            self.doc.update({"batches": old["batches"], "globals": old.get("globals", self.doc["globals"])})

    @property
    def next_n(self) -> int:
        return len(self.doc["batches"]) + 1

    @property
    def committed_ids(self) -> list:
        return [i for b in self.doc["batches"] for i in b["ids"]]

    def append(self, n: int, ids: list, digests: dict, renamed: list, committed_at: str) -> None:
        self.doc["batches"].append({"n": n, "committed_at": committed_at, "ids": list(ids), "digests": digests, "renamed": renamed})
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

    @property
    def ok(self) -> bool:
        return self.failure is None

    def skip(self, reason: str, ids: list) -> None:
        if ids:
            self.skipped.setdefault(reason, []).extend(ids)


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


def apply_globals(ctx: Context, outcome: ApplyOutcome) -> None:
    """Hook for T7 (concepts upsert with setval, absorptions insert-or-close). Not built: nothing is written, the run says so."""
    glob = ctx.plan.globals
    if any(v["added"] or v["overwritten"] for v in glob.values()):
        outcome.notes.append("global_sets_not_applied")


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
    if todo and tables:
        if inp.files is None:
            raise ApplyError("files_report_missing")
        for side in (dev, prod):
            if dg.pin_session(side):
                raise ApplyError("session_not_pinned")
        prod.query(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        prod.query(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
        ctx.log = CommittedLog(folder, run_id, settings.env, tables)
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
        try:
            outcome.setvals = setval_tables(prod, [t for t in inp.global_tables if t in registry.setval_tables()])
            touched = [t for t in tables if outcome.rows.get(t)]
            if touched:
                analyze_tables(prod, touched)
        except connect.DbError as exc:
            outcome.failure = exc.code
    return outcome
