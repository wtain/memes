# move_flagged Resilience, Stats, and Chaining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `batch/move_flagged.py` no longer aborts the whole run on a per-file move error, persists
counts (moved / error.file_not_found / error.move_failed) onto its `batch_runs` row, and chains a
full `unregister_deleted_images` run afterward so the DB reconciles with what was actually moved.

**Architecture:** A `SimpleMetricsListener` (existing class, one new `counters_dict()` method)
accumulates counts inside `move_flagged.run()`'s per-file loop, wrapped in a per-file try/except
that never aborts the loop. `main()` persists those counts via
`BatchRunRepository.update_stats()` inside the same session block the move happened in, then —
after its own tracked run's context manager exits — calls `unregister_deleted_images.main()`
directly, inheriting the same `trigger`.

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, pytest + pytest-asyncio (mocked
session/repository, real filesystem via `tmp_path` — matching `batch/tests/`'s existing style).

**Spec:** `docs/superpowers/specs/2026-07-31-move-flagged-resilience-and-chaining-design.md`

## Global Constraints

- `os.makedirs(flagged_path, exist_ok=True)` stays unguarded — only per-file `shutil.move()`
  failures get the lenient try/except treatment; a failure creating `excluded/` itself must still
  fail the whole run.
- `unregister_deleted_images.py` itself is unchanged — only called, never modified.
- The chained `unregister_deleted_images.main()` call inherits `move_flagged`'s own `trigger`
  value exactly (not hardcoded to `"manual"`).
- `update_stats` happens *inside* the same `async with AsyncSessionLocal() as session:` block
  `run()` used, committed before that block exits — not after the tracked-run context manager has
  already closed out the row.
- No changes to `unregister_deleted_images.py`, the admin API/UI, or the scheduler.

---

### Task 1: `SimpleMetricsListener.counters_dict()`

**Files:**
- Modify: `metrics/listener.py`

**Interfaces:**
- Produces: `SimpleMetricsListener.counters_dict() -> dict[str, int]`. Task 2 depends on this
  exact method name.

- [ ] **Step 1: Add the method**

In `metrics/listener.py`, add this method to `SimpleMetricsListener` (after `print()`):

```python
    def counters_dict(self) -> dict[str, int]:
        """Snapshot of all counters as a plain dict, for persisting elsewhere (e.g.
        batch_runs.stats) -- print() is the only other consumer today."""
        return dict(self._counters)
```

- [ ] **Step 2: Manual sanity check**

Run: `cd H:\workspace_sandbox\memes && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -c "from metrics.listener import SimpleMetricsListener; m = SimpleMetricsListener(); m.increment('a'); m.increment('a'); m.increment('b'); print(m.counters_dict())"`
Expected output: `{'a': 2, 'b': 1}`

- [ ] **Step 3: Commit**

```bash
git add metrics/listener.py
git commit -m "feat: add SimpleMetricsListener.counters_dict()"
```

---

### Task 2: `move_flagged.py` — per-file resilience, stats, chained unregister

**Files:**
- Modify: `batch/move_flagged.py`
- Test: `batch/tests/test_move_flagged.py`

**Interfaces:**
- Consumes: `SimpleMetricsListener.counters_dict()` (Task 1); `BatchRunRepository.update_stats`
  (existing, `repository/batch_runs.py`); `unregister_deleted_images.main(trigger, run_id=None)`
  (existing, unchanged).
- Produces: `run(session, base_path) -> SimpleMetricsListener` (changed return type — was `None`).
  `main(trigger, run_id)` unchanged signature.

- [ ] **Step 1: Write the failing tests**

Create `batch/tests/test_move_flagged.py`:

```python
"""
Unit tests for batch/move_flagged.py's run() (per-file resilience + metrics) and main()
(stats persistence + chained unregister_deleted_images call). No real DB -- session/repo
interactions are mocked, matching batch/tests/test_run_tracking.py's style. run()'s
filesystem behavior uses real tmp_path files, matching
batch/tests/test_move_reference_duplicates.py's style.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.move_flagged import main, run


def _mock_session(filenames):
    session = AsyncMock()
    session.execute = AsyncMock(return_value=[(f,) for f in filenames])
    return session


def _ctx(session):
    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestRun:
    @pytest.mark.asyncio
    async def test_missing_file_does_not_abort_remaining_moves(self, tmp_path):
        (tmp_path / "b.jpg").write_bytes(b"x")
        session = _mock_session(["missing.jpg", "b.jpg"])

        metrics = await run(session, str(tmp_path))

        assert metrics.counters_dict() == {"error.file_not_found": 1, "moved": 1}
        assert (tmp_path / "excluded" / "b.jpg").exists()

    @pytest.mark.asyncio
    async def test_other_move_error_is_counted_and_does_not_abort(self, tmp_path, monkeypatch):
        (tmp_path / "a.jpg").write_bytes(b"x")
        (tmp_path / "b.jpg").write_bytes(b"x")
        session = _mock_session(["a.jpg", "b.jpg"])

        import batch.move_flagged as module
        real_move = module.shutil.move

        def fake_move(src, dst):
            if str(src).endswith("a.jpg"):
                raise PermissionError("locked")
            return real_move(src, dst)

        monkeypatch.setattr(module.shutil, "move", fake_move)

        metrics = await run(session, str(tmp_path))

        assert metrics.counters_dict() == {"error.move_failed": 1, "moved": 1}
        assert (tmp_path / "excluded" / "b.jpg").exists()
        assert not (tmp_path / "excluded" / "a.jpg").exists()


class TestMain:
    @pytest.mark.asyncio
    async def test_tracked_run_path_persists_stats_and_chains_unregister(self, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"x")
        session = _mock_session(["a.jpg"])
        repo = AsyncMock()
        unregister_main = AsyncMock()

        class _FakeTrackedRun:
            async def __aenter__(self_inner):
                return "run-1"

            async def __aexit__(self_inner, *exc_info):
                return False

        import batch.move_flagged as module

        with patch.object(module, "settings") as mock_settings, \
             patch.object(module, "tracked_run", return_value=_FakeTrackedRun()), \
             patch.object(module, "AsyncSessionLocal", return_value=_ctx(session)), \
             patch.object(module, "BatchRunRepository", return_value=repo), \
             patch.object(module, "unregister_deleted_images") as mock_unregister:
            mock_settings.BASE_PATH = str(tmp_path)
            mock_unregister.main = unregister_main

            await main(trigger="scheduled")

        repo.update_stats.assert_awaited_once_with("run-1", moved=1)
        unregister_main.assert_awaited_once_with(trigger="scheduled")

    @pytest.mark.asyncio
    async def test_finish_existing_run_path_persists_stats_and_chains_unregister(self, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"x")
        session = _mock_session(["a.jpg"])
        repo = AsyncMock()
        unregister_main = AsyncMock()

        class _FakeFinishExistingRun:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, *exc_info):
                return False

        import batch.move_flagged as module

        with patch.object(module, "settings") as mock_settings, \
             patch.object(module, "finish_existing_run", return_value=_FakeFinishExistingRun()), \
             patch.object(module, "AsyncSessionLocal", return_value=_ctx(session)), \
             patch.object(module, "BatchRunRepository", return_value=repo), \
             patch.object(module, "unregister_deleted_images") as mock_unregister:
            mock_settings.BASE_PATH = str(tmp_path)
            mock_unregister.main = unregister_main

            await main(trigger="manual", run_id="existing-run-1")

        repo.update_stats.assert_awaited_once_with("existing-run-1", moved=1)
        unregister_main.assert_awaited_once_with(trigger="manual")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd H:\workspace_sandbox\memes && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_move_flagged.py -v`
Expected: FAIL — `run()` currently returns `None` (no `metrics` object), and `main()` doesn't call
`update_stats` or `unregister_deleted_images.main()` yet.

- [ ] **Step 3: Implement the new `batch/move_flagged.py`**

Replace the full file with:

```python
import argparse
import asyncio
import os
import shutil
import uuid

from sqlalchemy import select

from batch import unregister_deleted_images
from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchRunRepository
from Storage.db import AsyncSessionLocal
from Storage.models import Image, ImageExtras


async def run(session, base_path) -> SimpleMetricsListener:
    metrics = SimpleMetricsListener()

    query = (
        select(
            Image.filename,
        )
        .join(ImageExtras, ImageExtras.image_id == Image.id)
        .where(ImageExtras.flagged == True)
    )
    images = await session.execute(query)

    flagged_path = os.path.join(base_path, "excluded")
    os.makedirs(flagged_path, exist_ok=True)

    for (filename, ) in images:
        path_from = os.path.join(base_path, filename)
        path_to = os.path.join(flagged_path, filename)
        try:
            print(f"Moving {filename} from {path_from} to {path_to}")
            shutil.move(path_from, path_to)
            metrics.increment("moved")
        except FileNotFoundError as e:
            print(f"Skipping {filename}: not found ({e})")
            metrics.increment("error.file_not_found")
        except Exception as e:
            print(f"Skipping {filename}: move failed ({e})")
            metrics.increment("error.move_failed")

    return metrics


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(settings.BASE_PATH)
                metrics = await run(session, base_path)
                await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
                await session.commit()
    else:
        async with tracked_run(kind="move_flagged", trigger=trigger) as run_id:
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(settings.BASE_PATH)
                metrics = await run(session, base_path)
                await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
                await session.commit()

    metrics.print()
    await unregister_deleted_images.main(trigger=trigger)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd H:\workspace_sandbox\memes && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_move_flagged.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full `batch/tests/` root**

Run: `cd H:\workspace_sandbox\memes && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/ -v`
Expected: all PASS, no regressions in the other files in this root.

- [ ] **Step 6: Commit**

```bash
git add batch/move_flagged.py batch/tests/test_move_flagged.py
git commit -m "feat: move_flagged tolerates per-file errors, reports stats, chains unregister_deleted_images"
```

## Self-Review Notes

- **Spec coverage:** per-file try/except with the two named error buckets (Task 2), `os.makedirs`
  left unguarded (Task 2, unchanged from the existing file), `counters_dict()` (Task 1),
  `update_stats` inside the same session block before commit (Task 2), chained
  `unregister_deleted_images.main(trigger=trigger)` after both `main()` branches (Task 2) — every
  section of the spec has a corresponding step.
- **Type consistency:** `run()`'s new return type (`SimpleMetricsListener`) is used consistently by
  both `main()` branches and by every test in `TestRun`/`TestMain`; `counters_dict()`'s name matches
  between Task 1's implementation and every call site in Task 2 and its tests.
- **Caught during this plan's own writing:** an early draft of `main()` computed `metrics.print()`
  and the chained `unregister_deleted_images` call *inside* the tracked-run block, using a session
  that had already been used for `update_stats` — reordered so `metrics.print()` and the chained
  call both happen after the block exits (matching the spec exactly: the chained run must be fully
  independent of `move_flagged`'s own tracked-run context), while `update_stats` itself stays
  inside, before that block's session commits.
