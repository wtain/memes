# Universal Batch Run Wrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make run-tracking generic and correct for `trends_batch`, `move_flagged`, and `unregister_deleted_images` across all three invocation paths (direct CLI, scheduler, later the admin controller), via a shared tracking helper, an externalized/hot-reloadable script registry, and a new subprocess wrapper — without losing the ability to run any of these scripts by hand exactly as today.

**Architecture:** `batch/registry.py` (`BatchRegistry`, YAML-backed, re-read on every call) is the single allow-list mapping a public script name to a module + `BatchRun.kind`. `batch/run_tracking.py` provides two async context managers (`tracked_run` for self-created runs, `finish_existing_run` for pre-created ones). Each script gets a `main(trigger, run_id=None)` entry point built on those. `batch/run_wrapper.py` is a new CLI that resolves a script via the registry and calls its `main()`. `scheduler.py` is updated to invoke the wrapper instead of the raw module.

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, PyYAML, pytest + `pytest-asyncio` (`Backend/tests/` mocked-DB style for pure logic, `tests/integration/` for real-DB behavior).

**Spec:** `docs/superpowers/specs/2026-07-28-batch-run-wrapper-design.md`

**Depends on:** `docs/superpowers/plans/2026-07-28-batch-run-trigger-tracking.md` (must be implemented
first — this plan's every `create_run()` call requires `trigger`, and relies on
`BatchAlreadyRunningError` existing).

## Global Constraints

- `batch/registry.py`'s `BatchRegistry` re-reads its YAML file(s) from disk on every call (`get`,
  `all_names`, `name_for_kind`) — no caching, so a registry edit takes effect without restarting any
  long-running process (the admin API, the scheduler).
- `import_module`/subprocess invocation must only ever receive a value that came from
  `BatchRegistry` — never a raw client-/config-supplied string used directly as a module path.
- Direct CLI invocation (`python -m batch.<script> --env <env>`) for all three scripts must keep
  working exactly as today, self-tracked with `trigger="manual"`.
- `move_flagged.py` and `unregister_deleted_images.py`'s existing `run(session, base_path)`
  functions are untouched — only `main()` changes for them.
- `trends_batch.py`'s refactor must not change its actual scraping/tagging behavior — only how the
  work is invoked/tracked (its `process_source` function, and the trend-fetching logic itself, are
  moved, not altered).
- No new third-party dependency (PyYAML is already a transitive dependency via Dynaconf; verify
  it's importable directly rather than only through Dynaconf before relying on that assumption).

---

### Task 1: `batch/registry.py` — externalized, hot-reloadable script allow-list

**Files:**
- Create: `environments/batch_registry.yaml`
- Create: `batch/registry.py`
- Test: `batch/tests/test_registry.py`

**Interfaces:**
- Produces: `BatchRegistry(base_dir: Path = Path("environments"))` with `.get(script_name: str) -> dict | None`, `.all_names() -> list[str]`, `.name_for_kind(kind: str) -> str | None`. Every later task in this plan (and the admin controller spec after it) depends on exactly these three method signatures.

- [ ] **Step 1: Write the failing test**

Create `environments/batch_registry.yaml` first (needed for the "real file" tests below):

```yaml
trends_batch:
  module: batch.trends_batch
  kind: trends
move_flagged:
  module: batch.move_flagged
  kind: move_flagged
unregister_deleted_images:
  module: batch.unregister_deleted_images
  kind: unregister_deleted_images
```

Create `batch/tests/test_registry.py`:

```python
"""
Unit tests for batch/registry.py's BatchRegistry -- a hot-reloadable, YAML-backed
allow-list. Uses tmp_path fixture files rather than the real environments/ directory,
so these tests don't depend on (or risk breaking) the real registry contents.
"""
import pytest

from batch.registry import BatchRegistry


def _write_common(base_dir, content):
    (base_dir / "batch_registry.yaml").write_text(content)


class TestBatchRegistry:
    def test_get_returns_entry_for_known_script(self, tmp_path):
        _write_common(tmp_path, "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n")
        registry = BatchRegistry(base_dir=tmp_path)

        entry = registry.get("trends_batch")

        assert entry == {"module": "batch.trends_batch", "kind": "trends"}

    def test_get_returns_none_for_unknown_script(self, tmp_path):
        _write_common(tmp_path, "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n")
        registry = BatchRegistry(base_dir=tmp_path)

        assert registry.get("does_not_exist") is None

    def test_all_names_lists_every_entry(self, tmp_path):
        _write_common(
            tmp_path,
            "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n"
            "move_flagged:\n  module: batch.move_flagged\n  kind: move_flagged\n",
        )
        registry = BatchRegistry(base_dir=tmp_path)

        assert set(registry.all_names()) == {"trends_batch", "move_flagged"}

    def test_name_for_kind_reverse_lookup(self, tmp_path):
        _write_common(tmp_path, "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n")
        registry = BatchRegistry(base_dir=tmp_path)

        assert registry.name_for_kind("trends") == "trends_batch"
        assert registry.name_for_kind("no_such_kind") is None

    def test_reads_fresh_on_every_call_no_caching(self, tmp_path):
        _write_common(tmp_path, "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n")
        registry = BatchRegistry(base_dir=tmp_path)
        assert registry.get("move_flagged") is None

        _write_common(
            tmp_path,
            "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n"
            "move_flagged:\n  module: batch.move_flagged\n  kind: move_flagged\n",
        )

        # Same BatchRegistry instance, no restart/reload call -- must see the edit.
        assert registry.get("move_flagged") == {"module": "batch.move_flagged", "kind": "move_flagged"}

    def test_per_environment_override_extends_common(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APP_ENV", "metal")
        _write_common(tmp_path, "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n")
        (tmp_path / "batch_registry.metal.yaml").write_text(
            "metal_only_job:\n  module: batch.metal_only\n  kind: metal_only\n"
        )
        registry = BatchRegistry(base_dir=tmp_path)

        assert set(registry.all_names()) == {"trends_batch", "metal_only_job"}

    def test_missing_per_environment_override_file_is_fine(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APP_ENV", "it")
        _write_common(tmp_path, "trends_batch:\n  module: batch.trends_batch\n  kind: trends\n")
        registry = BatchRegistry(base_dir=tmp_path)  # no batch_registry.it.yaml exists

        assert registry.all_names() == ["trends_batch"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batch.registry'`.

- [ ] **Step 3: Implement `batch/registry.py`**

```python
import os
from pathlib import Path

import yaml


class BatchRegistry:
    def __init__(self, base_dir: Path = Path("environments")):
        self._base_dir = base_dir

    def get(self, script_name: str) -> dict | None:
        """Fresh read on every call -- editing the registry file takes effect
        immediately, no backend/scheduler restart needed."""
        return self._load().get(script_name)

    def all_names(self) -> list[str]:
        return list(self._load().keys())

    def name_for_kind(self, kind: str) -> str | None:
        """Reverse lookup -- BatchRun.kind -> public script name."""
        for name, entry in self._load().items():
            if entry["kind"] == kind:
                return name
        return None

    def _load(self) -> dict:
        common = self._read_yaml(self._base_dir / "batch_registry.yaml")
        env = os.environ.get("APP_ENV")
        override = self._read_yaml(self._base_dir / f"batch_registry.{env}.yaml") if env else {}
        return {**common, **override}

    @staticmethod
    def _read_yaml(path: Path) -> dict:
        if not path.exists():
            return {}
        with open(path) as f:
            return yaml.safe_load(f) or {}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_registry.py -v`
Expected: all 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add environments/batch_registry.yaml batch/registry.py batch/tests/test_registry.py
git commit -m "feat: add externalized, hot-reloadable BatchRegistry"
```

---

### Task 2: `batch/run_tracking.py` — shared tracking helper

**Files:**
- Create: `batch/run_tracking.py`
- Test: `batch/tests/test_run_tracking.py`

**Interfaces:**
- Consumes: `BatchRunRepository.create_run(kind, trigger, stage=None)`, `.commit(run_id, stats=None)`, `.fail(run_id, error=None)` (all from the trigger-tracking plan), `Storage.db.AsyncSessionLocal`.
- Produces: `tracked_run(kind: str, trigger: str)` and `finish_existing_run(run_id: uuid.UUID)`, both async context managers yielding (`tracked_run` yields the new `run_id`; `finish_existing_run` yields nothing). Task 3/4/5 use both.

- [ ] **Step 1: Write the failing tests**

Create `batch/tests/test_run_tracking.py`:

```python
"""
Unit tests for batch/run_tracking.py's two tracking context managers. Repository/session
interactions are mocked -- no real DB, matching Backend/tests/test_scheduler.py's style.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.run_tracking import finish_existing_run, tracked_run


class TestTrackedRun:
    async def test_creates_run_and_commits_on_success(self):
        repo = AsyncMock()
        repo.create_run.return_value = "new-run-id"
        session = AsyncMock()

        with patch("batch.run_tracking.AsyncSessionLocal", return_value=_ctx(session)), \
             patch("batch.run_tracking.BatchRunRepository", return_value=repo):
            async with tracked_run(kind="trends", trigger="manual") as run_id:
                assert run_id == "new-run-id"

        repo.create_run.assert_awaited_once_with(kind="trends", trigger="manual")
        repo.commit.assert_awaited_once_with("new-run-id")
        repo.fail.assert_not_called()

    async def test_fails_run_on_exception_and_reraises(self):
        repo = AsyncMock()
        repo.create_run.return_value = "new-run-id"
        session = AsyncMock()

        with patch("batch.run_tracking.AsyncSessionLocal", return_value=_ctx(session)), \
             patch("batch.run_tracking.BatchRunRepository", return_value=repo):
            with pytest.raises(RuntimeError, match="boom"):
                async with tracked_run(kind="trends", trigger="manual"):
                    raise RuntimeError("boom")

        repo.fail.assert_awaited_once_with("new-run-id", error="boom")
        repo.commit.assert_not_called()


class TestFinishExistingRun:
    async def test_commits_existing_run_on_success(self):
        repo = AsyncMock()
        session = AsyncMock()

        with patch("batch.run_tracking.AsyncSessionLocal", return_value=_ctx(session)), \
             patch("batch.run_tracking.BatchRunRepository", return_value=repo):
            async with finish_existing_run("existing-run-id"):
                pass

        repo.commit.assert_awaited_once_with("existing-run-id")
        repo.create_run.assert_not_called()
        repo.fail.assert_not_called()

    async def test_fails_existing_run_on_exception_and_reraises(self):
        repo = AsyncMock()
        session = AsyncMock()

        with patch("batch.run_tracking.AsyncSessionLocal", return_value=_ctx(session)), \
             patch("batch.run_tracking.BatchRunRepository", return_value=repo):
            with pytest.raises(RuntimeError, match="boom"):
                async with finish_existing_run("existing-run-id"):
                    raise RuntimeError("boom")

        repo.fail.assert_awaited_once_with("existing-run-id", error="boom")
        repo.commit.assert_not_called()


def _ctx(session):
    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_run_tracking.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batch.run_tracking'`.

- [ ] **Step 3: Implement `batch/run_tracking.py`**

```python
import uuid
from contextlib import asynccontextmanager

from repository.batch_runs import BatchRunRepository
from Storage.db import AsyncSessionLocal


@asynccontextmanager
async def tracked_run(kind: str, trigger: str):
    """Self-creates a run, yields its run_id, commits/fails it around the wrapped work.
    Used by each script's own main() for direct-CLI/self-tracked use, and by the wrapper
    when no run_id was pre-created (the scheduler's case)."""
    async with AsyncSessionLocal() as session:
        repo = BatchRunRepository(session)
        run_id = await repo.create_run(kind=kind, trigger=trigger)
        await session.commit()
    try:
        yield run_id
    except Exception as e:
        async with AsyncSessionLocal() as session:
            await BatchRunRepository(session).fail(run_id, error=str(e))
            await session.commit()
        raise
    else:
        async with AsyncSessionLocal() as session:
            await BatchRunRepository(session).commit(run_id)
            await session.commit()


@asynccontextmanager
async def finish_existing_run(run_id: uuid.UUID):
    """Commits/fails a run_id the CALLER already created (the admin endpoint's case, where
    the run_id must exist synchronously before the subprocess is even spawned)."""
    try:
        yield
    except Exception as e:
        async with AsyncSessionLocal() as session:
            await BatchRunRepository(session).fail(run_id, error=str(e))
            await session.commit()
        raise
    else:
        async with AsyncSessionLocal() as session:
            await BatchRunRepository(session).commit(run_id)
            await session.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_run_tracking.py -v`
Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add batch/run_tracking.py batch/tests/test_run_tracking.py
git commit -m "feat: add shared tracked_run/finish_existing_run context managers"
```

---

### Task 3: `move_flagged.py` / `unregister_deleted_images.py` — gain tracking

**Files:**
- Modify: `batch/move_flagged.py`
- Modify: `batch/unregister_deleted_images.py`
- Test: `tests/integration/test_move_flagged_tracking.py`, `tests/integration/test_unregister_deleted_images_tracking.py` (new)

**Interfaces:**
- Consumes: `tracked_run`, `finish_existing_run` (Task 2).
- Produces: `async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None` on both modules — Task 5's wrapper calls this exact signature on whichever module the registry resolves to.

Current `move_flagged.py` (full file, for reference):

```python
import argparse
import asyncio
import os
import shutil

from sqlalchemy import select

from config.settings import load_env, settings
from Storage.db import AsyncSessionLocal
from Storage.models import Image, ImageExtras


async def run(session, base_path):
    query = (
        select(Image.filename)
        .join(ImageExtras, ImageExtras.image_id == Image.id)
        .where(ImageExtras.flagged == True)
    )
    images = await session.execute(query)
    flagged_path = os.path.join(base_path, "excluded")
    os.makedirs(flagged_path, exist_ok=True)
    for (filename,) in images:
        path_from = os.path.join(base_path, filename)
        path_to = os.path.join(flagged_path, filename)
        print(f"Moving {filename} from {path_from} to {path_to}")
        shutil.move(path_from, path_to)


async def main():
    async with AsyncSessionLocal() as session:
        BASE_PATH = settings.BASE_PATH
        print(f"BASE_PATH={BASE_PATH}")
        base_path = os.path.abspath(BASE_PATH)
        await run(session, base_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
```

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_move_flagged_tracking.py`:

```python
"""
Integration tests for move_flagged.py's new main() tracking behavior. run()'s actual
file-moving logic is unchanged and untested here (out of scope for this change) --
run() itself is monkeypatched to a no-op/raising stub so these tests focus purely on
whether main() creates/finishes the right BatchRun row.
"""
import uuid
from unittest.mock import AsyncMock

import pytest

import batch.move_flagged as move_flagged
from repository.batch_runs import BatchRunRepository


@pytest.mark.asyncio(loop_scope="session")
async def test_main_self_tracks_as_manual_by_default(db_session, monkeypatch):
    monkeypatch.setattr(move_flagged, "run", AsyncMock())
    monkeypatch.setattr(move_flagged, "AsyncSessionLocal", lambda: _session_ctx(db_session))

    await move_flagged.main()

    repo = BatchRunRepository(db_session)
    active = await repo.get_active_run(kind="move_flagged")
    # main() commits on success, so nothing should still be "active" (started)
    assert active is None


@pytest.mark.asyncio(loop_scope="session")
async def test_main_creates_completed_run_with_manual_trigger(db_session, monkeypatch):
    monkeypatch.setattr(move_flagged, "run", AsyncMock())
    monkeypatch.setattr(move_flagged, "AsyncSessionLocal", lambda: _session_ctx(db_session))

    await move_flagged.main()

    repo = BatchRunRepository(db_session)
    most_recent = await repo.get_most_recent_run(kind="move_flagged")
    assert most_recent is not None
    assert most_recent.trigger == "manual"
    assert most_recent.status == "completed"


@pytest.mark.asyncio(loop_scope="session")
async def test_main_marks_run_failed_when_run_raises(db_session, monkeypatch):
    monkeypatch.setattr(move_flagged, "run", AsyncMock(side_effect=RuntimeError("disk full")))
    monkeypatch.setattr(move_flagged, "AsyncSessionLocal", lambda: _session_ctx(db_session))

    with pytest.raises(RuntimeError, match="disk full"):
        await move_flagged.main()

    repo = BatchRunRepository(db_session)
    most_recent = await repo.get_most_recent_run(kind="move_flagged")
    assert most_recent.status == "failed"
    assert most_recent.error == "disk full"


@pytest.mark.asyncio(loop_scope="session")
async def test_main_with_pre_created_run_id_finishes_that_row_not_a_new_one(db_session, monkeypatch):
    monkeypatch.setattr(move_flagged, "run", AsyncMock())
    monkeypatch.setattr(move_flagged, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    repo = BatchRunRepository(db_session)
    existing_run_id = await repo.create_run(kind="move_flagged", trigger="manual")
    await db_session.commit()

    await move_flagged.main(trigger="manual", run_id=existing_run_id)

    finished = await repo.get_run(existing_run_id)
    assert finished.status == "completed"
    # No second row was created for this kind
    all_recent = await repo.get_most_recent_run(kind="move_flagged")
    assert all_recent.run_id == existing_run_id


def _session_ctx(session):
    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()
```

Create `tests/integration/test_unregister_deleted_images_tracking.py` with the identical four
tests, `import batch.unregister_deleted_images as unregister_deleted_images` in place of
`move_flagged`, and `kind="unregister_deleted_images"` in place of `kind="move_flagged"`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_move_flagged_tracking.py tests/integration/test_unregister_deleted_images_tracking.py -v`
Expected: FAIL — `TypeError: main() got an unexpected keyword argument 'trigger'` (current `main()`
takes no arguments).

- [ ] **Step 3: Implement the `main()` change**

In `batch/move_flagged.py`, replace `main()` and the `if __name__ == "__main__":` block:

```python
import uuid

from batch.run_tracking import finish_existing_run, tracked_run


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(settings.BASE_PATH)
                await run(session, base_path)
    else:
        async with tracked_run(kind="move_flagged", trigger=trigger):
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(settings.BASE_PATH)
                await run(session, base_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
```

In `batch/unregister_deleted_images.py`, apply the identical shape, with `kind="unregister_deleted_images"`
and its own existing `base_path = os.path.abspath(os.getenv('BASE_PATH'))` line kept as-is (don't
change it to `settings.BASE_PATH` — that's a pre-existing inconsistency between the two scripts,
out of scope for this change).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_move_flagged_tracking.py tests/integration/test_unregister_deleted_images_tracking.py -v`
Expected: all 8 PASS.

- [ ] **Step 5: Run the full integration root**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/ -v`
Expected: all PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add batch/move_flagged.py batch/unregister_deleted_images.py tests/integration/test_move_flagged_tracking.py tests/integration/test_unregister_deleted_images_tracking.py
git commit -m "feat: add run tracking to move_flagged and unregister_deleted_images"
```

---

### Task 4: `trends_batch.py` — split work from tracking

**Files:**
- Modify: `batch/trends_batch.py`
- Test: `tests/integration/test_trends_batch_tracking.py` (new)

**Interfaces:**
- Consumes: `tracked_run`, `finish_existing_run` (Task 2).
- Produces: same `main(trigger="manual", run_id=None)` shape as Task 3, on `trends_batch.py`; a new
  `run(session)` extracted from the old inlined `main()` body.

Current `trends_batch.py` (relevant part, for reference — `process_source` above it, lines 1-31, is
unchanged and out of scope):

```python
async def main():
    processor = Processor()
    morph = make_morph()

    async with AsyncSessionLocal() as session:
        sources_repo = TrendSourceRepository(session)
        sources = await sources_repo.get_all()

        runs_repo = BatchRunRepository(session)
        run_id = await runs_repo.create_run(kind="trends", trigger="unknown")
        results_repo = TrendsRunResultRepository(session, run_id)

        try:
            for source in sources:
                connector = get_connector(source.name, source.connector_type, source.config)
                labels = resolve_labels(source, settings)
                model_name = resolve_model(source, settings)
                language = resolve_language(source, settings)
                trends = process_source(source, connector, processor, labels, model_name, language, morph)
                for topic, value in trends.items():
                    label, name = topic.split(":", 1)
                    await results_repo.add_result(source_id=source.id, label=label, name=name, value=value)
            await runs_repo.commit(run_id)
        except Exception:
            await runs_repo.fail(run_id)
            raise
        finally:
            await session.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
```

(Note: `trigger="unknown"` above reflects the *previous* plan's temporary call-site update — this
task removes that call site entirely.)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_trends_batch_tracking.py`:

```python
"""
Integration tests for trends_batch.py's main()/run() split and tracking behavior.
run()'s actual scraping logic is exercised separately by tests/batch/test_trends_batch.py
(process_source) -- these tests monkeypatch run() to a stub and focus purely on tracking.
"""
from unittest.mock import AsyncMock

import pytest

import batch.trends_batch as trends_batch
from repository.batch_runs import BatchRunRepository


@pytest.mark.asyncio(loop_scope="session")
async def test_main_creates_completed_run_with_manual_trigger_by_default(db_session, monkeypatch):
    monkeypatch.setattr(trends_batch, "run", AsyncMock())
    monkeypatch.setattr(trends_batch, "AsyncSessionLocal", lambda: _session_ctx(db_session))

    await trends_batch.main()

    repo = BatchRunRepository(db_session)
    most_recent = await repo.get_most_recent_run(kind="trends")
    assert most_recent.trigger == "manual"
    assert most_recent.status == "completed"


@pytest.mark.asyncio(loop_scope="session")
async def test_main_marks_run_failed_when_run_raises(db_session, monkeypatch):
    monkeypatch.setattr(trends_batch, "run", AsyncMock(side_effect=RuntimeError("connector down")))
    monkeypatch.setattr(trends_batch, "AsyncSessionLocal", lambda: _session_ctx(db_session))

    with pytest.raises(RuntimeError, match="connector down"):
        await trends_batch.main()

    repo = BatchRunRepository(db_session)
    most_recent = await repo.get_most_recent_run(kind="trends")
    assert most_recent.status == "failed"


@pytest.mark.asyncio(loop_scope="session")
async def test_main_with_pre_created_run_id_finishes_that_row(db_session, monkeypatch):
    monkeypatch.setattr(trends_batch, "run", AsyncMock())
    monkeypatch.setattr(trends_batch, "AsyncSessionLocal", lambda: _session_ctx(db_session))
    repo = BatchRunRepository(db_session)
    existing_run_id = await repo.create_run(kind="trends", trigger="scheduled")
    await db_session.commit()

    await trends_batch.main(trigger="scheduled", run_id=existing_run_id)

    finished = await repo.get_run(existing_run_id)
    assert finished.status == "completed"


def _session_ctx(session):
    class _Ctx:
        async def __aenter__(self_inner):
            return session

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_trends_batch_tracking.py -v`
Expected: FAIL — `AttributeError: module 'batch.trends_batch' has no attribute 'run'` (no such
function exists yet — the work is still inlined in `main()`).

- [ ] **Step 3: Implement the `run()`/`main()` split**

In `batch/trends_batch.py`, replace `main()` and the `__main__` block with:

```python
import uuid

from batch.run_tracking import finish_existing_run, tracked_run


async def run(session) -> None:
    processor = Processor()
    morph = make_morph()

    sources_repo = TrendSourceRepository(session)
    sources = await sources_repo.get_all()

    runs_repo = BatchRunRepository(session)
    run_id = await runs_repo.get_active_run(kind="trends")  # populated by tracked_run/finish_existing_run's caller
    results_repo = TrendsRunResultRepository(session, run_id.run_id)

    for source in sources:
        connector = get_connector(source.name, source.connector_type, source.config)
        labels = resolve_labels(source, settings)
        model_name = resolve_model(source, settings)
        language = resolve_language(source, settings)
        trends = process_source(source, connector, processor, labels, model_name, language, morph)
        for topic, value in trends.items():
            label, name = topic.split(":", 1)
            await results_repo.add_result(source_id=source.id, label=label, name=name, value=value)


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                await run(session)
    else:
        async with tracked_run(kind="trends", trigger=trigger):
            async with AsyncSessionLocal() as session:
                await run(session)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
```

**Stop and reconsider `run()`'s access to `run_id`.** The sketch above (`get_active_run` inside
`run()`) is awkward — `results_repo` needs the run's `run_id` for `TrendsRunResultRepository`, but
`run(session)` as designed here has no direct parameter for it, and re-querying `get_active_run`
from inside `run()` is indirect and assumes there's exactly one active run (usually true, but
`run()` shouldn't need to rediscover state its caller already has). **Fix this before implementing:**
give `run()` an explicit `run_id: uuid.UUID` parameter instead —
`async def run(session, run_id: uuid.UUID) -> None`, called as `await run(session, run_id)` from
both branches of `main()` (in the `if run_id is not None` branch, that's the parameter directly; in
the `else` branch, it's the `run_id` yielded by `tracked_run(...)`). Update the test file's mocked
`run` calls' expectations accordingly if any test asserts on `run`'s call signature (none of the
tests above do — they only check `run.assert_awaited_once()`-style calls or rely on
`AsyncMock()`'s permissiveness, so no test changes needed for this fix, only the implementation).

- [ ] **Step 4: Run the test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_trends_batch_tracking.py -v`
Expected: all 3 PASS.

- [ ] **Step 5: Confirm `process_source` and its own tests are untouched**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/batch/test_trends_batch.py -v`
Expected: unaffected, still PASS — this refactor moves `main()`'s body, it does not touch
`process_source` (lines 1-31 of the original file).

- [ ] **Step 6: Run the full integration root**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/ -v`
Expected: all PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add batch/trends_batch.py tests/integration/test_trends_batch_tracking.py
git commit -m "refactor: split trends_batch.py's work from its tracking, matching the other two scripts"
```

---

### Task 5: `batch/run_wrapper.py`

**Files:**
- Create: `batch/run_wrapper.py`
- Test: `batch/tests/test_run_wrapper.py`

**Interfaces:**
- Consumes: `BatchRegistry` (Task 1); `module.main(trigger, run_id=None)` on all three scripts
  (Tasks 3-4).
- Produces: `async def main() -> None` (the wrapper's own CLI entry point) — Task 6's scheduler
  update spawns this as `python -m batch.run_wrapper --script ... --env ... --trigger scheduled`;
  the (not-yet-written) admin controller spawns it the same way with `--run-id` also set.

- [ ] **Step 1: Write the failing tests**

Create `batch/tests/test_run_wrapper.py`:

```python
"""
Unit tests for batch/run_wrapper.py's argument resolution and dispatch. Mocks
importlib.import_module and BatchRegistry -- no real batch script or DB involved.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import batch.run_wrapper as run_wrapper


class TestRunWrapperMain:
    async def test_resolves_registry_entry_and_calls_module_main_no_run_id(self):
        fake_module = MagicMock()
        fake_module.main = AsyncMock()
        fake_registry = MagicMock()
        fake_registry.all_names.return_value = ["trends_batch"]
        fake_registry.get.return_value = {"module": "batch.trends_batch", "kind": "trends"}

        argv = ["--script", "trends_batch", "--env", "metal", "--trigger", "scheduled"]
        with patch("batch.run_wrapper.BatchRegistry", return_value=fake_registry), \
             patch("batch.run_wrapper.load_env") as mock_load_env, \
             patch("batch.run_wrapper.importlib.import_module", return_value=fake_module) as mock_import, \
             patch("sys.argv", ["run_wrapper.py"] + argv):
            await run_wrapper.main()

        mock_load_env.assert_called_once_with("metal")
        mock_import.assert_called_once_with("batch.trends_batch")
        fake_module.main.assert_awaited_once_with(trigger="scheduled", run_id=None)

    async def test_passes_through_run_id_when_given(self):
        fake_module = MagicMock()
        fake_module.main = AsyncMock()
        fake_registry = MagicMock()
        fake_registry.all_names.return_value = ["move_flagged"]
        fake_registry.get.return_value = {"module": "batch.move_flagged", "kind": "move_flagged"}
        run_id = uuid.uuid4()

        argv = ["--script", "move_flagged", "--env", "general", "--trigger", "manual",
                "--run-id", str(run_id)]
        with patch("batch.run_wrapper.BatchRegistry", return_value=fake_registry), \
             patch("batch.run_wrapper.load_env"), \
             patch("batch.run_wrapper.importlib.import_module", return_value=fake_module), \
             patch("sys.argv", ["run_wrapper.py"] + argv):
            await run_wrapper.main()

        fake_module.main.assert_awaited_once_with(trigger="manual", run_id=run_id)

    async def test_unknown_script_name_is_rejected_by_argparse(self):
        fake_registry = MagicMock()
        fake_registry.all_names.return_value = ["trends_batch"]

        argv = ["--script", "not_a_real_script", "--env", "metal", "--trigger", "scheduled"]
        with patch("batch.run_wrapper.BatchRegistry", return_value=fake_registry), \
             patch("sys.argv", ["run_wrapper.py"] + argv):
            with pytest.raises(SystemExit):
                await run_wrapper.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_run_wrapper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batch.run_wrapper'`.

- [ ] **Step 3: Implement `batch/run_wrapper.py`**

```python
import argparse
import asyncio
import importlib
import uuid

from batch.registry import BatchRegistry
from config.settings import load_env


async def main() -> None:
    registry = BatchRegistry()
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", choices=registry.all_names(), required=True)
    parser.add_argument("--env", choices=["metal", "general", "it"], required=True)
    parser.add_argument("--trigger", choices=["manual", "scheduled"], required=True)
    parser.add_argument("--run-id", default=None,
                         help="Pre-created run id (admin controller); omitted for the scheduler, "
                              "which lets this wrapper create its own run.")
    args = parser.parse_args()
    load_env(args.env)

    entry = registry.get(args.script)  # re-read here too, not reused from the choices lookup above
    module = importlib.import_module(entry["module"])
    run_id = uuid.UUID(args.run_id) if args.run_id else None
    await module.main(trigger=args.trigger, run_id=run_id)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_run_wrapper.py -v`
Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add batch/run_wrapper.py batch/tests/test_run_wrapper.py
git commit -m "feat: add batch/run_wrapper.py, the registry-backed subprocess entry point"
```

---

### Task 6: `scheduler.py` — invoke the wrapper instead of the raw module

**Files:**
- Modify: `Backend/app/scheduler.py`
- Modify: `environments/settings.yaml` (and any of `settings.metal.yaml`/`settings.general.yaml`/`settings.it.yaml` that override `scheduler.jobs`)
- Modify: `Backend/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `batch/run_wrapper.py` (Task 5) as the thing actually spawned; job config's `script` key
  (this task renames `module` → `script` in tracked YAML).
- No change to `_initial_delay`, `_should_run`, `_safe_tick`, `_wait_for_process`,
  `start_scheduler`, `stop_scheduler` signatures — only `_load_job_configs`'s returned dict shape
  and `_spawn`'s invocation change.

Current `_load_job_configs` result shape and `_spawn` (`Backend/app/scheduler.py`, shown in full
above in this plan's setup — re-read the actual file before editing, since exact line numbers may
have drifted from what's quoted here) use `job["module"]` as a raw Python module path
(`"batch.trends_batch"`) invoked directly. This task changes that to a registry `script` name
(`"trends_batch"`) invoked via the wrapper.

- [ ] **Step 1: Write the failing tests**

In `Backend/tests/test_scheduler.py`:

1. Change the `_job()` helper's base dict: replace `"module": "batch.trends_batch",` with
   `"script": "trends_batch",`.
2. In `TestLoadJobConfigs.test_returns_expected_fields_only`, change the expected dict's
   `"module": "batch.trends_batch",` to `"script": "trends_batch",`.
3. In `TestSpawn.test_invokes_subprocess_with_expected_args`, change the assertion:
   ```python
   assert args[0] == [sys.executable, "-m", "batch.trends_batch", "--env", "general"]
   ```
   to:
   ```python
   assert args[0] == [
       sys.executable, "-m", "batch.run_wrapper",
       "--script", "trends_batch", "--env", "general", "--trigger", "scheduled",
   ]
   ```
4. `TestSpawnSurvivesRealShutdown` and `test_real_subprocess_survives_asyncio_runner_close` both
   construct `job = _job(module="sleepy_job", name="sleepy")` and rely on `_spawn` invoking
   `job["module"]` directly as a real, literal importable module (`sleepy_job.py`, written to
   `tmp_path` by the test itself) — **not** something resolvable through `batch/registry.py`. Since
   `_spawn` now always goes through `batch.run_wrapper`, these two tests can no longer point `_spawn`
   directly at an arbitrary throwaway script. Change both to instead monkeypatch
   `scheduler_module._spawn`'s subprocess command construction indirectly by keeping `job["script"]`
   pointed at a *fake but registry-resolvable* name, and monkeypatch `BatchRegistry` (imported in
   `batch.run_wrapper`, not `scheduler.py`) to resolve it to the same throwaway `sleepy_job` module —
   i.e., these two tests now exercise `_spawn` → real `subprocess.Popen` → real
   `python -m batch.run_wrapper --script sleepy --env general --trigger scheduled`, with
   `batch.registry.BatchRegistry` monkeypatched (via `monkeypatch.setattr("batch.registry.BatchRegistry", ...)`
   or an actual `environments/batch_registry.yaml`-shaped tmp fixture directory passed via
   `BatchRegistry(base_dir=...)`, whichever is simpler to wire from the test) so that `--script sleepy`
   resolves to `{"module": "sleepy_job", "kind": "sleepy"}`. This is a real integration point (a
   subprocess-of-a-subprocess: scheduler spawns `run_wrapper.py`, which itself imports and calls the
   target). Confirm the child process genuinely completes (`done.txt` marker) and, for the
   `asyncio.Runner`-based test, that shutdown still doesn't block — same assertions as today, just
   through one more layer.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd Backend && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/test_scheduler.py -v`
Expected: FAIL — `KeyError: 'module'` (job dicts now carry `script`, not `module`) and/or assertion
mismatches on the expected `Popen` argv, until Steps 3 below land.

- [ ] **Step 3: Implement the `scheduler.py` change**

In `Backend/app/scheduler.py`'s `_load_job_configs`, change:
```python
                "module": job["module"],
```
to:
```python
                "script": job["script"],
```

In `_spawn`, change the `subprocess.Popen` argv construction from:
```python
        proc = subprocess.Popen(
            [sys.executable, "-m", job["module"], "--env", app_env],
            stdout=log_file, stderr=log_file,
        )
```
to:
```python
        proc = subprocess.Popen(
            [sys.executable, "-m", "batch.run_wrapper",
             "--script", job["script"], "--env", app_env, "--trigger", "scheduled"],
            stdout=log_file, stderr=log_file,
        )
```

In `environments/settings.yaml`, under `scheduler.jobs`, change:
```yaml
      module: batch.trends_batch
```
to:
```yaml
      script: trends_batch
```
Check `environments/settings.metal.yaml`, `settings.general.yaml`, `settings.it.yaml` for any
`scheduler.jobs` overrides with the same `module` key and update identically if present (as of this
plan's writing, none override it, but verify against the actual current file contents rather than
assuming).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd Backend && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/test_scheduler.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full Backend suite**

Run: `cd Backend && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest -v`
Expected: all PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add Backend/app/scheduler.py Backend/tests/test_scheduler.py environments/settings.yaml
git commit -m "feat: invoke batch/run_wrapper.py from the scheduler instead of the raw module"
```

## Self-Review Notes

- **Spec coverage:** externalized registry (Task 1), shared tracking helper (Task 2),
  `move_flagged`/`unregister_deleted_images` tracking (Task 3), `trends_batch` split (Task 4),
  wrapper (Task 5), scheduler update (Task 6) — matches the spec's rollout list item-for-item.
- **Gap found and fixed during this plan's own writing (Task 4):** the spec's `run(session)` sketch
  didn't give `run()` a way to know its own `run_id` for `TrendsRunResultRepository`, which needs
  it. Task 4's Step 3 calls this out explicitly and fixes it before the implementer writes code,
  rather than leaving an implementer to discover the gap mid-implementation.
- **Gap found during this plan's own writing (Task 6):** the scheduler's own two hardest-won tests
  (`TestSpawnSurvivesRealShutdown`, `test_real_subprocess_survives_asyncio_runner_close`) directly
  construct a throwaway script and point `_spawn` at it via `job["module"]` — once `_spawn` always
  goes through the registry-backed wrapper, those tests need a registry-resolvable stand-in, not a
  bare module path. Task 6 Step 1 spells out the fix rather than silently letting these two
  regression tests degrade or get deleted.
- **Type consistency:** `main(trigger, run_id=None)` signature is identical across
  `move_flagged.py`, `unregister_deleted_images.py`, and `trends_batch.py` (Tasks 3-4), and matches
  exactly what `batch/run_wrapper.py` (Task 5) calls. `BatchRegistry`'s three method names/signatures
  (Task 1) match every later task's usage of them.
