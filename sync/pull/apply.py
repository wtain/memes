"""P4-T3/T4: the apply phase of `sync.pull`: the mirror of the six class P tables into dev, in ONE dev transaction.

This code writes to DEV only. Prod is read (the open `REPEATABLE READ READ ONLY` snapshot of the plan); no statement here is issued on prod
other than `COPY (SELECT ...) TO STDOUT` and the digest SELECT. What happens, in order, inside `BEGIN ... COMMIT` on the dev connection:

1. `SET LOCAL lock_timeout = '10s'`, then `LOCK TABLE` of the tables the mirror writes (children first, then the ratings table in mode
   `mirror`) in `SHARE ROW EXCLUSIVE` mode: dev writers wait, readers keep reading, and a concurrent dev insert cannot take an id that the copy
   brings. A lock timeout is a failed run ("retry"), nothing changed.
2. Ratings (mode `mirror` only): the dev rows are re-read under the lock and partitioned (`plan.partition_ratings`); the dev rows that will be
   updated or deleted are appended to `ratings_archive.jsonl` (fsynced) BEFORE anything is removed; then deleted, and the prod rows to insert
   or update are copied in. In mode `skip` the table is not touched.
3. The five simple tables: children first, `DELETE` (or `TRUNCATE` for the FK-less tables once dev has more rows than the tracked threshold;
   never for `search_history`), then binary `COPY (SELECT ... left(text, cap) ...)` from the prod snapshot into dev, parents first.
4. `setval` of `meme_impressions`' sequence to `max(id)` (`setval(seq, 1, false)` when empty).
5. Verification inside the transaction: for each simple table the row count and one aggregate hash of dev equal prod's snapshot; for the ratings
   every prod key whose image exists on dev has the same row hash on dev and every planned delete is gone. Any difference rolls everything back.

Any failure rolls the dev transaction back (dev unchanged; a rating archive line may exist for a row that was not removed after all: lines
carry the run id). Messages are codes; a database error is reduced to SQLSTATE, constraint and table by `SyncConnection`.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sync import connect, registry
from sync.config import EXIT_FAILURE, SyncError
from sync.model import Klass
from sync.pull import plan as plan_mod
from sync.pull import snapshot as snap
from sync.pull import state

LOCK_TIMEOUT = "10s"
SPOOL_BYTES = 32 * 1024 * 1024
LOCK_NOT_AVAILABLE = "55P03"
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ID_CHUNK = 5000


class ApplyError(SyncError):
    """A failure of the pull apply that is not a database error (exit 1). The message is a code, never a value."""

    exit_code = EXIT_FAILURE


@dataclass
class ApplyOutcome:
    failure: str | None = None
    replaced: dict = field(default_factory=dict)  # table -> rows written to dev by this run
    truncated: list = field(default_factory=list)  # tables emptied with TRUNCATE
    partition: plan_mod.RatingsPartition | None = None  # the ratings partition as applied (re-read under the lock)
    archived: int = 0
    ledger: frozenset | None = None  # the new ledger content (mirror mode); written by the caller after the commit
    setval: int | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None


def uuid_array(ids: list) -> str:
    """A `'{...}'::uuid[]` literal of checked uuids (COPY takes no parameters)."""
    for i in ids:
        if not _UUID.match(str(i)):
            raise ApplyError("bad_rating_id")
    return "'{" + ",".join(ids) + "}'::uuid[]"


def copy_out_sql(table: str, cap: int, where: str = "") -> str:
    return f"COPY (SELECT {snap.select_list(table, cap)} FROM {snap.table_ident(table)}{where}) TO STDOUT (FORMAT binary)"


def copy_in_sql(table: str) -> str:
    cols = ", ".join(snap.ident(c) for c in snap.columns(table))
    return f"COPY {snap.table_ident(table)} ({cols}) FROM STDIN (FORMAT binary)"


def lock_sql(tables: list) -> str:
    return "LOCK TABLE " + ", ".join(snap.table_ident(t) for t in tables) + " IN SHARE ROW EXCLUSIVE MODE"


def delete_order() -> list:
    return list(reversed(plan_mod.mirror_tables()))


def _copy(prod: Any, dev: Any, table: str, cap: int, where: str = "") -> int:
    with tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES) as spool:
        sent = prod.copy_out(copy_out_sql(table, cap, where), spool)
        spool.seek(0)
        dev.copy_in(copy_in_sql(table), spool)
    return sent


def _chunks(items: list):
    for i in range(0, len(items), ID_CHUNK):
        yield items[i:i + ID_CHUNK]


def _apply_ratings(inp: plan_mod.PlanInput, dev: Any, prod: Any, directory: Path, run_id: str, now: _dt.datetime, out: ApplyOutcome) -> None:
    cap = inp.limits.text_cap
    fresh = snap.read_ratings(dev, cap)
    dev_images = snap.read_image_ids(dev)
    part = plan_mod.partition_ratings(inp.prod_ratings, fresh, inp.ledger, inp.prod_images, dev_images)
    out.partition = part
    reasons = {fresh[k].id: "overwritten" for k in part.update} | {fresh[k].id: "deleted" for k in part.delete}
    victims = list(reasons)
    rows = []
    for chunk in _chunks(victims):
        rows += [r[0] for r in dev.query(f"select to_jsonb(t) from {snap.table_ident(plan_mod.RATINGS)} t where t.id = any(%s::uuid[])", (chunk,))]
    out.archived = state.append_archive(directory, rows, run_id, reasons, now)  # on disk before the first delete
    if out.archived != len(victims):
        raise ApplyError("archive_incomplete")
    for chunk in _chunks(victims):
        dev.query(f"delete from {snap.table_ident(plan_mod.RATINGS)} where id = any(%s::uuid[])", (chunk,))
    wanted = [inp.prod_ratings[k].id for k in part.insert + part.update]
    for chunk in _chunks(wanted):
        _copy(prod, dev, plan_mod.RATINGS, cap, f" WHERE id = ANY({uuid_array(chunk)})")
    out.replaced[plan_mod.RATINGS] = len(wanted)
    received = {plan_mod.key_hash(*k) for k in part.insert + part.update + part.unchanged}
    gone = {plan_mod.key_hash(*k) for k in part.delete}
    out.ledger = frozenset((set(inp.ledger) - gone) | received)


def _verify_ratings(inp: plan_mod.PlanInput, dev: Any, part: plan_mod.RatingsPartition) -> None:
    after = snap.read_ratings(dev, inp.limits.text_cap)
    for k in part.insert + part.update + part.unchanged:
        if k not in after or after[k].hash != inp.prod_ratings[k].hash:
            raise ApplyError("digest_mismatch")
    if any(k in after for k in part.delete):
        raise ApplyError("digest_mismatch")


def _apply_simple(inp: plan_mod.PlanInput, dev: Any, prod: Any, out: ApplyOutcome) -> None:
    cap, threshold = inp.limits.text_cap, inp.limits.truncate_threshold
    for t in delete_order():
        if t in plan_mod.FK_FREE and int(dev.scalar(snap.count_sql(t))) > threshold:
            dev.query(f"TRUNCATE TABLE {snap.table_ident(t)}")
            out.truncated.append(t)
        else:
            dev.query(f"DELETE FROM {snap.table_ident(t)}")
    for t in plan_mod.mirror_tables():
        sent = _copy(prod, dev, t, cap)
        if sent != inp.prod[t].count:
            raise ApplyError("row_count_mismatch")
        out.replaced[t] = sent
    for t in plan_mod.mirror_tables():
        entry = registry.get(t)
        if entry.klass is Klass.P and entry.setval:
            seq = dev.scalar("select pg_get_serial_sequence(%s, 'id')", (t,))
            top = dev.scalar(f"select max(id) from {snap.table_ident(t)}")
            dev.scalar("select setval(%s, %s, %s)", (seq, 1 if top is None else top, top is not None))
            out.setval = 1 if top is None else int(top)


def _verify_simple(inp: plan_mod.PlanInput, dev: Any, prod: Any) -> None:
    cap = inp.limits.text_cap
    for t in plan_mod.mirror_tables():
        if snap.table_digest(dev, t, cap) != snap.table_digest(prod, t, cap):
            raise ApplyError("digest_mismatch")


def run_apply(*, inp: plan_mod.PlanInput, dev: Any, prod: Any, directory: Path, run_id: str, now: _dt.datetime, out: Any = None) -> ApplyOutcome:
    """Mirror prod's snapshot into dev in one transaction. Returns the outcome; a failure leaves dev unchanged (`outcome.failure`)."""
    outcome = ApplyOutcome()
    locked = [*delete_order(), *([plan_mod.RATINGS] if inp.mode == "mirror" else [])]
    began = False
    try:
        dev.query("BEGIN")
        began = True
        dev.query(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        dev.query(lock_sql(locked))
        if inp.mode == "mirror":
            _apply_ratings(inp, dev, prod, Path(directory), run_id, now, outcome)
        else:
            outcome.replaced[plan_mod.RATINGS] = 0
        _apply_simple(inp, dev, prod, outcome)
        _verify_simple(inp, dev, prod)
        if outcome.partition is not None:
            _verify_ratings(inp, dev, outcome.partition)
        dev.query("COMMIT")
        began = False
    except connect.DbError as exc:
        outcome.failure = "dev_lock_timeout" if exc.sqlstate == LOCK_NOT_AVAILABLE else exc.code
    except ApplyError as exc:
        outcome.failure = str(exc)
    except BaseException:
        if began:
            with contextlib.suppress(Exception):
                dev.query("ROLLBACK")
        raise
    if outcome.failure is not None and began:
        with contextlib.suppress(Exception):
            dev.query("ROLLBACK")
    if outcome.failure is not None:  # nothing was committed: do not hand out a ledger or counts of a rolled-back run
        outcome.replaced, outcome.truncated, outcome.ledger, outcome.setval = {}, [], None, None
    return outcome
