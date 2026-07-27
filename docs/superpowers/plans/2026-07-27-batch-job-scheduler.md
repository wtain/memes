# Batch Job Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a generic, config-driven recurring-job scheduler embedded in each environment's backend process, so jobs like `trends_batch` run on an interval without a human invoking them manually.

**Architecture:** A new `Backend/app/scheduler.py` module started/stopped from FastAPI's existing `lifespan` context manager. One `asyncio.Task` per configured job, each sleeping until due, then spawning `python -m batch.<module> --env <env>` as a subprocess. Restart-safe timing and crash recovery both key off the existing `BatchRun` table (`repository/batch_runs.py`), which gets one small additive method.

**Tech Stack:** Python 3.11, `asyncio` (stdlib only — no new dependency), FastAPI lifespan, SQLAlchemy async ORM via the existing `BatchRunRepository`, pytest + `pytest-asyncio` (`asyncio_mode = auto`).

**Spec:** `docs/superpowers/specs/2026-07-27-batch-job-scheduler-design.md`

## Global Constraints

- No new third-party dependency — plain `asyncio`, no APScheduler/celery/cron library.
- Simple interval scheduling only (`interval_minutes`) — no cron expressions.
- Jobs are invoked via subprocess (`sys.executable -m <module> --env <APP_ENV>`) — never modify any existing batch script's internals to make this work.
- Each environment's backend (metal/general/IT) runs its own independent scheduler instance against its own database — no cross-environment coordination.
- No new API endpoint or UI surface. Observability is the `batch_runs` table plus per-job log files only.
- No retry-on-failure logic. A failed tick is logged; the scheduler waits for the next regular interval.
- In-flight subprocesses are never killed on backend shutdown — they're allowed to finish.
- `scheduler.enabled: false` must result in zero scheduler tasks being started for that environment.
- A single tick's unhandled exception must not stop that job from ever being scheduled again — must be caught, logged, and the loop must continue.

---

### Task 1: `get_most_recent_run` on `BatchRunRepository`

**Files:**
- Modify: `repository/batch_runs.py`
- Test: `tests/integration/test_batch_runs_repository.py`

**Interfaces:**
- Produces: `BatchRunRepository.get_most_recent_run(kind: str) -> BatchRun | None` — the most recent row for `kind` **regardless of status** (unlike `get_active_run`, which only returns rows with `status == "started"`), ordered by `created_at` descending, or `None` if no run of that kind exists yet. Task 2's `_initial_delay` depends on this exact signature.

Existing `BatchRun` model fields for reference (`Storage/models.py`, unchanged): `run_id`, `kind`, `created_at`, `completed_at`, `status`, `stage`, `stats`, `error`.

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_batch_runs_repository.py`:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_get_most_recent_run_returns_latest_regardless_of_status(db_session):
    repo = BatchRunRepository(db_session)
    older_id = await repo.create_run(kind="trends")
    await repo.commit(older_id)
    newer_id = await repo.create_run(kind="trends")
    await repo.fail(newer_id, error="disk full")

    result = await repo.get_most_recent_run(kind="trends")

    assert result is not None
    assert result.run_id == newer_id


@pytest.mark.asyncio(loop_scope="session")
async def test_get_most_recent_run_none_when_no_runs_of_that_kind(db_session):
    repo = BatchRunRepository(db_session)
    await repo.create_run(kind="ingestion", stage="hash_dedup")

    assert await repo.get_most_recent_run(kind="trends") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_batch_runs_repository.py -v`
Expected: the two new tests FAIL with `AttributeError: 'BatchRunRepository' object has no attribute 'get_most_recent_run'`; all pre-existing tests in the file still PASS.

- [ ] **Step 3: Implement `get_most_recent_run`**

In `repository/batch_runs.py`, add (next to `get_active_run`):

```python
    async def get_most_recent_run(self, kind: str) -> BatchRun | None:
        result = await self._session.execute(
            select(BatchRun)
            .where(BatchRun.kind == kind)
            .order_by(BatchRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
```

- [ ] **Step 4: Run the full integration root to verify pass and no regressions**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
Expected: all tests PASS, including the two new ones. (Full root, not just this file — `repository/batch_runs.py` is shared code per the "Running the right test scope" note in `CLAUDE.md`.)

- [ ] **Step 5: Commit**

```bash
git add repository/batch_runs.py tests/integration/test_batch_runs_repository.py
git commit -m "feat: add get_most_recent_run to BatchRunRepository"
```

---

### Task 2: Scheduler config parsing and timing/guard logic

**Files:**
- Create: `Backend/app/scheduler.py`
- Modify: `environments/settings.yaml`
- Test: `Backend/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `BatchRunRepository.get_most_recent_run(kind) -> BatchRun | None` and `.get_active_run(kind) -> BatchRun | None` (existing) and `.fail(run_id, error=None) -> None` (existing), all from Task 1's `repository/batch_runs.py`. `BatchRun.created_at` is a timezone-aware `datetime`.
- Produces (used by Task 3):
  - `_load_job_configs(cfg=settings) -> list[dict]` — each dict has exactly the keys `name`, `module`, `batch_run_kind`, `interval_minutes`, `max_runtime_minutes` (all required in config; `interval_minutes`/`max_runtime_minutes` are `int` minutes).
  - `async def _initial_delay(repo: BatchRunRepository, job: dict) -> float` — seconds to sleep before this job's first tick.
  - `async def _should_run(repo: BatchRunRepository, job: dict) -> bool` — whether this tick should spawn a run; mutates (via `repo.fail`) but does not commit — the caller (Task 3) owns the session/commit.

**Config schema** — add to `environments/settings.yaml` (common, applies to all three environments unless a `settings.<env>.yaml` overrides it — none need to for now):

```yaml
scheduler:
  enabled: true
  jobs:
    - name: trends_batch
      module: batch.trends_batch
      batch_run_kind: trends
      interval_minutes: 360
      max_runtime_minutes: 60
      enabled: true
```

- [ ] **Step 1: Write the failing tests**

Create `Backend/tests/test_scheduler.py`:

```python
"""
Unit tests for Backend/app/scheduler.py's config parsing and timing/guard
logic. Repository interactions are mocked (AsyncMock) -- no real DB, matching
the ImageService test style in test_image_service.py.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from Backend.app.scheduler import _initial_delay, _load_job_configs, _should_run


def _job(**overrides):
    base = {
        "name": "trends_batch",
        "module": "batch.trends_batch",
        "batch_run_kind": "trends",
        "interval_minutes": 360,
        "max_runtime_minutes": 60,
        "enabled": True,
    }
    base.update(overrides)
    return base


def _settings(enabled, jobs):
    return SimpleNamespace(SCHEDULER=SimpleNamespace(ENABLED=enabled, JOBS=jobs))


def _run(*, run_id="run-1", created_at):
    return SimpleNamespace(run_id=run_id, created_at=created_at)


class TestLoadJobConfigs:
    def test_returns_empty_when_scheduler_disabled(self):
        cfg = _settings(False, [_job()])

        assert _load_job_configs(cfg) == []

    def test_skips_jobs_with_enabled_false(self):
        cfg = _settings(True, [_job(name="on", enabled=True), _job(name="off", enabled=False)])

        result = _load_job_configs(cfg)

        assert [job["name"] for job in result] == ["on"]

    def test_returns_expected_fields_only(self):
        cfg = _settings(True, [_job()])

        result = _load_job_configs(cfg)

        assert result == [{
            "name": "trends_batch",
            "module": "batch.trends_batch",
            "batch_run_kind": "trends",
            "interval_minutes": 360,
            "max_runtime_minutes": 60,
        }]

    def test_job_missing_enabled_key_defaults_to_included(self):
        job_without_enabled = _job()
        del job_without_enabled["enabled"]
        cfg = _settings(True, [job_without_enabled])

        result = _load_job_configs(cfg)

        assert len(result) == 1


class TestInitialDelay:
    async def test_zero_when_no_prior_run(self):
        repo = AsyncMock()
        repo.get_most_recent_run.return_value = None

        delay = await _initial_delay(repo, _job())

        assert delay == 0.0

    async def test_zero_when_last_run_older_than_interval(self):
        repo = AsyncMock()
        repo.get_most_recent_run.return_value = _run(
            created_at=datetime.now(timezone.utc) - timedelta(minutes=400)
        )

        delay = await _initial_delay(repo, _job(interval_minutes=360))

        assert delay == 0.0

    async def test_remaining_time_when_last_run_recent(self):
        repo = AsyncMock()
        repo.get_most_recent_run.return_value = _run(
            created_at=datetime.now(timezone.utc) - timedelta(minutes=100)
        )

        delay = await _initial_delay(repo, _job(interval_minutes=360))

        # ~260 minutes remaining, in seconds -- allow slack for test execution time
        assert 15500 < delay < 15700


class TestShouldRun:
    async def test_true_when_no_active_run(self):
        repo = AsyncMock()
        repo.get_active_run.return_value = None

        assert await _should_run(repo, _job()) is True
        repo.fail.assert_not_called()

    async def test_false_when_active_run_is_recent(self):
        repo = AsyncMock()
        repo.get_active_run.return_value = _run(
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5)
        )

        assert await _should_run(repo, _job(max_runtime_minutes=60)) is False
        repo.fail.assert_not_called()

    async def test_true_and_marks_failed_when_active_run_is_stale(self):
        repo = AsyncMock()
        repo.get_active_run.return_value = _run(
            run_id="stale-run-1",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=90),
        )

        result = await _should_run(repo, _job(max_runtime_minutes=60))

        assert result is True
        repo.fail.assert_awaited_once_with(
            "stale-run-1", error="orphaned: presumed crashed or killed"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Backend && pytest tests/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'Backend.app.scheduler'`.

- [ ] **Step 3: Implement the config-parsing and timing/guard logic**

Create `Backend/app/scheduler.py`:

```python
import logging
from datetime import datetime, timezone

from config.settings import settings
from repository.batch_runs import BatchRunRepository

logger = logging.getLogger(__name__)


def _load_job_configs(cfg=settings) -> list[dict]:
    if not cfg.SCHEDULER.ENABLED:
        return []
    result = []
    for job in cfg.SCHEDULER.JOBS:
        if not job.get("enabled", True):
            continue
        result.append({
            "name": job["name"],
            "module": job["module"],
            "batch_run_kind": job["batch_run_kind"],
            "interval_minutes": job["interval_minutes"],
            "max_runtime_minutes": job["max_runtime_minutes"],
        })
    return result


async def _initial_delay(repo: BatchRunRepository, job: dict) -> float:
    """Seconds to wait before this job's first fire, restart-safe.

    If the most recent run for job['batch_run_kind'] (any status) is younger
    than the configured interval, delay only the remainder rather than firing
    immediately -- so a backend restart (e.g. dev --reload) doesn't re-fire a
    job ahead of schedule.
    """
    interval_seconds = job["interval_minutes"] * 60
    last_run = await repo.get_most_recent_run(job["batch_run_kind"])
    if last_run is None:
        return 0.0
    elapsed = (datetime.now(timezone.utc) - last_run.created_at).total_seconds()
    return max(interval_seconds - elapsed, 0.0)


async def _should_run(repo: BatchRunRepository, job: dict) -> bool:
    """Whether this tick should spawn a new run.

    Recovers from orphaned runs (process/machine killed without reaching
    commit()/fail()): a still-"active" run older than max_runtime_minutes is
    marked failed here so it stops blocking the schedule. Does not commit --
    the caller owns the session/commit.
    """
    active = await repo.get_active_run(job["batch_run_kind"])
    if active is None:
        return True

    age_seconds = (datetime.now(timezone.utc) - active.created_at).total_seconds()
    if age_seconds < job["max_runtime_minutes"] * 60:
        return False

    await repo.fail(active.run_id, error="orphaned: presumed crashed or killed")
    return True
```

Add the `scheduler` block shown above to `environments/settings.yaml`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Backend && pytest tests/test_scheduler.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add Backend/app/scheduler.py Backend/tests/test_scheduler.py environments/settings.yaml
git commit -m "feat: add scheduler config parsing and timing/guard logic"
```

---

### Task 3: Job orchestration, subprocess spawning, and lifespan wiring

**Files:**
- Modify: `Backend/app/scheduler.py`
- Modify: `Backend/app/main.py`
- Test: `Backend/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `_load_job_configs`, `_initial_delay`, `_should_run` (Task 2, same module); `Storage.db.AsyncSessionLocal` (existing); `repository.batch_runs.BatchRunRepository` (existing/Task 1).
- Produces: `async def start_scheduler() -> list[asyncio.Task]` and `async def stop_scheduler(tasks: list[asyncio.Task]) -> None`, wired into `Backend/app/main.py`'s `lifespan`.

- [ ] **Step 1: Write the failing tests**

Append to `Backend/tests/test_scheduler.py`:

```python
import asyncio
import sys

import Backend.app.scheduler as scheduler_module
from Backend.app.scheduler import start_scheduler, stop_scheduler


class TestSafeTick:
    async def test_calls_run_tick_with_job_and_app_env(self, monkeypatch):
        run_tick = AsyncMock()
        monkeypatch.setattr(scheduler_module, "_run_tick", run_tick)

        await scheduler_module._safe_tick(_job(), "general")

        run_tick.assert_awaited_once_with(_job(), "general")

    async def test_swallows_exception_from_run_tick(self, monkeypatch):
        monkeypatch.setattr(
            scheduler_module, "_run_tick", AsyncMock(side_effect=RuntimeError("boom"))
        )

        await scheduler_module._safe_tick(_job(), "general")  # must not raise


class TestSpawn:
    async def test_invokes_subprocess_with_expected_args(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        fake_proc = AsyncMock()
        fake_proc.wait = AsyncMock(return_value=0)
        create_subprocess = AsyncMock(return_value=fake_proc)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess)

        await scheduler_module._spawn(_job(), "general")

        args, kwargs = create_subprocess.call_args
        assert args == (sys.executable, "-m", "batch.trends_batch", "--env", "general")
        assert "stdout" in kwargs and "stderr" in kwargs
        fake_proc.wait.assert_awaited_once()
        assert (tmp_path / "logs" / "scheduler-trends_batch.log").exists()


class TestStartStopScheduler:
    async def test_start_creates_one_task_per_job_stop_cancels_all(self, monkeypatch):
        monkeypatch.setattr(
            scheduler_module, "_load_job_configs", lambda: [_job(name="a"), _job(name="b")]
        )

        async def _fake_job_loop(job, app_env):
            await asyncio.Event().wait()  # blocks until cancelled

        monkeypatch.setattr(scheduler_module, "_job_loop", _fake_job_loop)

        tasks = await start_scheduler()
        assert len(tasks) == 2
        assert all(not task.done() for task in tasks)

        await stop_scheduler(tasks)
        assert all(task.done() for task in tasks)

    async def test_start_creates_no_tasks_when_scheduler_disabled(self, monkeypatch):
        monkeypatch.setattr(scheduler_module, "_load_job_configs", lambda: [])

        tasks = await start_scheduler()

        assert tasks == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Backend && pytest tests/test_scheduler.py -v`
Expected: FAIL with `AttributeError`/`ImportError` for `_safe_tick`, `_spawn`, `start_scheduler`, `stop_scheduler` (not yet defined), while all Task 2 tests still PASS.

- [ ] **Step 3: Implement orchestration**

Append to `Backend/app/scheduler.py` (add `asyncio`, `os`, `sys`, `Path` imports at the top alongside the existing ones):

```python
import asyncio
import os
import sys
from pathlib import Path

from Storage.db import AsyncSessionLocal
```

```python
async def _run_tick(job: dict, app_env: str) -> None:
    async with AsyncSessionLocal() as session:
        repo = BatchRunRepository(session)
        run_now = await _should_run(repo, job)
        await session.commit()
    if run_now:
        await _spawn(job, app_env)


async def _safe_tick(job: dict, app_env: str) -> None:
    try:
        await _run_tick(job, app_env)
    except Exception:
        logger.exception("scheduler: job %s tick failed", job["name"])


async def _spawn(job: dict, app_env: str) -> None:
    log_path = Path("logs") / f"scheduler-{job['name']}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log_file:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", job["module"], "--env", app_env,
            stdout=log_file, stderr=log_file,
        )
        await proc.wait()


async def _job_loop(job: dict, app_env: str) -> None:
    async with AsyncSessionLocal() as session:
        delay = await _initial_delay(BatchRunRepository(session), job)

    interval_seconds = job["interval_minutes"] * 60
    while True:
        await asyncio.sleep(delay)
        delay = interval_seconds
        await _safe_tick(job, app_env)


async def start_scheduler() -> list[asyncio.Task]:
    app_env = os.environ.get("APP_ENV", "general")
    return [asyncio.create_task(_job_loop(job, app_env)) for job in _load_job_configs()]


async def stop_scheduler(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Backend && pytest tests/test_scheduler.py -v`
Expected: all PASS.

- [ ] **Step 5: Wire into `Backend/app/main.py`**

In `Backend/app/main.py`, add the import near the other `Backend.app.*` imports:

```python
from Backend.app.scheduler import start_scheduler, stop_scheduler
```

Change the `lifespan` function (currently `Backend/app/main.py:62-65`):

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    _configure_logging()
    scheduler_tasks = await start_scheduler()
    yield
    await stop_scheduler(scheduler_tasks)
```

- [ ] **Step 6: Run the full Backend test suite to check for regressions**

Run: `cd Backend && pytest`
Expected: all PASS, including `test_main.py` and `test_scheduler.py`.

- [ ] **Step 7: Manual verification**

1. In `environments/settings.yaml`, temporarily set (for this manual check only — revert after):
   ```yaml
   scheduler:
     enabled: true
     jobs:
       - name: trends_batch
         module: batch.trends_batch
         batch_run_kind: trends
         interval_minutes: 1
         max_runtime_minutes: 1
         enabled: true
   ```
2. Start a backend: `set WATCHFILES_FORCE_POLLING=1` then
   `uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.metal --port 8081 --host 0.0.0.0`
   (metal is arbitrary — any of the three environments' own DB/port works; don't reuse a port already in use by the developer's own running environments — see `environments/Environments.md`).
3. Within ~1 minute, confirm a new row appears: `SELECT * FROM batch_runs WHERE kind='trends' ORDER BY created_at DESC LIMIT 1;` against that environment's DB, and that `logs/scheduler-trends_batch.log` was created with output.
4. Restart the backend (Ctrl+C, start it again) within the 1-minute window and confirm the next run does **not** fire early (restart-safety) — check `batch_runs` timestamps stay ~1 minute apart, not less.
5. Manually kill the spawned subprocess mid-run (find it via Task Manager / `Get-Process python`) and confirm: the `batch_runs` row it left behind stays `status='started'` until `max_runtime_minutes` (1 min) elapses, then gets marked `status='failed'`, `error='orphaned: presumed crashed or killed'` on the next tick, and a fresh run spawns after that.
6. Revert the temporary `interval_minutes`/`max_runtime_minutes` values back to `360`/`60` in `environments/settings.yaml`.

- [ ] **Step 8: Update `backend_api.md` if applicable, and commit**

This change adds no new API endpoint, so `backend_api.md` needs no update. Commit:

```bash
git add Backend/app/scheduler.py Backend/app/main.py Backend/tests/test_scheduler.py
git commit -m "feat: spawn and schedule batch jobs from the backend lifespan"
```

---

## Self-Review Notes

- **Spec coverage:** config schema (Task 2), restart-safe timing (Task 2 `_initial_delay`), concurrency guard + orphan recovery (Task 2 `_should_run`), subprocess invocation (Task 3 `_spawn`), per-tick error isolation (Task 3 `_safe_tick`), lifespan wiring (Task 3 Step 5), per-environment scope (inherent — each backend process only ever loads its own `environments/settings.<env>.yaml` and DB) — all covered. The "jobs without BatchRun integration" and "operational caveat" sections of the spec are documentation/rationale, not code — no task needed for them beyond the config/doc comments already present in the spec itself.
- **Deviation from spec, noted:** the spec's reuse claim ("no repository changes needed") undersold Task 1 — `_initial_delay` needs the most recent run *of any status*, which `get_active_run` (status-filtered) can't provide, so one small additive repository method was needed. Purely additive, doesn't change any existing method's behavior.
- **Type consistency:** `job: dict` shape (`name`, `module`, `batch_run_kind`, `interval_minutes`, `max_runtime_minutes`) is identical across `_load_job_configs`'s output (Task 2) and every consumer (`_initial_delay`, `_should_run` in Task 2; `_run_tick`, `_safe_tick`, `_spawn`, `_job_loop` in Task 3). `BatchRunRepository` method names/signatures match `repository/batch_runs.py` exactly as it exists today plus Task 1's addition.
