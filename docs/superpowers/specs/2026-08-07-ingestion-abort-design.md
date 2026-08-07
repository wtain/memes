# Ingestion Abort — Design

Status: planned
Plan: docs/superpowers/plans/2026-08-07-ingestion-abort.md

**Date:** 2026-08-07.

Adds `batch/ingest_abort.py`, a new CLI script that abandons the currently active ingestion
run — undoing every `pending`/`rejected` image it registered and freeing the
one-active-run-per-kind lock so a new ingestion batch can start. This is one of two specs
from the "ingestion abort or incremental first step" ask; the incremental-first-step half is
a separate spec, not covered here.

---

## Motivation

`batch/ingest_hash_dedup.py` refuses to start a new ingestion run while
`BatchRunRepository.get_active_run(kind="ingestion")` returns a row — its own error message
already says "finish or abandon it before starting a new one," but no "abandon" mechanism
exists today. Since every stage after Stage 1 requires an explicit script/API call and human
review with no automatic timeout, a run that the operator no longer wants to continue (wrong
images ingested, a test batch, review found the whole batch not worth pursuing) blocks all
future ingestion indefinitely, with no way to recover except direct DB surgery.

## Scope

**In scope:** `batch/ingest_abort.py` (new), a new `IngestionRepository` method to list a
batch's abortable images, a new `RunStatus.aborted` value, and updating `CLAUDE.md`'s
ingestion pipeline documentation.

**Out of scope:**
- Undoing an already-`active` (promoted) image. Per the ingestion design spec's own "Out of
  scope" section, undo is only ever in scope before promotion — an aborted run's already-promoted
  images (partial promotion is possible mid-run, per `ingest_promote.py`'s `maybe_complete_run`)
  stay exactly as they are; they're fully independent of the run's outcome once promoted.
- `PATH_INGESTION_SOURCE/duplicates/` — Stage 1's hash-dedup rejects never became `Image` rows,
  so there's nothing to undo there. Leaving them is harmless; a re-run's hash dedup would just
  re-detect and re-move them the same way regardless.
- No API endpoint, no frontend button — CLI-only, matching every other ingestion stage script
  (`ingest_hash_dedup.py`, `ingest_find_duplicates.py`, `ingest_promote.py`, none of which are
  wired into the admin controller or frontend today).
- No interactive confirmation prompt — matches every other batch script in this codebase
  (`unregister_deleted_images.py`, `remove_singletons.py`, `move_flagged.py` all run without one);
  the operator invoking the command is the confirmation.
- No DB migration — `batch_runs.status` is a plain `String(20)` column with no DB-level enum
  type or CHECK constraint (only a Python-side `RunStatus` enum for typing), so adding
  `aborted` as a new value is a pure code change.
- No `--run-id` flag — operates on `get_active_run(kind="ingestion")` only, matching
  `ingest_promote.py`'s exact precedent. The one-active-run-per-kind lock means there's never
  more than one candidate anyway.

## Design

### `RunStatus.aborted`

`Storage/models.py`'s `RunStatus` enum gains a fourth member:

```python
class RunStatus(enum.Enum):
    started = "started"
    completed = "completed"
    failed = "failed"
    aborted = "aborted"

    def __str__(self) -> str:
        return self.value
```

The partial unique index backing the one-active-run-per-kind lock
(`ix_batch_runs_one_active_per_kind`, `postgresql_where=sa.text("status = 'started'")`) already
only cares about `status = 'started'` — any other value, including `aborted`, correctly frees the
lock with no index change needed.

### `IngestionRepository.list_abortable_images`

New method on `Backend/app/repositories/ingestion_repository.py`, alongside the existing
`list_pending_images`:

```python
async def list_abortable_images(self, batch_id):
    """Every pending or rejected image in this batch -- i.e. everything an abort should
    undo. Excludes active (promoted) images, which are out of scope for abort."""
    result = await self.session.execute(
        select(Image.id, Image.filename, Image.status)
        .where(Image.ingestion_batch_id == batch_id, Image.status.in_(["pending", "rejected"]))
        .order_by(Image.created_at)
    )
    return result.all()
```

### `BatchRunRepository.abort`

New method on `repository/batch_runs.py`, mirroring `fail()`'s shape:

```python
async def abort(self, run_id: uuid.UUID, note: str | None = None) -> None:
    run = await self._get(run_id)
    run.status = str(RunStatus.aborted)
    run.completed_at = datetime.now(timezone.utc)
    if note is not None:
        run.error = note
    await self._session.flush()
```

(Reuses the existing `error` text column for the note — same field `fail()` already writes a
message into, just with `aborted` semantics rather than a caught exception's message.)

### `batch/ingest_abort.py`

```python
import argparse
import asyncio
import os

from sqlalchemy import delete

from batch.utils.safe_move import move_without_overwrite
from Backend.app.repositories.ingestion_repository import IngestionRepository
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchRunRepository
from Storage.db import AsyncSessionLocal
from Storage.models import Image


async def run(session, source_path: str, base_path: str, batch_id) -> SimpleMetricsListener:
    """Undo every pending/rejected image in this batch: move its file back to
    source_path, then delete its row (FK cascades clean up embeddings/OCR/tmp_duplicates/
    tmp_clusters automatically -- see Storage/models.py, every images.id FK is
    ondelete='CASCADE'). Returns metrics; does not commit or touch the batch_runs row --
    the caller owns both, in one transaction (see note below on why this doesn't use
    ImagesRepository.delete_images())."""
    metrics = SimpleMetricsListener()
    repo = IngestionRepository(session)
    rows = await repo.list_abortable_images(batch_id)

    to_delete = []
    for image_id, filename, status in rows:
        src_dir = os.path.join(base_path, "rejected") if status == "rejected" else base_path
        src_path = os.path.join(src_dir, filename)
        try:
            move_without_overwrite(src_path, source_path)
            metrics.increment("moved_back")
        except Exception as e:
            print(f"Can't move {src_path} back to inbox: {e}")
            metrics.increment("error.move_failed")
        to_delete.append(image_id)

    if to_delete:
        await session.execute(delete(Image).where(Image.id.in_(to_delete)))
    metrics.add("unregistered", len(to_delete))
    return metrics


async def main(env: str | None) -> None:
    load_env(env)
    source_path = settings.get("PATH_INGESTION_SOURCE")
    if not source_path:
        raise RuntimeError("PATH_INGESTION_SOURCE is required but not set")
    base_path = settings.BASE_PATH

    async with AsyncSessionLocal() as session:
        runs_repo = BatchRunRepository(session)
        active_run = await runs_repo.get_active_run(kind="ingestion")
        if active_run is None:
            raise RuntimeError("No ingestion run is currently in progress -- nothing to abort.")

        metrics = await run(session, source_path, base_path, active_run.run_id)
        await runs_repo.abort(active_run.run_id, note="Aborted by user via ingest_abort.py")
        await session.commit()

    print(f"Aborted ingestion run {active_run.run_id}:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    asyncio.run(main(args.env))
```

`os.makedirs(source_path, exist_ok=True)` isn't needed before the move loop —
`PATH_INGESTION_SOURCE` is the same directory `ingest_hash_dedup.py` already reads from at the
start of every ingestion run, so it necessarily already exists by the time an abort is possible
(a run can't have started, let alone reached a state worth aborting, without it).

**Why not `ImagesRepository.delete_images()`:** that existing method commits internally
(`repository/images.py:196-198`). Using it here would let the image deletions commit before
`main()` reaches `runs_repo.abort(...)` — if anything failed in between (or the process died),
the images would already be gone but the run would still show `status='started'`, still holding
the one-active-run-per-kind lock, which is a worse stuck state than the one abort exists to fix.
Using a raw `delete(Image)` statement inside `run()`'s own uncommitted transaction lets `main()`'s
single `await session.commit()` cover the image deletions and the run's `aborted` status together,
atomically.

### Known limitation

File moves are real, immediate OS operations; the DB delete is staged inside the transaction
`main()`'s single `session.commit()` finalizes at the end. If the process dies after some files
have already moved back to `PATH_INGESTION_SOURCE` but before that final commit, those images'
rows still exist pointing at files no longer in `BASE_PATH`. This self-heals on the next
`unregister_deleted_images` run (which already deletes rows for images whose files don't exist) —
matching `ingest_hash_dedup.py`'s own accepted crash-safety posture (see its docstring: "not worth
over-engineering before this pipeline has run against real data"). Not addressed further here.

## Testing

`tests/integration/test_ingest_abort.py` (new, real DB per `tests/integration/conftest.py`,
matching `test_ingest_promote.py`'s precedent — this logic's correctness depends on real FK
cascade behavior, worth exercising against the actual schema, not mocks). Uses `tmp_path` for
`base_path`/`source_path`, matching `batch/tests/test_move_flagged.py`'s established real-file
pattern:

- A `pending` image with a real `Embedding`, `OCRText`, and `TmpDuplicates` row: after `run()`,
  the `Image` row and all three related rows are gone (proves cascade delete), and the file has
  moved from `base_path` to `source_path`.
- A `rejected` image: same assertions, but sourced from `base_path/rejected/` instead of
  `base_path`.
- An `active` image in the same batch (partial promotion already happened): untouched — no file
  move, row still present, status still `active`.
- A missing file (row exists, no file on disk): `run()` doesn't raise, counts
  `error.move_failed`, and the row is still deleted (matches `move_flagged.py`'s established
  "one bad file doesn't block the rest" resilience pattern).
- `main()`'s no-active-run case: raises `RuntimeError`, matching `ingest_promote.py`'s existing
  test coverage for the same scenario in `test_ingest_promote.py` (worth confirming an equivalent
  test doesn't already exist for `ingest_promote`'s no-run case before duplicating it — if it
  does, mirror its exact assertion style).
- `abort()` sets `status = 'aborted'`, `completed_at`, and `error` on the `batch_runs` row.

## Rollout

1. Add `RunStatus.aborted` to `Storage/models.py`.
2. Add `BatchRunRepository.abort()`.
3. Add `IngestionRepository.list_abortable_images()`.
4. Add `batch/ingest_abort.py` + its integration test.
5. Update `CLAUDE.md`'s ingestion pipeline documentation (the "Ingestion" block under "Batch
   pipeline (execution order)") to mention `ingest_abort.py` — run at any point before promotion
   completes, when the operator wants to abandon the batch rather than continue it.
