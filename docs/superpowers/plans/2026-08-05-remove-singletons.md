# remove_singletons Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new `batch/remove_singletons.py` deletes `tmp_clusters` rows for any `cluster_id` that
has been reduced to a single member, chained onto `unregister_deleted_images.main()` (the step
that triggers the cascade-deletes creating singletons in the first place).

**Architecture:** `remove_singletons.run(session)` does one `DELETE ... WHERE cluster_id IN (SELECT
... GROUP BY cluster_id HAVING COUNT(*) = 1)` and returns a `SimpleMetricsListener`.
`unregister_deleted_images.main()` gets a `chain: bool = True` parameter (default preserves
existing scheduler/admin-controller behavior) and calls it after its own tracked block, matching
the `move_flagged.py` → `unregister_deleted_images.py` chaining pattern already established.

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-05-remove-singletons-design.md`

## Global Constraints

- No run tracking (`tracked_run`/`batch_runs`) for `remove_singletons` itself, and no admin
  registry entry — matches `clusterize.py`/`detect_file_duplicates.py`'s existing untracked-script
  tier.
- No changes to `clusterize.py`, `rebuild_duplicates.py`, `move_flagged.py`, or any threshold
  values — out of scope for this plan.
- `chain: bool = True` on `unregister_deleted_images.main()` must default to preserving existing
  behavior for every current caller (scheduler, admin controller — both call
  `main(trigger=..., run_id=...)` via `batch/run_wrapper.py` with no `chain` argument).

---

### Task 1: `batch/remove_singletons.py`

**Files:**
- Create: `batch/remove_singletons.py`
- Test: `tests/integration/test_remove_singletons.py`

**Interfaces:**
- Produces: `run(session) -> SimpleMetricsListener`. Task 2 depends on this exact name/signature —
  called as `await remove_singletons.run(session)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_remove_singletons.py`:

```python
"""
Integration tests for batch/remove_singletons.py.

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import select

from batch.remove_singletons import run
from Storage.models import Image, TmpImageClusters


@pytest.mark.asyncio(loop_scope="session")
async def test_removes_singleton_cluster(db_session):
    image = Image(filename=f"singleton-{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(TmpImageClusters(cluster_id=1, image_id=image.id))
    await db_session.flush()

    metrics = await run(db_session)

    remaining = (await db_session.execute(
        select(TmpImageClusters).where(TmpImageClusters.cluster_id == 1)
    )).scalars().all()
    assert remaining == []
    assert metrics.counters_dict() == {"removed": 1}


@pytest.mark.asyncio(loop_scope="session")
async def test_leaves_multi_member_cluster_untouched(db_session):
    image_a = Image(filename=f"a-{uuid.uuid4()}.jpg")
    image_b = Image(filename=f"b-{uuid.uuid4()}.jpg")
    db_session.add_all([image_a, image_b])
    await db_session.flush()
    db_session.add_all([
        TmpImageClusters(cluster_id=2, image_id=image_a.id),
        TmpImageClusters(cluster_id=2, image_id=image_b.id),
    ])
    await db_session.flush()

    metrics = await run(db_session)

    remaining = (await db_session.execute(
        select(TmpImageClusters).where(TmpImageClusters.cluster_id == 2)
    )).scalars().all()
    assert len(remaining) == 2
    assert metrics.counters_dict() == {"removed": 0}


@pytest.mark.asyncio(loop_scope="session")
async def test_mixed_clusters_only_singleton_removed(db_session):
    image_a = Image(filename=f"a-{uuid.uuid4()}.jpg")
    image_b = Image(filename=f"b-{uuid.uuid4()}.jpg")
    image_c = Image(filename=f"c-{uuid.uuid4()}.jpg")
    db_session.add_all([image_a, image_b, image_c])
    await db_session.flush()
    db_session.add_all([
        TmpImageClusters(cluster_id=3, image_id=image_a.id),  # singleton
        TmpImageClusters(cluster_id=4, image_id=image_b.id),  # pair
        TmpImageClusters(cluster_id=4, image_id=image_c.id),  # pair
    ])
    await db_session.flush()

    metrics = await run(db_session)

    remaining_ids = {
        row.cluster_id for row in (await db_session.execute(select(TmpImageClusters))).scalars().all()
    }
    assert remaining_ids == {4}
    assert metrics.counters_dict() == {"removed": 1}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from this task's own working directory — no `cd` needed if already there):
`DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_remove_singletons.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batch.remove_singletons'`.

- [ ] **Step 3: Implement `batch/remove_singletons.py`**

```python
import argparse
import asyncio

from sqlalchemy import delete, func, select

from config.settings import load_env
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from Storage.models import TmpImageClusters


async def run(session) -> SimpleMetricsListener:
    metrics = SimpleMetricsListener()

    singleton_cluster_ids = (
        select(TmpImageClusters.cluster_id)
        .group_by(TmpImageClusters.cluster_id)
        .having(func.count() == 1)
    )
    result = await session.execute(
        delete(TmpImageClusters).where(TmpImageClusters.cluster_id.in_(singleton_cluster_ids))
    )
    metrics.add("removed", result.rowcount)
    return metrics


async def main() -> None:
    async with AsyncSessionLocal() as session:
        metrics = await run(session)
        await session.commit()
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_remove_singletons.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add batch/remove_singletons.py tests/integration/test_remove_singletons.py
git commit -m "feat: add remove_singletons batch script"
```

---

### Task 2: Chain into `unregister_deleted_images.py`

**Files:**
- Modify: `batch/unregister_deleted_images.py`
- Modify: `tests/integration/test_unregister_deleted_images_tracking.py` (existing file this
  change breaks — see Step 2)

**Interfaces:**
- Consumes: `remove_singletons.run(session) -> SimpleMetricsListener` (Task 1).
- `main(trigger, run_id)`'s existing two parameters are unchanged; a new `chain: bool = True`
  parameter is added.

- [ ] **Step 1: Write the failing tests**

Replace `tests/integration/test_unregister_deleted_images_tracking.py`'s full content:

```python
"""
Integration tests for unregister_deleted_images.py's main() tracking behavior, and its
chained call to remove_singletons. run()'s actual file-unregistering logic is unchanged
and untested here (out of scope for this file) -- run() itself is monkeypatched to a
no-op/raising stub so these tests focus purely on whether main() creates/finishes the
right BatchRun row, and on the chaining behavior. remove_singletons.run is mocked to
return a real SimpleMetricsListener() in every test (not a bare AsyncMock's default),
matching the pattern test_move_flagged_tracking.py established for its own equivalent
chained call -- main() now unconditionally calls metrics.print() on that return value.
"""
import uuid
from unittest.mock import AsyncMock

import pytest

import batch.unregister_deleted_images as unregister_deleted_images
import batch.run_tracking as run_tracking
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchRunRepository

# tracked_run/finish_existing_run (batch/run_tracking.py) open their own fresh
# AsyncSessionLocal() connections, independent of whatever session main() itself uses.
# The db_session fixture wraps each test in an outer transaction that is only ever
# rolled back (never truly committed) so its writes are invisible to any other real
# connection -- so run_tracking's own AsyncSessionLocal must be patched too, or any
# row created directly via db_session (as in the last test below) is invisible to
# finish_existing_run's lookup on a separate connection.


@pytest.mark.asyncio(loop_scope="session")
async def test_main_self_tracks_as_manual_by_default(db_session, monkeypatch):
    monkeypatch.setattr(unregister_deleted_images, "run", AsyncMock())
    monkeypatch.setattr(unregister_deleted_images, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(
        unregister_deleted_images.remove_singletons, "run",
        AsyncMock(return_value=SimpleMetricsListener()),
    )

    await unregister_deleted_images.main()

    repo = BatchRunRepository(db_session)
    active = await repo.get_active_run(kind="unregister_deleted_images")
    # main() commits on success, so nothing should still be "active" (started)
    assert active is None


@pytest.mark.asyncio(loop_scope="session")
async def test_main_creates_completed_run_with_manual_trigger(db_session, monkeypatch):
    monkeypatch.setattr(unregister_deleted_images, "run", AsyncMock())
    monkeypatch.setattr(unregister_deleted_images, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(
        unregister_deleted_images.remove_singletons, "run",
        AsyncMock(return_value=SimpleMetricsListener()),
    )

    await unregister_deleted_images.main()

    repo = BatchRunRepository(db_session)
    most_recent = await repo.get_most_recent_run(kind="unregister_deleted_images")
    assert most_recent is not None
    assert most_recent.trigger == "manual"
    assert most_recent.status == "completed"


@pytest.mark.asyncio(loop_scope="session")
async def test_main_marks_run_failed_when_run_raises(db_session, monkeypatch):
    monkeypatch.setattr(unregister_deleted_images, "run", AsyncMock(side_effect=RuntimeError("disk full")))
    monkeypatch.setattr(unregister_deleted_images, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    remove_singletons_run = AsyncMock(return_value=SimpleMetricsListener())
    monkeypatch.setattr(unregister_deleted_images.remove_singletons, "run", remove_singletons_run)

    with pytest.raises(RuntimeError, match="disk full"):
        await unregister_deleted_images.main()

    repo = BatchRunRepository(db_session)
    most_recent = await repo.get_most_recent_run(kind="unregister_deleted_images")
    assert most_recent.status == "failed"
    assert most_recent.error == "disk full"
    # run() raised before main() ever reaches the chained call
    remove_singletons_run.assert_not_called()


@pytest.mark.asyncio(loop_scope="session")
async def test_main_with_pre_created_run_id_finishes_that_row_not_a_new_one(db_session, monkeypatch):
    monkeypatch.setattr(unregister_deleted_images, "run", AsyncMock())
    monkeypatch.setattr(unregister_deleted_images, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(
        unregister_deleted_images.remove_singletons, "run",
        AsyncMock(return_value=SimpleMetricsListener()),
    )
    repo = BatchRunRepository(db_session)
    existing_run_id = await repo.create_run(kind="unregister_deleted_images", trigger="manual")
    await db_session.commit()

    await unregister_deleted_images.main(trigger="manual", run_id=existing_run_id)

    finished = await repo.get_run(existing_run_id)
    assert finished.status == "completed"
    # No second row was created for this kind
    all_recent = await repo.get_most_recent_run(kind="unregister_deleted_images")
    assert all_recent.run_id == existing_run_id


@pytest.mark.asyncio(loop_scope="session")
async def test_chain_true_by_default_calls_remove_singletons(db_session, monkeypatch):
    monkeypatch.setattr(unregister_deleted_images, "run", AsyncMock())
    monkeypatch.setattr(unregister_deleted_images, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    remove_singletons_run = AsyncMock(return_value=SimpleMetricsListener())
    monkeypatch.setattr(unregister_deleted_images.remove_singletons, "run", remove_singletons_run)

    await unregister_deleted_images.main()

    remove_singletons_run.assert_awaited_once()


@pytest.mark.asyncio(loop_scope="session")
async def test_chain_false_skips_remove_singletons(db_session, monkeypatch):
    monkeypatch.setattr(unregister_deleted_images, "run", AsyncMock())
    monkeypatch.setattr(unregister_deleted_images, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    monkeypatch.setattr(run_tracking, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    remove_singletons_run = AsyncMock(return_value=SimpleMetricsListener())
    monkeypatch.setattr(unregister_deleted_images.remove_singletons, "run", remove_singletons_run)

    await unregister_deleted_images.main(chain=False)

    remove_singletons_run.assert_not_awaited()


def _session_ctx(session):
    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_unregister_deleted_images_tracking.py -v`
Expected: FAIL — `AttributeError: module 'batch.unregister_deleted_images' has no attribute
'remove_singletons'` (or `main() got an unexpected keyword argument 'chain'` for the two new tests).

- [ ] **Step 3: Implement the chaining in `batch/unregister_deleted_images.py`**

Replace the full file:

```python
import argparse
import asyncio
import os
import uuid

from batch import remove_singletons
from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import load_env
from Storage.db import AsyncSessionLocal
from batch.tasks.SourceTasks import UnregisterNonExisting


async def run(session, base_path):
    task = UnregisterNonExisting(session, base_path)
    await task.run()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, chain: bool = True) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(os.getenv('BASE_PATH'))
                await run(session, base_path)
    else:
        async with tracked_run(kind="unregister_deleted_images", trigger=trigger):
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(os.getenv('BASE_PATH'))
                await run(session, base_path)

    if chain:
        async with AsyncSessionLocal() as session:
            metrics = await remove_singletons.run(session)
            await session.commit()
        metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    parser.add_argument("--no-chain", action="store_true",
                        help="Skip the automatic remove_singletons cleanup after unregistering deleted images.")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(chain=not args.no_chain))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_unregister_deleted_images_tracking.py -v`
Expected: all PASS (6 tests: the 4 original plus the 2 new chain-behavior tests).

- [ ] **Step 5: Run the full `tests/integration/` root and `batch/tests/` root**

Per this project's own testing gotcha (a change to a script with an existing dedicated integration
test file needs the whole `tests/integration/` root run, not just that one file, before merging):

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/ -v`
Expected: all PASS, no regressions.

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/ -v`
Expected: all PASS (this plan touches no files under `batch/tests/`'s own root directly, but
`move_flagged.py`'s tests import `unregister_deleted_images` — confirm nothing there regresses).

- [ ] **Step 6: Commit**

```bash
git add batch/unregister_deleted_images.py tests/integration/test_unregister_deleted_images_tracking.py
git commit -m "feat: unregister_deleted_images chains remove_singletons cleanup"
```

## Self-Review Notes

- **Spec coverage:** the utility itself (Task 1), the `chain`/`--no-chain` wiring matching
  `move_flagged.py`'s established pattern exactly, and the pre-existing test file this change
  breaks (Task 2) — every part of the spec has a corresponding task.
- **Type consistency:** `remove_singletons.run(session) -> SimpleMetricsListener`'s signature and
  return type are used identically in Task 1's own tests and in Task 2's mocking/call site.
- **Existing-test audit, done during this plan's own writing (not left for the review loop):**
  found and fully specified the exact rewrite `tests/integration/test_unregister_deleted_images_tracking.py`
  needs — this is the third time in this project's history a chained-call addition has broken a
  pre-existing tracking test file (after `move_flagged.py`'s own chaining onto
  `unregister_deleted_images.py`), so this plan fixes it up front rather than relying on the SDD
  pre-flight scan or task review to catch it.
- **No `cd` prefix in any `Run:` command** — this plan's own worktree path isn't known at
  plan-writing time; every test command relies on the implementer's dispatch already establishing
  the correct working directory (a lesson from three earlier plans in this project whose `Run:`
  commands wrongly hardcoded the main checkout's path).
