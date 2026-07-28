# Batch Run Trigger Tracking — Design

Status: planned
Plan: docs/superpowers/plans/2026-07-28-batch-run-trigger-tracking.md
Follow-ups: docs/superpowers/specs/2026-07-28-batch-run-wrapper-design.md, docs/superpowers/specs/2026-07-28-admin-batch-controller-design.md

**Date:** 2026-07-28.

First of a 3-spec sequence for the batch admin controller feature. Followed by
`2026-07-28-batch-run-wrapper-design.md` (the universal run wrapper, depends on this) and
`2026-07-28-admin-batch-controller-design.md` (the HTTP API, depends on both).

---

## Motivation

The upcoming admin controller (spec 3) needs to distinguish a manually-triggered batch run from a
scheduler-triggered one, both in the `GET .../runs` list and for the concurrency guard. `BatchRun`
(`repository/batch_runs.py`, `Storage/models.py`) has no such column today. This spec adds it, plus
closes a real race condition in the existing "is one already running" check that the admin
controller and the scheduler will both depend on.

## Scope

**In scope:** a `trigger` column on `batch_runs` (`manual` / `scheduled` / `unknown`) with backfill;
a partial unique index closing the concurrency-guard race; `BatchRunRepository.create_run()`'s
signature change and the new `BatchAlreadyRunningError`; updating the two existing call sites.

**Out of scope:** the run wrapper and the trends_batch/move_flagged/unregister_deleted_images
refactor (spec 2); the HTTP API (spec 3). Nothing in this spec changes runtime behavior of any
existing batch job beyond the required call-site signature update.

---

## Current state (for reference)

Two call sites create `BatchRun` rows today (verified by grep, no others exist):

```python
# batch/ingest_hash_dedup.py:156
batch_id = await runs_repo.create_run(kind="ingestion", stage="hash_dedup")

# batch/trends_batch.py:45
run_id = await runs_repo.create_run(kind="trends")
```

`repository/batch_runs.py`'s `create_run`:

```python
async def create_run(self, kind: str, stage: str | None = None) -> uuid.UUID:
    run = BatchRun(kind=kind, status=str(RunStatus.started), stage=stage)
    self._session.add(run)
    await self._session.flush()
    return run.run_id
```

`get_active_run(kind)` is a plain check-then-act query (`SELECT ... WHERE kind = :kind AND status =
'started' ORDER BY created_at DESC LIMIT 1`) — nothing today prevents two concurrent callers from
both seeing "no active run" and both inserting a `started` row for the same `kind`.

## Design

### Schema change

New Alembic revision, run from `Storage/`:

**Table lock first, before anything else in `upgrade()`.** Alembic migrations run inside a
transaction by default; taking an exclusive lock as the very first statement means any concurrent
`create_run()` (from the scheduler ticking, or a manual trigger once spec 3 exists) simply blocks
until this migration's transaction commits or rolls back, rather than racing the cleanup/backfill
steps below — closing the window where a new row could appear between the cleanup UPDATE and the
unique index creation:

```python
op.execute("LOCK TABLE batch_runs IN ACCESS EXCLUSIVE MODE")

op.add_column('batch_runs', sa.Column('trigger', sa.String(20), nullable=True))

# Defensive cleanup before the unique index: if migration ever runs against data with more
# than one 'started' row for the same kind (shouldn't happen given the scheduler's own
# orphan-recovery, but the index creation would fail outright on dirty data), keep only the
# most recent per kind and fail the rest.
op.execute("""
    UPDATE batch_runs SET status = 'failed', completed_at = now(),
           error = 'superseded (migration cleanup)'
    WHERE status = 'started' AND run_id NOT IN (
        SELECT DISTINCT ON (kind) run_id FROM batch_runs
        WHERE status = 'started' ORDER BY kind, created_at DESC
    )
""")

op.execute("UPDATE batch_runs SET trigger = 'unknown' WHERE trigger IS NULL")
op.alter_column('batch_runs', 'trigger', nullable=False)

op.create_index(
    'ix_batch_runs_one_active_per_kind', 'batch_runs', ['kind'],
    unique=True, postgresql_where=sa.text("status = 'started'"),
)
```

Resulting model addition (`Storage/models.py`):

```python
class TriggerType(enum.Enum):
    manual = "manual"
    scheduled = "scheduled"
    unknown = "unknown"

    def __str__(self) -> str:
        return self.value

class BatchRun(Base):
    __tablename__ = "batch_runs"
    # ... existing columns unchanged ...
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
```

### Repository change

```python
class BatchAlreadyRunningError(Exception):
    """Raised by create_run() when the partial unique index rejects a concurrent duplicate."""

async def create_run(self, kind: str, trigger: str, stage: str | None = None) -> uuid.UUID:
    run = BatchRun(kind=kind, trigger=trigger, status=str(RunStatus.started), stage=stage)
    self._session.add(run)
    try:
        await self._session.flush()
    except sqlalchemy.exc.IntegrityError as e:
        raise BatchAlreadyRunningError(kind) from e
    return run.run_id
```

`trigger` is a required parameter — no default — so every caller must state its intent explicitly
rather than silently defaulting to something that might be wrong. Callers (the wrapper, the admin
endpoint — both built in later specs) catch `BatchAlreadyRunningError` and translate it into their
own "already running" response; `get_active_run()` remains as a cheap pre-check callers can use to
avoid attempting (and logging a rollback for) a doomed insert, but it is no longer the sole guard —
the unique index is what actually closes the race.

### Existing call-site updates

- `batch/ingest_hash_dedup.py:156` → `trigger="manual"` (ingestion is always human-invoked today,
  no scheduler path exists for it).
- `batch/trends_batch.py:45` → `trigger="unknown"`, **temporarily** — today this single code path
  serves both a human running the script directly *and* the scheduler (which currently invokes
  `trends_batch.py` directly, per `2026-07-27-batch-job-scheduler-design.md`), so there is no way
  to honestly distinguish them yet. Spec 2 replaces this call site entirely (splitting `manual` CLI
  use from `scheduled` wrapper use), so `"unknown"` here is a deliberately short-lived placeholder,
  not a real answer — noted so a reviewer doesn't mistake it for the final design.

### Testing

Extend `tests/integration/test_batch_runs_repository.py`: `create_run` requires `trigger` (update
all existing calls in that file); a new test asserts a second `create_run(kind=X, ...)` while a
`kind=X` row is still `started` raises `BatchAlreadyRunningError`, and that creating one for a
*different* kind — or the same kind once the first is completed/failed — succeeds. Run the full
`tests/integration/` root (not just this file) per the existing "Running the right test scope"
convention in `CLAUDE.md`, since `repository/batch_runs.py` is shared code.

## Rollout

**Operational note:** the table lock guarantees correctness even if the scheduler is running during
this migration (a concurrent `create_run()` just blocks briefly rather than racing), but as a
belt-and-suspenders step, stop the backend (or set `scheduler.enabled: false` and restart it) before
running this migration in each environment, so no tick is left waiting on the lock at all.

1. Alembic revision per above (each environment's DB migrates independently, as usual).
2. Add `TriggerType`, extend `BatchRun` (`Storage/models.py`).
3. Update `repository/batch_runs.py`: `create_run` signature, `BatchAlreadyRunningError`.
4. Update the two existing call sites.
5. Tests per above.
