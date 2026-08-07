# Ingestion Abort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `batch/ingest_abort.py`, a CLI script that abandons the currently active ingestion run — undoing every `pending`/`rejected` image it registered and freeing the one-active-run-per-kind lock.

**Architecture:** Two tasks. Task 1 adds the three small, tightly-coupled prerequisites (a new `RunStatus.aborted` enum value, `BatchRunRepository.abort()`, `IngestionRepository.list_abortable_images()`) with direct test coverage in their existing dedicated test files. Task 2 adds the script itself (`batch/ingest_abort.py`), its own integration test file, and the `CLAUDE.md` documentation update — the one piece with real, interesting logic (file-move resilience, atomic delete+abort, the no-active-run error path).

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, pytest + pytest-asyncio against a real disposable test DB (`ocrdb_test`), this repo's existing `batch/utils/safe_move.move_without_overwrite` and `metrics/listener.SimpleMetricsListener` utilities.

## Global Constraints

- No DB migration — `batch_runs.status` is a plain `String(20)` column with no DB-level enum type or CHECK constraint, so adding `RunStatus.aborted` is a pure code change.
- `batch/ingest_abort.py` operates only on `BatchRunRepository.get_active_run(kind="ingestion")` — no `--run-id` flag, matching `batch/ingest_promote.py`'s exact precedent.
- Never undo an already-`active` (promoted) image — only `pending`/`rejected` images in the batch are in scope.
- Do **not** use `ImagesRepository.delete_images()` for the row deletion in `ingest_abort.py` — it calls `session.commit()` internally (`repository/images.py:196-198`), which would let images get deleted before the run is marked `aborted` if anything failed in between. Use a raw `delete(Image)` statement inside the script's own uncommitted transaction instead, so `main()`'s single final `await session.commit()` covers both the deletions and the run's `aborted` status atomically.
- No interactive confirmation prompt and no API endpoint / frontend button — CLI-only, matching every other ingestion stage script (`ingest_hash_dedup.py`, `ingest_find_duplicates.py`, `ingest_promote.py`).
- `PATH_INGESTION_SOURCE/duplicates/` is out of scope — Stage 1's hash-dedup rejects never became `Image` rows, so there's nothing there to undo.

---

### Task 1: RunStatus.aborted, BatchRunRepository.abort(), IngestionRepository.list_abortable_images()

**Files:**
- Modify: `Storage/models.py:443-449` (the `RunStatus` enum)
- Modify: `repository/batch_runs.py` (add `abort()` after `fail()`, currently at lines 47-53)
- Modify: `Backend/app/repositories/ingestion_repository.py` (add `list_abortable_images()` after `list_pending_images()`, currently at lines 28-34)
- Test: `tests/integration/test_batch_runs_repository.py` (add a test for `abort()`)
- Test: `tests/integration/test_backend_ingestion_repository.py` (add tests for `list_abortable_images()`)

**Interfaces:**
- Produces: `RunStatus.aborted` (str value `"aborted"`, used via `str(RunStatus.aborted)` matching every other status write in this codebase). `BatchRunRepository.abort(run_id: uuid.UUID, note: str | None = None) -> None`. `IngestionRepository.list_abortable_images(batch_id) -> Sequence[Row]` yielding `(id, filename, status)` tuples, `status` always `"pending"` or `"rejected"`.
- Consumes: nothing from other tasks — this task's additions are pure prerequisites Task 2 depends on.

- [x] **Step 1: Read the current `RunStatus` enum**

Run: `grep -n "class RunStatus" -A 8 Storage/models.py`
Expected output confirms the current enum:
```python
class RunStatus(enum.Enum):
    started = "started"
    completed = "completed"
    failed = "failed"

    def __str__(self) -> str:
        return self.value
```

- [x] **Step 2: Add `aborted` to the enum**

In `Storage/models.py`, change:
```python
class RunStatus(enum.Enum):
    started = "started"
    completed = "completed"
    failed = "failed"

    def __str__(self) -> str:
        return self.value
```
to:
```python
class RunStatus(enum.Enum):
    started = "started"
    completed = "completed"
    failed = "failed"
    aborted = "aborted"

    def __str__(self) -> str:
        return self.value
```

- [x] **Step 3: Write the failing test for `BatchRunRepository.abort()`**

Add to `tests/integration/test_batch_runs_repository.py` (it already imports `pytest` and `BatchRunRepository` — no new imports needed):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_abort_marks_status_aborted_with_note(db_session):
    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")

    await repo.abort(run_id, note="Aborted by user via ingest_abort.py")

    run = await repo.get_run(run_id)
    assert run.status == "aborted"
    assert run.completed_at is not None
    assert run.error == "Aborted by user via ingest_abort.py"
```

- [x] **Step 4: Run the test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_batch_runs_repository.py::test_abort_marks_status_aborted_with_note -v`
Expected: FAIL with `AttributeError: 'BatchRunRepository' object has no attribute 'abort'`

- [x] **Step 5: Add `BatchRunRepository.abort()`**

In `repository/batch_runs.py`, add this method immediately after `fail()` (currently ending at line 53, right before `async def get_run`):

```python
    async def abort(self, run_id: uuid.UUID, note: str | None = None) -> None:
        run = await self._get(run_id)
        run.status = str(RunStatus.aborted)
        run.completed_at = datetime.now(timezone.utc)
        if note is not None:
            run.error = note
        await self._session.flush()
```

- [x] **Step 6: Run the test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_batch_runs_repository.py::test_abort_marks_status_aborted_with_note -v`
Expected: PASS

- [x] **Step 7: Write the failing tests for `IngestionRepository.list_abortable_images()`**

Add to `tests/integration/test_backend_ingestion_repository.py` (it already has `_make_run` and `_make_image` helpers at the top — reuse them, no new imports needed):

```python
# --------------------------------------------------------------------------
# list_abortable_images
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_list_abortable_images_includes_pending_and_rejected(db_session):
    batch_id = await _make_run(db_session)
    pending = await _make_image(db_session, "pending", batch_id)
    rejected = await _make_image(db_session, "rejected", batch_id)

    repo = IngestionRepository(db_session)
    rows = await repo.list_abortable_images(batch_id)

    ids_and_statuses = {(row[0], row[2]) for row in rows}
    assert ids_and_statuses == {(pending, "pending"), (rejected, "rejected")}


@pytest.mark.asyncio(loop_scope="session")
async def test_list_abortable_images_excludes_active_and_other_batches(db_session):
    batch_id = await _make_run(db_session)
    pending = await _make_image(db_session, "pending", batch_id)
    await _make_image(db_session, "active", batch_id)  # promoted -- must not be included

    other_batch_id = await _make_run(db_session)
    await _make_image(db_session, "pending", other_batch_id)  # different batch -- must not be included

    repo = IngestionRepository(db_session)
    rows = await repo.list_abortable_images(batch_id)

    assert [row[0] for row in rows] == [pending]
```

Note: `_make_run` calls `BatchRunRepository(session).create_run(kind="ingestion", ...)`, and this codebase's `batch_runs` table has a partial unique index allowing only one `status='started'` row per `kind` at a time (see `Storage/models.py`'s `ix_batch_runs_one_active_per_kind`). The second test above calls `_make_run` twice in the same test — this is safe because neither run is ever committed to `started`-conflicting state across tests (each test runs in its own rolled-back savepoint per `tests/integration/conftest.py`), and both calls happen within the same test's single transaction where two `started` `kind="ingestion"` rows *would* violate the constraint — so if this test fails with an `IntegrityError`, that confirms the fixture isolation assumption was wrong, not a bug in `list_abortable_images` itself; if that happens, `abort()` (or `commit()`/`fail()`) the first run before creating the second.

- [x] **Step 8: Run the tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_ingestion_repository.py -k list_abortable_images -v`
Expected: FAIL with `AttributeError: 'IngestionRepository' object has no attribute 'list_abortable_images'`

- [x] **Step 9: Add `IngestionRepository.list_abortable_images()`**

In `Backend/app/repositories/ingestion_repository.py`, add this method immediately after `list_pending_images()` (currently ending at line 34, right before `async def get_tier_candidate_rows`):

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

- [x] **Step 10: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_ingestion_repository.py -k list_abortable_images -v`
Expected: PASS (2 passed)

- [x] **Step 11: Run the full `tests/integration/` root**

This task touches `Storage/models.py` (a shared model file) and two repository files used across the ingestion pipeline — per this repo's own `CLAUDE.md` guidance ("Running the right test scope"), that warrants the full root, not just the two files touched above.

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
Expected: all pass, no new failures.

- [x] **Step 12: Commit**

```bash
git add Storage/models.py repository/batch_runs.py Backend/app/repositories/ingestion_repository.py tests/integration/test_batch_runs_repository.py tests/integration/test_backend_ingestion_repository.py
git commit -m "feat: add RunStatus.aborted, BatchRunRepository.abort, IngestionRepository.list_abortable_images"
```

---

### Task 2: batch/ingest_abort.py

**Files:**
- Create: `batch/ingest_abort.py`
- Test: `tests/integration/test_ingest_abort.py`
- Modify: `CLAUDE.md` (ingestion pipeline documentation, in the "Batch pipeline (execution order)" section)

**Interfaces:**
- Consumes: `RunStatus.aborted` (via `BatchRunRepository.abort()`, not referenced directly), `BatchRunRepository.get_active_run(kind="ingestion")` / `.abort(run_id, note=None)` (Task 1), `IngestionRepository.list_abortable_images(batch_id)` (Task 1, returns `(id, filename, status)` rows), `batch.utils.safe_move.move_without_overwrite(src_path: str, dest_dir: str) -> str` (existing), `metrics.listener.SimpleMetricsListener` (existing: `.increment(name)`, `.add(name, value)`, `.print()`, `.counters_dict()`).
- Produces: `run(session, source_path: str, base_path: str, batch_id) -> SimpleMetricsListener` and `main(env: str | None) -> None`, both importable by the test file — no other task depends on these.

- [x] **Step 1: Write the failing tests**

Create `tests/integration/test_ingest_abort.py`:

```python
"""
Integration tests for batch/ingest_abort.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.
Uses tmp_path for base_path/source_path, matching batch/tests/test_move_flagged.py's
established real-file pattern -- this script's correctness depends on real FK cascade
behavior (embeddings/ocr_texts/tmp_duplicates all cascade-delete on images.id), worth
exercising against the actual schema rather than mocks.
"""
import uuid

import pytest

from batch.ingest_abort import run
from repository.batch_runs import BatchRunRepository
from Storage.models import Embedding, Image, OCRText, TmpDuplicates


async def _make_run(session) -> uuid.UUID:
    return await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_a_review")


async def _make_image(session, status: str, batch_id, filename: str) -> uuid.UUID:
    image = Image(filename=filename, status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    return image.id


@pytest.mark.asyncio(loop_scope="session")
async def test_pending_image_moved_back_and_row_and_related_rows_deleted(db_session, tmp_path):
    base_path = tmp_path / "base"
    base_path.mkdir()
    source_path = tmp_path / "inbox"
    source_path.mkdir()
    (base_path / "pending.jpg").write_bytes(b"x")

    batch_id = await _make_run(db_session)
    image_id = await _make_image(db_session, "pending", batch_id, "pending.jpg")
    db_session.add(Embedding(image_id=image_id, embedding=[0.0] * 512))
    db_session.add(OCRText(image_id=image_id, text="hello", confidence=0.9, language="en"))
    await db_session.flush()
    other_id = await _make_image(db_session, "active", None, "other.jpg")
    db_session.add(TmpDuplicates(
        image_id1=min(image_id, other_id), image_id2=max(image_id, other_id),
        distance=0.1, match_source="cross_corpus",
    ))
    await db_session.flush()

    metrics = await run(db_session, str(source_path), str(base_path), batch_id)

    assert metrics.counters_dict() == {"moved_back": 1, "unregistered": 1}
    assert (source_path / "pending.jpg").exists()
    assert not (base_path / "pending.jpg").exists()
    assert (await db_session.get(Image, image_id)) is None
    embeddings_left = (await db_session.execute(
        Embedding.__table__.select().where(Embedding.image_id == image_id)
    )).all()
    assert embeddings_left == []
    ocr_left = (await db_session.execute(
        OCRText.__table__.select().where(OCRText.image_id == image_id)
    )).all()
    assert ocr_left == []
    pairs_left = (await db_session.execute(
        TmpDuplicates.__table__.select().where(
            (TmpDuplicates.image_id1 == image_id) | (TmpDuplicates.image_id2 == image_id)
        )
    )).all()
    assert pairs_left == []


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_image_sourced_from_rejected_subdir(db_session, tmp_path):
    base_path = tmp_path / "base"
    (base_path / "rejected").mkdir(parents=True)
    source_path = tmp_path / "inbox"
    source_path.mkdir()
    (base_path / "rejected" / "rej.jpg").write_bytes(b"x")

    batch_id = await _make_run(db_session)
    await _make_image(db_session, "rejected", batch_id, "rej.jpg")

    metrics = await run(db_session, str(source_path), str(base_path), batch_id)

    assert metrics.counters_dict() == {"moved_back": 1, "unregistered": 1}
    assert (source_path / "rej.jpg").exists()
    assert not (base_path / "rejected" / "rej.jpg").exists()


@pytest.mark.asyncio(loop_scope="session")
async def test_active_image_in_same_batch_untouched(db_session, tmp_path):
    base_path = tmp_path / "base"
    base_path.mkdir()
    source_path = tmp_path / "inbox"
    source_path.mkdir()
    (base_path / "promoted.jpg").write_bytes(b"x")

    batch_id = await _make_run(db_session)
    image_id = await _make_image(db_session, "active", batch_id, "promoted.jpg")

    metrics = await run(db_session, str(source_path), str(base_path), batch_id)

    assert metrics.counters_dict() == {"unregistered": 0}
    assert (base_path / "promoted.jpg").exists()
    assert not (source_path / "promoted.jpg").exists()
    assert (await db_session.get(Image, image_id)) is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_file_does_not_abort_remaining_and_row_still_deleted(db_session, tmp_path):
    base_path = tmp_path / "base"
    base_path.mkdir()
    source_path = tmp_path / "inbox"
    source_path.mkdir()
    # no file written for this image -- simulates a file already missing on disk

    batch_id = await _make_run(db_session)
    image_id = await _make_image(db_session, "pending", batch_id, "missing.jpg")

    metrics = await run(db_session, str(source_path), str(base_path), batch_id)

    assert metrics.counters_dict() == {"error.move_failed": 1, "unregistered": 1}
    assert (await db_session.get(Image, image_id)) is None
```

Note: this file tests `run()` only, not `main()`. Neither sibling ingestion script's own test suite
(`tests/integration/test_ingest_promote.py`, and `ingest_hash_dedup.py` has no test file testing
its `main()` either) tests its `main()` — both test only the underlying functions `main()` calls,
and `ingest_promote.py`'s own "no active run" `RuntimeError` path is likewise untested in its
suite. `main()`'s job here is a thin wrapper (`load_env`, read `PATH_INGESTION_SOURCE`/`BASE_PATH`,
check `get_active_run`, call `run()`, call `abort()`, commit, print) with no branching logic of its
own beyond the no-active-run guard already established as out-of-scope-to-test at this layer in
this codebase — matching that precedent here rather than introducing new `main()`-level test
scaffolding (mocked `AsyncSessionLocal`/`load_env`) that doesn't exist for either sibling script.

- [x] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingest_abort.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batch.ingest_abort'`

- [x] **Step 3: Create `batch/ingest_abort.py`**

```python
"""
Ingestion abort: abandon the currently active ingestion run.

Undoes every pending/rejected image the run registered -- moves each file back to
PATH_INGESTION_SOURCE and deletes its Image row (every images.id FK in Storage/models.py
is ondelete='CASCADE', so embeddings/ocr_texts/tmp_duplicates/tmp_clusters clean up
automatically). Marks the batch_runs row 'aborted', freeing the one-active-run-per-kind
lock so a new ingestion run can start. Already-active (promoted) images in the batch are
never touched -- undoing a promotion is out of scope, see
docs/superpowers/specs/2026-08-07-ingestion-abort-design.md.

Known limitation: file moves are real, immediate OS operations, but the DB delete is
staged inside main()'s single final commit. If the process dies after some files have
already moved back but before that commit, those images' rows still exist pointing at
files no longer in BASE_PATH -- this self-heals on the next unregister_deleted_images run
(which already deletes rows for images whose files don't exist), matching
ingest_hash_dedup.py's own accepted crash-safety posture. Not addressed further here.
"""
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
    source_path, then delete its row. Returns metrics; does not commit or touch the
    batch_runs row -- the caller owns both, in one transaction. Does not use
    ImagesRepository.delete_images() -- that method commits internally, which would
    break atomicity with the caller's abort() call (see this module's docstring)."""
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

- [x] **Step 4: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingest_abort.py -v`
Expected: all pass (4 passed)

- [x] **Step 5: Run the full `tests/integration/` root**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
Expected: all pass, no new failures.

- [x] **Step 6: Also run `batch/tests/`**

Run: `pytest batch/tests/`
Expected: all pass (this task doesn't touch anything under `batch/tests/`'s own scope, but this repo's own `CLAUDE.md` gotcha about never combining `Backend/tests/`/`tests/integration/`/`batch/tests/` in one invocation means running it as its own separate command here, as a sanity check before merge).

- [x] **Step 7: Update `CLAUDE.md`'s ingestion pipeline documentation**

Read `CLAUDE.md`'s "Batch pipeline (execution order)" section, specifically the `# Ingestion` block (the multi-line comment describing `ingest_hash_dedup` → `build_image_embeddings --status pending --incremental` → `extract_text_from_memes --status pending` → `ingest_find_duplicates --tier tier_a` → `ingest_find_duplicates --tier tier_b` → `ingest_promote`). Add one more entry after `ingest_promote`'s description, documenting `ingest_abort` as the "abandon this run instead of continuing it" alternative available at any point before promotion completes — for example:

```
ingest_abort                → Abandons the currently active ingestion run instead of continuing
                               it: undoes every pending/rejected image it registered (moves each
                               file back to PATH_INGESTION_SOURCE, deletes its row -- FK cascades
                               clean up embeddings/OCR/tmp_duplicates automatically) and marks the
                               run `aborted`, freeing the one-active-run-per-kind lock. Never
                               touches already-`active` (promoted) images in the batch. Can be run
                               at any point before the run completes -- right after Stage 1, mid-
                               review, etc.
```

Match the exact indentation and wrapping style of the surrounding ingestion block (the run-order comment already in `CLAUDE.md`'s "Batch pipeline (execution order)" section) rather than the snippet's own line breaks verbatim.

- [x] **Step 8: Commit**

```bash
git add batch/ingest_abort.py tests/integration/test_ingest_abort.py CLAUDE.md
git commit -m "feat: add ingest_abort batch script to abandon the active ingestion run"
```
