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


import asyncio
import gc
import sys
from pathlib import Path

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
        log_path = tmp_path / "logs" / "scheduler-trends_batch.log"
        assert log_path.exists()
        # Must be the actual opened log file object, not merely "some truthy value" --
        # a regression that silently swapped in subprocess.PIPE would pass a weaker
        # "key present" check but must fail this one. _spawn opens the file with a
        # relative Path (cwd is monkeypatched to tmp_path), so compare against that
        # same relative form rather than the absolute tmp_path-prefixed one.
        relative_log_path = Path("logs") / "scheduler-trends_batch.log"
        assert kwargs["stdout"].name == str(relative_log_path)
        assert kwargs["stderr"].name == str(relative_log_path)
        assert kwargs["stdout"] is kwargs["stderr"]
        fake_proc.wait.assert_awaited_once()


class TestSpawnSurvivesJobLoopCancellation:
    async def test_real_subprocess_survives_job_loop_cancellation(self, monkeypatch, tmp_path):
        """Regression test for a real bug found in review: cancelling the job-loop
        task (exactly what stop_scheduler does on shutdown) must never kill an
        in-flight subprocess. This can NOT be reproduced with a mocked subprocess --
        the bug lives in asyncio's real BaseSubprocessTransport GC/close() behavior
        (the transport kills its child during close() if it's collected before the
        child has been reaped), which only triggers with a genuine OS process/
        transport. So this test spawns a real Python script and verifies, via a
        file the script only writes after completing a multi-second sleep, that the
        child ran to completion rather than being killed.

        Deliberately captures only the subprocess's PID, never the asyncio Process/
        transport object itself: holding a second reference to Process would keep
        the transport alive independent of the job-loop task's coroutine frame,
        which would mask the very bug under test (the transport is only torn down,
        killing the child, when its LAST reference disappears -- exactly what
        happens when the frame that awaited proc.wait() is unwound by cancellation).
        An earlier version of this test kept the Process object alive via a
        captured list for convenience and consequently could not detect the bug at
        all, passing identically against both the buggy and fixed implementation.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "sleepy_job.py").write_text(
            "import time\n"
            "time.sleep(3)\n"
            "open('done.txt', 'w').close()\n"
        )
        job = _job(module="sleepy_job", name="sleepy")

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def commit(self):
                pass

        monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", lambda: _FakeSession())
        monkeypatch.setattr(scheduler_module, "_should_run", AsyncMock(return_value=True))

        # Wrap (don't replace) the real create_subprocess_exec so we can observe that a
        # subprocess was spawned, while still exercising a genuine OS process end to
        # end -- this is the crucial difference from TestSpawn's fully-mocked version.
        real_create_subprocess_exec = asyncio.create_subprocess_exec
        pids: list[int] = []
        spawned = asyncio.Event()

        async def _capturing_create_subprocess_exec(*args, **kwargs):
            proc = await real_create_subprocess_exec(*args, **kwargs)
            pids.append(proc.pid)
            spawned.set()
            return proc

        monkeypatch.setattr(
            asyncio, "create_subprocess_exec", _capturing_create_subprocess_exec
        )

        # Stand-in for the job-loop's own task -- the one start_scheduler creates and
        # stop_scheduler cancels.
        job_loop_task = asyncio.create_task(scheduler_module._run_tick(job, "general"))

        await asyncio.wait_for(spawned.wait(), timeout=5)
        assert pids, "subprocess was never spawned"
        done_marker = tmp_path / "done.txt"
        assert not done_marker.exists(), "test setup bug: child finished before we could cancel"

        # Cancel the job-loop stand-in exactly like stop_scheduler does. Under the old
        # (buggy) design, where _run_tick awaited _spawn inline, this task would still be
        # blocked inside `await proc.wait()` at this point, and cancelling it would tear
        # down the only frame referencing the Process/transport, triggering the transport
        # to kill the child during its GC/close() path. Post-fix, _run_tick has already
        # detached the wait via asyncio.create_task + _in_flight_spawns, so this
        # cancellation never touches the subprocess.
        job_loop_task.cancel()
        try:
            await job_loop_task
        except asyncio.CancelledError:
            pass

        # Give the interpreter a beat to run whatever GC/transport-close cancellation
        # would have triggered under the buggy implementation.
        gc.collect()
        await asyncio.sleep(0.1)

        # The real assertion: the child must run to completion on its own. Poll for the
        # file it only ever writes AFTER finishing its 3-second sleep.
        for _ in range(100):
            if done_marker.exists():
                break
            await asyncio.sleep(0.1)
        assert done_marker.exists(), (
            "child did not run to completion -- it was killed as a side effect of "
            "cancelling the job-loop task"
        )


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
