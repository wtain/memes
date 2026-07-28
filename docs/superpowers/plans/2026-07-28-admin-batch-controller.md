# Admin Batch Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /api/admin/batches/{batch_name}/run`, `GET /api/admin/batches/runs/{run_id}`, and `GET /api/admin/batches/runs` so an operator can trigger `trends_batch`/`move_flagged`/`unregister_deleted_images` on demand and see run status/history, on top of the shared subprocess mechanism extracted from the scheduler.

**Architecture:** `Backend/app/batch_subprocess.py` (extracted from `scheduler.py`, behavior-preserving) owns the Popen+daemon-thread spawn/wait mechanism, per-invocation log-path naming, and a fire-and-forget helper. `Backend/app/services/admin_batch_service.py` + `Backend/app/api/admin.py` build the three endpoints on top of it, `batch/registry.py`, and `batch/run_wrapper.py`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async ORM, pytest + `TestClient` (mirroring `Backend/tests/test_ingestion_endpoints.py`'s style).

**Spec:** `docs/superpowers/specs/2026-07-28-admin-batch-controller-design.md`

**Depends on:** `docs/superpowers/plans/2026-07-28-batch-run-trigger-tracking.md` and
`docs/superpowers/plans/2026-07-28-batch-run-wrapper.md` (both must be implemented first — this
plan's endpoints call `create_run(..., trigger=...)`, catch `BatchAlreadyRunningError`, and spawn
`batch.run_wrapper`).

## Global Constraints

- No request body on `POST .../run` beyond the URL path — `batch_name` is the only client input,
  validated against `batch.registry.BatchRegistry` before anything else happens.
- The admin endpoint explicitly commits its session immediately after `create_run()` succeeds,
  before spawning anything — not left to `get_async_db`'s usual after-the-handler commit — since the
  spawned subprocess is a separate OS process that must see the committed row the instant it starts.
- `POST .../run` never awaits the spawned subprocess's completion — it returns as soon as the
  subprocess is launched.
- `GET .../runs/{run_id}` and `GET .../runs` are scoped to `kind IN ('trends', 'move_flagged',
  'unregister_deleted_images')` only — an `ingestion`-kind `run_id` (or any other kind) is a `404`,
  not a leak into this generic view.
- Log files nest by environment and are named from data already known before spawning
  (`logs/{env}/{script}_{timestamp}.log`) — no PID, no `run_id` in the filename, no
  download-by-run_id endpoint (deliberately dropped — see the spec's design history).
- `Backend/app/scheduler.py`'s own behavior (restart-safe timing, orphan recovery, per-tick error
  isolation, "never kill in-flight subprocesses on shutdown") must be unchanged after the extraction
  in Task 1 — only *where* the spawn/wait code lives changes.

---

### Task 1: Extract the subprocess mechanism into `Backend/app/batch_subprocess.py`

**Files:**
- Create: `Backend/app/batch_subprocess.py`
- Create: `Backend/tests/test_batch_subprocess.py`
- Modify: `Backend/app/scheduler.py`
- Modify: `Backend/tests/test_scheduler.py`

**Interfaces:**
- Produces: `build_log_path(env: str, script: str) -> Path`; `async def spawn_and_track(args: list[str], log_path: Path) -> int` (Popen + daemon-thread wait + exit-code logging, returns the exit code); `async def fire_and_forget(coro) -> None` (creates a tracked background task, logs — doesn't swallow — any exception it raises). Task 2 depends on all three exact names/signatures.
- Consumes (unchanged): nothing new — this is a behavior-preserving move of code that already exists in `Backend/app/scheduler.py` (`_wait_for_process`, the Popen/log-path parts of `_spawn`).

This task moves working, hard-won code (see `Backend/app/scheduler.py`'s current `_wait_for_process`
and `_spawn` docstrings for the full rationale of *why* it's built this way — `subprocess.Popen`
not `asyncio.create_subprocess_exec`, a manually-created daemon thread not `asyncio.to_thread`).
Read those two docstrings in the actual current file before starting — they explain two real,
previously-shipped-then-fixed bugs this code avoids reintroducing.

- [ ] **Step 1: Write the failing tests**

Create `Backend/tests/test_batch_subprocess.py`. This migrates the subprocess-mechanism tests
currently in `Backend/tests/test_scheduler.py`'s `TestSpawn`, `TestWaitForProcess`, and both
real-subprocess-survival tests, adapted to the new module's signatures:

```python
"""
Unit tests for Backend/app/batch_subprocess.py -- the Popen+daemon-thread spawn/wait
mechanism extracted from scheduler.py, plus log-path naming and the fire-and-forget
helper the admin endpoint needs. See scheduler.py's git history (this module's direct
ancestor) for why Popen/daemon-thread specifically -- both a killed-child bug and a
blocked-shutdown bug were found and fixed there across several review rounds.
"""
import asyncio
import gc
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import Backend.app.batch_subprocess as batch_subprocess
from Backend.app.batch_subprocess import build_log_path, fire_and_forget, spawn_and_track


class TestBuildLogPath:
    def test_nests_by_environment_and_includes_script_and_timestamp(self):
        path = build_log_path("metal", "trends_batch")

        assert path.parent == Path("logs") / "metal"
        assert path.name.startswith("trends_batch_")
        assert path.suffix == ".log"

    def test_different_calls_produce_different_paths(self):
        # Microsecond-resolution timestamp -- collision-free for this call frequency.
        path_a = build_log_path("metal", "trends_batch")
        path_b = build_log_path("metal", "trends_batch")

        assert path_a != path_b


class TestSpawnAndTrack:
    async def test_invokes_popen_with_given_args_and_returns_exit_code(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        fake_proc = MagicMock()
        fake_proc.wait = MagicMock(return_value=0)
        popen_mock = MagicMock(return_value=fake_proc)
        monkeypatch.setattr(subprocess, "Popen", popen_mock)
        log_path = tmp_path / "logs" / "metal" / "trends_batch_x.log"

        returncode = await spawn_and_track([sys.executable, "-m", "batch.run_wrapper"], log_path)

        assert returncode == 0
        args, kwargs = popen_mock.call_args
        assert args[0] == [sys.executable, "-m", "batch.run_wrapper"]
        assert log_path.exists()
        assert kwargs["stdout"].name == str(log_path)
        assert kwargs["stdout"] is kwargs["stderr"]

    async def test_logs_launch_and_nonzero_exit_code(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        fake_proc = MagicMock()
        fake_proc.wait = MagicMock(return_value=1)
        monkeypatch.setattr(subprocess, "Popen", MagicMock(return_value=fake_proc))
        mock_logger = MagicMock()
        monkeypatch.setattr(batch_subprocess, "logger", mock_logger)
        log_path = tmp_path / "logs" / "metal" / "flaky_x.log"

        await spawn_and_track(["flaky"], log_path)

        mock_logger.warning.assert_called_once()

    async def test_logs_zero_exit_code_at_info_level(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        fake_proc = MagicMock()
        fake_proc.wait = MagicMock(return_value=0)
        monkeypatch.setattr(subprocess, "Popen", MagicMock(return_value=fake_proc))
        mock_logger = MagicMock()
        monkeypatch.setattr(batch_subprocess, "logger", mock_logger)
        log_path = tmp_path / "logs" / "metal" / "ok_x.log"

        await spawn_and_track(["ok"], log_path)

        mock_logger.warning.assert_not_called()


class TestWaitForProcess:
    """Moved verbatim in spirit from scheduler.py's TestWaitForProcess -- see
    _wait_for_process's docstring (this module) for why a manually-created daemon
    thread, not asyncio.to_thread, is load-bearing here."""

    async def test_returns_the_process_returncode(self):
        class _FakeProc:
            def wait(self):
                time.sleep(0.05)
                return 7

        returncode = await batch_subprocess._wait_for_process(_FakeProc())

        assert returncode == 7

    async def test_uses_a_manually_created_daemon_thread_not_a_threadpool(self, monkeypatch):
        real_thread_cls = threading.Thread
        captured_threads = []

        class _CapturingThread(real_thread_cls):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured_threads.append(self)

        monkeypatch.setattr(threading, "Thread", _CapturingThread)

        class _FakeProc:
            def wait(self):
                return 0

        await batch_subprocess._wait_for_process(_FakeProc())

        assert len(captured_threads) == 1
        assert captured_threads[0].daemon is True


class TestFireAndForget:
    async def test_task_runs_to_completion(self):
        done = asyncio.Event()

        async def _work():
            done.set()

        await fire_and_forget(_work())
        await asyncio.sleep(0)  # let the created task actually run

        assert done.is_set()

    async def test_holds_strong_reference_until_done_not_gced_early(self, monkeypatch):
        # Regression guard: a bare asyncio.create_task with no strong reference kept
        # anywhere can be garbage-collected before it ever runs. fire_and_forget must
        # keep one until the task's own done-callback removes it.
        started = asyncio.Event()
        finished = asyncio.Event()

        async def _work():
            started.set()
            await asyncio.sleep(0.1)
            finished.set()

        await fire_and_forget(_work())
        gc.collect()  # a bug here would let this collect the task before it starts
        await asyncio.sleep(0.2)

        assert started.is_set()
        assert finished.is_set()

    async def test_logs_exception_from_task_instead_of_swallowing_it(self, monkeypatch):
        mock_logger = MagicMock()
        monkeypatch.setattr(batch_subprocess, "logger", mock_logger)

        async def _fails():
            raise RuntimeError("boom")

        await fire_and_forget(_fails())
        await asyncio.sleep(0.05)  # let the task run and its done-callback fire

        mock_logger.exception.assert_called_once()


class TestSpawnAndTrackSurvivesRealShutdown:
    async def test_real_subprocess_survives_task_cancellation(self, monkeypatch, tmp_path):
        """Moved from scheduler.py's TestSpawnSurvivesRealShutdown, adapted to call
        spawn_and_track directly instead of going through _run_tick/scheduler job
        config."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "sleepy_job.py").write_text(
            "import time\ntime.sleep(3)\nopen('done.txt', 'w').close()\n"
        )
        log_path = tmp_path / "logs" / "metal" / "sleepy_x.log"
        done_marker = tmp_path / "done.txt"

        task = asyncio.create_task(
            spawn_and_track([sys.executable, "sleepy_job.py"], log_path)
        )
        await asyncio.sleep(0.5)
        assert not task.done(), "spawn finished before we could cancel -- flaky timing"
        assert not done_marker.exists()

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        gc.collect()
        for _ in range(100):
            if done_marker.exists():
                break
            await asyncio.sleep(0.1)
        assert done_marker.exists(), "child was killed as a side effect of cancellation"


def test_real_subprocess_survives_asyncio_runner_close(tmp_path):
    """Moved from scheduler.py's test_real_subprocess_survives_asyncio_runner_close,
    adapted to call spawn_and_track directly. See that test's original docstring
    (scheduler.py's git history) for the full two-round history of why this exact
    scenario (real asyncio.Runner teardown, not just a single task.cancel()) is what
    actually matters -- uvicorn's real shutdown path cancels EVERY remaining task via
    Runner.close(), not just the ones explicitly cancelled.

    Deliberately a plain (non-async) test function -- manages its own asyncio.Runner
    loop, so pytest must not wrap it in one.
    """
    old_cwd = Path.cwd()
    child_sleep_seconds = 5
    (tmp_path / "sleepy_job.py").write_text(
        "import time\n"
        f"time.sleep({child_sleep_seconds})\n"
        "open('done.txt', 'w').close()\n"
    )
    log_path = Path("logs") / "metal" / "sleepy_x.log"
    done_marker = tmp_path / "done.txt"

    os.chdir(tmp_path)
    try:
        async def _drive():
            task = asyncio.create_task(
                spawn_and_track([sys.executable, "sleepy_job.py"], log_path)
            )
            await asyncio.sleep(0.5)
            assert not task.done(), "spawn finished before we could tear down the loop"
            assert not done_marker.exists()
            return task

        close_started_at = time.monotonic()
        with asyncio.Runner() as runner:
            runner.run(_drive())
        close_elapsed = time.monotonic() - close_started_at
    finally:
        os.chdir(old_cwd)

    assert close_elapsed < 2.0, (
        f"asyncio.Runner's `with` block took {close_elapsed:.2f}s to return -- "
        "shutdown is blocking on the child subprocess instead of returning promptly"
    )
    for _ in range(100):
        if done_marker.exists():
            break
        time.sleep(0.1)
    assert done_marker.exists(), "child was killed by asyncio.Runner.close()'s task cancellation"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd Backend && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/test_batch_subprocess.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'Backend.app.batch_subprocess'`.

- [ ] **Step 3: Implement `Backend/app/batch_subprocess.py`**

Read `Backend/app/scheduler.py`'s current `_wait_for_process` function and its docstring in full
before writing this — move it into the new module **verbatim** (same docstring, same logic; only
its location changes):

```python
import asyncio
import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_in_flight: set[asyncio.Task] = set()


def build_log_path(env: str, script: str) -> Path:
    """One file per invocation, nested by environment. No PID, no run_id in the name --
    both would create a chicken-and-egg problem (Popen's stdout= file must be opened
    before the child exists, so the name can't depend on anything only the child would
    know), and there is deliberately no link back to a specific batch_runs.run_id -- an
    operator correlates by script name and timestamp against a run's created_at instead.
    """
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S_%f")
    return Path("logs") / env / f"{script}_{timestamp}.log"


async def _wait_for_process(proc: subprocess.Popen) -> int:
    # (moved verbatim from Backend/app/scheduler.py -- same docstring, same logic)
    loop = asyncio.get_running_loop()
    future: asyncio.Future[int] = loop.create_future()

    def _deliver_result(fut: "asyncio.Future[int]", result: int) -> None:
        if not fut.done():
            fut.set_result(result)

    def _wait_in_thread() -> None:
        returncode = proc.wait()
        try:
            loop.call_soon_threadsafe(_deliver_result, future, returncode)
        except RuntimeError:
            pass

    threading.Thread(target=_wait_in_thread, daemon=True).start()
    return await future


async def spawn_and_track(args: list[str], log_path: Path) -> int:
    """Spawn args via Popen (see this module's git-history ancestor, scheduler.py's
    _spawn docstring, for why Popen specifically -- its __del__ never kills the child,
    unlike asyncio's own subprocess transport), redirect stdout/stderr to log_path,
    await completion via _wait_for_process's daemon thread (survives cancellation and
    doesn't block shutdown), log the exit code, and return it. Caller decides whether
    to await this inline (the scheduler) or via fire_and_forget (the admin endpoint).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("batch_subprocess: launching %s", args)
    with open(log_path, "ab") as log_file:
        proc = subprocess.Popen(args, stdout=log_file, stderr=log_file)
        returncode = await _wait_for_process(proc)

    if returncode == 0:
        logger.info("batch_subprocess: %s exited with code %s", args[0], returncode)
    else:
        logger.warning("batch_subprocess: %s exited with code %s", args[0], returncode)
    return returncode


def _on_fire_and_forget_done(task: asyncio.Task) -> None:
    _in_flight.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.exception("batch_subprocess: fire-and-forget task failed", exc_info=exc)


async def fire_and_forget(coro) -> None:
    """Creates an asyncio.Task for coro, holding a strong reference in a module-level
    set until it's done (a bare asyncio.create_task with no reference kept anywhere
    can be garbage-collected before it ever runs), and logs -- rather than silently
    swallows -- any exception the task raises. For callers that must not block on the
    task's completion (the admin endpoint's HTTP response)."""
    task = asyncio.create_task(coro)
    _in_flight.add(task)
    task.add_done_callback(_on_fire_and_forget_done)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd Backend && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/test_batch_subprocess.py -v`
Expected: all PASS.

- [ ] **Step 5: Simplify `scheduler.py`'s `_spawn` and remove the now-duplicated tests**

In `Backend/app/scheduler.py`:
- Remove `_wait_for_process` entirely (moved to `batch_subprocess.py`).
- Replace `_spawn`:

```python
from Backend.app.batch_subprocess import build_log_path, spawn_and_track


async def _spawn(job: dict, app_env: str) -> None:
    log_path = build_log_path(app_env, job["script"])
    args = [sys.executable, "-m", "batch.run_wrapper",
            "--script", job["script"], "--env", app_env, "--trigger", "scheduled"]
    await spawn_and_track(args, log_path)
```

(Note: this uses `job["script"]`, matching the wrapper-plan's rename of `module`→`script` — that
plan must already be implemented before this one, per this plan's "Depends on" line above. If for
some reason it isn't, this step is not yet applicable — stop and check.)

Remove now-imports that are no longer used in `scheduler.py` (`subprocess`, `threading` — check
whether anything else in the file still needs them before removing).

In `Backend/tests/test_scheduler.py`:
- Delete the `TestSpawn` class, `TestWaitForProcess` class, `TestSpawnSurvivesRealShutdown` class,
  and the standalone `test_real_subprocess_survives_asyncio_runner_close` function — all now live in
  `Backend/tests/test_batch_subprocess.py` instead.
- Remove the now-unused imports these left behind if nothing else in the file uses them: `gc`,
  `subprocess`, `threading`, `time` (keep `os`, `sys`, `Path`, `pytest` if other tests in the file
  still use them — check before removing each one individually).
- Add a new, small `TestSpawn` class that verifies `_spawn` delegates correctly, mocking
  `batch_subprocess.spawn_and_track` rather than re-testing the whole mechanism:

```python
class TestSpawn:
    async def test_builds_wrapper_args_and_delegates_to_spawn_and_track(self, monkeypatch):
        spawn_and_track_mock = AsyncMock(return_value=0)
        monkeypatch.setattr(scheduler_module, "spawn_and_track", spawn_and_track_mock)
        monkeypatch.setattr(scheduler_module, "build_log_path", lambda env, script: Path("fake.log"))

        await scheduler_module._spawn(_job(), "general")

        spawn_and_track_mock.assert_awaited_once_with(
            [sys.executable, "-m", "batch.run_wrapper",
             "--script", "trends_batch", "--env", "general", "--trigger", "scheduled"],
            Path("fake.log"),
        )
```

(`sys` and `Path` must remain imported in the test file for this — re-add if the cleanup above
removed them and nothing else needed them; this new test needs both.)

- [ ] **Step 6: Run the full Backend suite**

Run: `cd Backend && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest -v`
Expected: all PASS — the moved tests now live in `test_batch_subprocess.py` (Step 4), and
`test_scheduler.py`'s remaining tests (config parsing, timing/guard logic, `_run_tick`/`_safe_tick`,
`_safe_initial_delay`, `start_scheduler`/`stop_scheduler`) are all untouched by this extraction and
should still pass exactly as before.

- [ ] **Step 7: Commit**

```bash
git add Backend/app/batch_subprocess.py Backend/tests/test_batch_subprocess.py Backend/app/scheduler.py Backend/tests/test_scheduler.py
git commit -m "refactor: extract subprocess spawn/wait mechanism into Backend/app/batch_subprocess.py"
```

---

### Task 2: Admin batch service + router

**Files:**
- Create: `Backend/app/services/admin_batch_service.py`
- Create: `Backend/app/api/admin.py`
- Test: `Backend/tests/test_admin_batch_service.py`, `Backend/tests/test_admin_batch_endpoints.py`

**Interfaces:**
- Consumes: `repository.batch_runs.BatchRunRepository`/`.BatchAlreadyRunningError` (trigger-tracking
  plan); `batch.registry.BatchRegistry` (wrapper plan); `Backend.app.batch_subprocess.build_log_path`/
  `.spawn_and_track`/`.fire_and_forget` (Task 1).
- Produces: `AdminBatchService` with `trigger_run(batch_name) -> dict`, `get_run(run_id) -> dict`,
  `list_runs(limit, offset) -> dict` — all raising `fastapi.HTTPException` directly on error paths,
  matching `IngestionService`'s existing style; the router in `Backend/app/api/admin.py`, registered
  under `router = APIRouter(prefix="/admin/batches", tags=["admin"])` (note: **not**
  `/api/admin/batches` — `Backend/app/main.py` already adds the `/api` prefix via
  `app.include_router(admin_router, prefix="/api")`, matching every other router in that file).

- [ ] **Step 1: Write the failing service tests**

Create `Backend/tests/test_admin_batch_service.py`:

```python
"""
Unit tests for AdminBatchService. Repository/registry/subprocess interactions are all
mocked -- matching IngestionService's own test style (no real DB, no real subprocess).
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from Backend.app.services.admin_batch_service import AdminBatchService
from repository.batch_runs import BatchAlreadyRunningError


def _fake_run(*, run_id, kind, trigger="manual", status="started", error=None,
              created_at=None, completed_at=None):
    return MagicMock(
        run_id=run_id, kind=kind, trigger=trigger, status=status, error=error,
        created_at=created_at or datetime.now(timezone.utc), completed_at=completed_at,
    )


@pytest.fixture
def mock_repo():
    return AsyncMock()


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    registry.get.return_value = {"module": "batch.trends_batch", "kind": "trends"}
    registry.name_for_kind.return_value = "trends_batch"
    return registry


@pytest.fixture
def service(mock_repo, mock_registry):
    return AdminBatchService(mock_repo, mock_registry)


class TestTriggerRun:
    async def test_unknown_batch_name_raises_404(self, service, mock_registry):
        mock_registry.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await service.trigger_run("not_a_real_batch")

        assert exc_info.value.status_code == 404

    async def test_already_running_raises_409(self, service, mock_repo):
        mock_repo.create_run.side_effect = BatchAlreadyRunningError("trends")

        with pytest.raises(HTTPException) as exc_info:
            await service.trigger_run("trends_batch")

        assert exc_info.value.status_code == 409

    async def test_success_returns_run_id_and_running_status(self, service, mock_repo, monkeypatch):
        run_id = uuid.uuid4()
        mock_repo.create_run.return_value = run_id
        fire_and_forget_mock = AsyncMock()
        monkeypatch.setattr(
            "Backend.app.services.admin_batch_service.fire_and_forget", fire_and_forget_mock
        )

        result = await service.trigger_run("trends_batch")

        assert result == {"run_id": str(run_id), "status": "running"}
        mock_repo.create_run.assert_awaited_once_with(kind="trends", trigger="manual")
        fire_and_forget_mock.assert_awaited_once()

    async def test_commits_session_before_spawning(self, service, mock_repo, monkeypatch):
        """The deliberate exception to the usual get_async_db-commits-after-handler
        convention: the spawned subprocess is a separate OS process that must see the
        new row immediately, so this commits explicitly, before spawning."""
        mock_repo.create_run.return_value = uuid.uuid4()
        call_order = []
        mock_repo._session = MagicMock()
        mock_repo._session.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))
        fire_and_forget_mock = AsyncMock(side_effect=lambda coro: call_order.append("spawn"))
        monkeypatch.setattr(
            "Backend.app.services.admin_batch_service.fire_and_forget", fire_and_forget_mock
        )

        await service.trigger_run("trends_batch")

        assert call_order == ["commit", "spawn"]


class TestGetRun:
    async def test_not_found_raises_404(self, service, mock_repo):
        mock_repo.get_run.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await service.get_run(uuid.uuid4())

        assert exc_info.value.status_code == 404

    async def test_wrong_kind_raises_404(self, service, mock_repo):
        mock_repo.get_run.return_value = _fake_run(run_id=uuid.uuid4(), kind="ingestion")

        with pytest.raises(HTTPException) as exc_info:
            await service.get_run(uuid.uuid4())

        assert exc_info.value.status_code == 404

    async def test_maps_started_status_to_running(self, service, mock_repo):
        run_id = uuid.uuid4()
        mock_repo.get_run.return_value = _fake_run(run_id=run_id, kind="trends", status="started")

        result = await service.get_run(run_id)

        assert result["status"] == "running"
        assert result["batch_name"] == "trends_batch"

    async def test_maps_completed_and_failed_statuses_unchanged(self, service, mock_repo):
        run_id = uuid.uuid4()
        mock_repo.get_run.return_value = _fake_run(run_id=run_id, kind="trends", status="completed")
        assert (await service.get_run(run_id))["status"] == "completed"

        mock_repo.get_run.return_value = _fake_run(
            run_id=run_id, kind="trends", status="failed", error="boom"
        )
        result = await service.get_run(run_id)
        assert result["status"] == "failed"
        assert result["error"] == "boom"


class TestListRuns:
    async def test_returns_items_and_total(self, service, mock_repo):
        run_id = uuid.uuid4()
        mock_repo.list_runs.return_value = ([_fake_run(run_id=run_id, kind="trends")], 1)

        result = await service.list_runs(limit=50, offset=0)

        assert result["total"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["run_id"] == str(run_id)
        mock_repo.list_runs.assert_awaited_once_with(
            kinds=["trends", "move_flagged", "unregister_deleted_images"], limit=50, offset=0,
        )
```

- [ ] **Step 2: Run the service tests to verify they fail**

Run: `cd Backend && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/test_admin_batch_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'Backend.app.services.admin_batch_service'`.

- [ ] **Step 3: Add `BatchRunRepository.list_runs`**

The service test above calls a repository method (`list_runs`) that doesn't exist yet. Add it to
`repository/batch_runs.py`:

```python
    async def list_runs(self, kinds: list[str], limit: int, offset: int) -> tuple[list[BatchRun], int]:
        base_where = BatchRun.kind.in_(kinds)
        items_result = await self._session.execute(
            select(BatchRun).where(base_where)
            .order_by(BatchRun.created_at.desc())
            .limit(limit).offset(offset)
        )
        total_result = await self._session.execute(
            select(func.count()).select_from(BatchRun).where(base_where)
        )
        return list(items_result.scalars()), total_result.scalar_one()
```

Add `from sqlalchemy import func` to `repository/batch_runs.py`'s imports if not already present.
Add a focused integration test for this new method to `tests/integration/test_batch_runs_repository.py`:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_list_runs_filters_by_kind_and_paginates(db_session):
    repo = BatchRunRepository(db_session)
    await repo.create_run(kind="trends", trigger="manual")
    await repo.create_run(kind="move_flagged", trigger="scheduled")
    await repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")

    items, total = await repo.list_runs(kinds=["trends", "move_flagged"], limit=10, offset=0)

    assert total == 2
    assert {item.kind for item in items} == {"trends", "move_flagged"}

    items_page_2, total_page_2 = await repo.list_runs(
        kinds=["trends", "move_flagged"], limit=1, offset=1
    )
    assert total_page_2 == 2
    assert len(items_page_2) == 1
```

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_batch_runs_repository.py -v`
Expected: all PASS including this new test, before continuing.

- [ ] **Step 4: Implement `AdminBatchService`**

Create `Backend/app/services/admin_batch_service.py`:

```python
import sys
import uuid

from fastapi import HTTPException

from Backend.app.batch_subprocess import build_log_path, fire_and_forget, spawn_and_track
from repository.batch_runs import BatchAlreadyRunningError, BatchRunRepository
from batch.registry import BatchRegistry

_ADMIN_KINDS = ["trends", "move_flagged", "unregister_deleted_images"]

_STATUS_MAP = {"started": "running", "completed": "completed", "failed": "failed"}


class AdminBatchService:
    def __init__(self, repo: BatchRunRepository, registry: BatchRegistry | None = None):
        self.repo = repo
        self.registry = registry or BatchRegistry()

    async def trigger_run(self, batch_name: str) -> dict:
        entry = self.registry.get(batch_name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Unknown batch: {batch_name}")

        try:
            run_id = await self.repo.create_run(kind=entry["kind"], trigger="manual")
        except BatchAlreadyRunningError:
            raise HTTPException(
                status_code=409, detail=f"{batch_name} is already running"
            )

        # Deliberate exception to the usual get_async_db convention: the spawned
        # subprocess is a separate OS process with its own DB connection and must
        # see this row the instant it starts, not whenever the request handler
        # eventually returns and get_async_db's post-handler commit fires.
        await self.repo._session.commit()

        app_env = __import__("os").environ.get("APP_ENV", "general")
        log_path = build_log_path(app_env, batch_name)
        args = [sys.executable, "-m", "batch.run_wrapper",
                "--script", batch_name, "--env", app_env, "--trigger", "manual",
                "--run-id", str(run_id)]
        await fire_and_forget(spawn_and_track(args, log_path))

        return {"run_id": str(run_id), "status": "running"}

    async def get_run(self, run_id: uuid.UUID) -> dict:
        run = await self.repo.get_run(run_id)
        if run is None or run.kind not in _ADMIN_KINDS:
            raise HTTPException(status_code=404, detail="Run not found")
        return self._to_response(run)

    async def list_runs(self, limit: int, offset: int) -> dict:
        items, total = await self.repo.list_runs(kinds=_ADMIN_KINDS, limit=limit, offset=offset)
        return {"items": [self._to_response(run) for run in items], "total": total}

    def _to_response(self, run) -> dict:
        return {
            "run_id": str(run.run_id),
            "batch_name": self.registry.name_for_kind(run.kind),
            "trigger": run.trigger,
            "status": _STATUS_MAP[run.status],
            "created_at": run.created_at,
            "completed_at": run.completed_at,
            "error": run.error,
        }
```

**Stop and reconsider the `__import__("os")` line above** — that's an ugly placeholder, not real
code to ship. Fix it before running tests: add `import os` to the top of the file alongside the
other imports, and change the line to plain `os.environ.get("APP_ENV", "general")`.

**Stop and reconsider `await self.repo._session.commit()`** — reaching into a repository's "private"
`_session` attribute from the service layer is fragile (relies on `BatchRunRepository` always
naming it exactly that). Check `repository/batch_runs.py`'s actual current constructor/attribute
name before writing this line — if it's `self._session` as shown throughout this plan and the
trigger-tracking plan, this works, but consider instead having `AdminBatchService.__init__` also
accept the raw `AsyncSession` directly (in addition to the repo), so this line becomes
`await self.session.commit()` against an attribute this service actually owns rather than reaching
into the repository's internals. Prefer that cleaner shape if it doesn't complicate the router's
dependency-injection wiring in Step 6 below; if it does, document why the direct `_session` access
was kept instead.

- [ ] **Step 5: Run the service tests to verify they pass**

Run: `cd Backend && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/test_admin_batch_service.py -v`
Expected: all PASS.

- [ ] **Step 6: Write the failing endpoint tests**

Create `Backend/tests/test_admin_batch_endpoints.py`, mirroring
`Backend/tests/test_ingestion_endpoints.py`'s `TestClient` + `dependency_overrides` style:

```python
"""
Tests for the admin batch controller endpoints.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.admin import router as admin_router

app = FastAPI()
app.include_router(admin_router, prefix="/api")


@pytest.fixture
def mock_service():
    return AsyncMock()


@pytest.fixture
def client(mock_service):
    async def override_get_admin_batch_service():
        yield mock_service

    from Backend.app.api.admin import get_admin_batch_service
    app.dependency_overrides[get_admin_batch_service] = override_get_admin_batch_service

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


class TestTriggerRun:
    def test_returns_run_id_and_running_status(self, client, mock_service):
        mock_service.trigger_run.return_value = {"run_id": "abc-123", "status": "running"}

        response = client.post("/api/admin/batches/trends_batch/run")

        assert response.status_code == 200
        assert response.json() == {"run_id": "abc-123", "status": "running"}
        mock_service.trigger_run.assert_awaited_once_with("trends_batch")

    def test_propagates_service_http_exceptions(self, client, mock_service):
        from fastapi import HTTPException
        mock_service.trigger_run.side_effect = HTTPException(status_code=409, detail="already running")

        response = client.post("/api/admin/batches/trends_batch/run")

        assert response.status_code == 409


class TestGetRun:
    def test_returns_run_status(self, client, mock_service):
        from datetime import datetime, timezone
        mock_service.get_run.return_value = {
            "run_id": "abc-123", "batch_name": "trends_batch", "trigger": "manual",
            "status": "running", "created_at": datetime.now(timezone.utc),
            "completed_at": None, "error": None,
        }

        response = client.get("/api/admin/batches/runs/abc-123")

        assert response.status_code == 200
        assert response.json()["status"] == "running"

    def test_not_found(self, client, mock_service):
        from fastapi import HTTPException
        mock_service.get_run.side_effect = HTTPException(status_code=404, detail="not found")

        response = client.get("/api/admin/batches/runs/00000000-0000-0000-0000-000000000000")

        assert response.status_code == 404


class TestListRuns:
    def test_returns_items_and_total(self, client, mock_service):
        mock_service.list_runs.return_value = {"items": [], "total": 0}

        response = client.get("/api/admin/batches/runs?limit=10&offset=0")

        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0}
        mock_service.list_runs.assert_awaited_once_with(limit=10, offset=0)
```

- [ ] **Step 7: Run the endpoint tests to verify they fail**

Run: `cd Backend && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/test_admin_batch_endpoints.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'Backend.app.api.admin'`.

- [ ] **Step 8: Implement `Backend/app/api/admin.py`**

```python
from typing import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

from Storage.db import get_async_db
from repository.batch_runs import BatchRunRepository
from Backend.app.services.admin_batch_service import AdminBatchService

router = APIRouter(prefix="/admin/batches", tags=["admin"])


class RunTriggerResponse(BaseModel):
    run_id: str
    status: str


class RunStatusResponse(BaseModel):
    run_id: str
    batch_name: str
    trigger: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime]
    error: Optional[str]


class RunListResponse(BaseModel):
    items: list[RunStatusResponse]
    total: int


async def get_admin_batch_service(
    db=Depends(get_async_db),
) -> AsyncGenerator[AdminBatchService, None]:
    yield AdminBatchService(BatchRunRepository(db))


@router.post("/{batch_name}/run", response_model=RunTriggerResponse)
async def trigger_run(batch_name: str, service: AdminBatchService = Depends(get_admin_batch_service)):
    return await service.trigger_run(batch_name)


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
async def get_run(run_id: UUID, service: AdminBatchService = Depends(get_admin_batch_service)):
    return await service.get_run(run_id)


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    limit: int = 50, offset: int = 0,
    service: AdminBatchService = Depends(get_admin_batch_service),
):
    return await service.list_runs(limit=limit, offset=offset)
```

- [ ] **Step 9: Run the endpoint tests to verify they pass**

Run: `cd Backend && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/test_admin_batch_endpoints.py -v`
Expected: all PASS.

- [ ] **Step 10: Run the full Backend suite**

Run: `cd Backend && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest -v`
Expected: all PASS, no regressions.

- [ ] **Step 11: Commit**

```bash
git add Backend/app/services/admin_batch_service.py Backend/app/api/admin.py Backend/tests/test_admin_batch_service.py Backend/tests/test_admin_batch_endpoints.py repository/batch_runs.py tests/integration/test_batch_runs_repository.py
git commit -m "feat: add admin batch controller (trigger/status/list endpoints)"
```

---

### Task 3: Wire into `main.py`, update `backend_api.md`, manual verification

**Files:**
- Modify: `Backend/app/main.py`
- Modify: `backend_api.md`

**Interfaces:**
- Consumes: `Backend.app.api.admin.router` (Task 2).

- [ ] **Step 1: Register the router**

In `Backend/app/main.py`, add the import near the other `Backend.app.api.*` imports:
```python
from Backend.app.api.admin import router as admin_router
```
And register it alongside the others:
```python
app.include_router(admin_router, prefix="/api")
```

- [ ] **Step 2: Run the full Backend suite**

Run: `cd Backend && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest -v`
Expected: all PASS, including `test_main.py`.

- [ ] **Step 3: Update `backend_api.md`**

Add a section documenting the three new endpoints — method, path, request body (none for
`POST .../run` beyond the path param), response shape (mirroring `RunTriggerResponse`/
`RunStatusResponse`/`RunListResponse` above), and status codes (`404` for an unknown batch name or
out-of-scope `run_id`, `409` for an already-running batch). Follow the existing formatting
convention already used in this file for the other routers (read a couple of existing entries in
`backend_api.md` first to match heading level/style before writing the new section).

- [ ] **Step 4: Manual verification**

Given this sandbox has no real `environments/.env.*` secrets (see this session's earlier scheduler
work), full live verification (starting a real backend, hitting the endpoint, confirming a
`batch_runs` row and a `logs/{env}/...` file both appear) is the same kind of sandbox limitation
noted there — attempt it if a real dev environment is reachable; if not, note in the final report
exactly what could and couldn't be verified live, same as that prior work's honest
`DONE_WITH_CONCERNS` handling. At minimum, with a real dev DB reachable (e.g. the same
`ocrdb_test` used by `tests/integration/`), this can be checked directly with `curl` against a
locally started backend using `environments/.env.metal` if that file exists on the machine doing
the implementation:
1. Start a backend for one environment.
2. `curl -X POST http://localhost:8081/api/admin/batches/move_flagged/run` — confirm a `run_id` +
   `"status": "running"` comes back immediately (not after `move_flagged` finishes).
3. `curl http://localhost:8081/api/admin/batches/runs/<run_id>` — confirm it eventually reports
   `"completed"`.
4. `curl http://localhost:8081/api/admin/batches/runs` — confirm the triggered run appears, and that
   `logs/metal/move_flagged_<timestamp>.log` was created on disk.
5. `curl -X POST http://localhost:8081/api/admin/batches/trends_batch/run` twice in quick succession
   — confirm the second returns `409`.

- [ ] **Step 5: Commit**

```bash
git add Backend/app/main.py backend_api.md
git commit -m "feat: register admin batch controller router, document new endpoints"
```

## Self-Review Notes

- **Spec coverage:** subprocess extraction with unchanged behavior (Task 1), all three endpoints +
  the explicit-commit-before-spawn exception + `BatchAlreadyRunningError`→409 + registry-based
  validation→404 + kind-scoping→404 (Task 2), `main.py` wiring + `backend_api.md` (Task 3) — all
  present. The log-naming design (nested by environment, no run_id linkage, no download endpoint)
  lives in Task 1's `build_log_path` and is exercised by Task 1's own tests.
- **Gap found and fixed during this plan's own writing (Task 2):** the spec's sketch didn't include
  a `BatchRunRepository.list_runs` method — `get_active_run`/`get_most_recent_run` only ever return
  a single row. Step 3 adds it explicitly, with its own integration test, before the service that
  depends on it is implemented.
- **Placeholder caught during this plan's own writing (Task 2, Step 4):** the first draft of
  `AdminBatchService.trigger_run` used `__import__("os")` inline as a lazy shortcut — flagged
  explicitly as not real code to ship, with the actual fix (`import os` at module level) spelled
  out, plus a second flag about the fragility of reaching into `repo._session` directly and a
  preferred alternative to consider.
- **Type consistency:** `AdminBatchService.trigger_run/get_run/list_runs` signatures and return
  dict shapes match exactly what `Backend/app/api/admin.py`'s three endpoints call and what their
  Pydantic response models expect; `BatchRunRepository.list_runs`'s signature matches both the
  service's call and its own integration test.
