"""
Unit tests for Backend/app/scheduler.py's config parsing and timing/guard
logic. Repository interactions are mocked (AsyncMock) -- no real DB, matching
the ImageService test style in test_image_service.py.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


class _FakeSession:
    """Minimal async-context-manager stand-in for AsyncSessionLocal()'s session,
    used wherever a test needs to exercise real scheduler.py code that opens a
    session but doesn't care about actual DB behavior (that part is separately
    mocked via _should_run/_initial_delay).
    """

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def commit(self):
        pass


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

    def test_zero_interval_minutes_is_clamped_to_one(self):
        cfg = _settings(True, [_job(interval_minutes=0)])

        result = _load_job_configs(cfg)

        assert result[0]["interval_minutes"] == 1

    def test_skips_malformed_entry_and_logs_but_keeps_valid_ones(self, monkeypatch):
        """A typo'd/missing required key (e.g. batch_run_kind) in one job entry must
        not crash the whole backend at boot -- skip just that entry, log it, and
        keep loading the rest. batch_run_kind is mandatory (see design spec's "Jobs
        without BatchRun integration" section): _initial_delay/_should_run both key
        off it, so a job missing it can't be scheduled through this mechanism.
        """
        mock_logger = MagicMock()
        monkeypatch.setattr(scheduler_module, "logger", mock_logger)
        bad_job = _job(name="bad")
        del bad_job["batch_run_kind"]
        cfg = _settings(True, [bad_job, _job(name="good")])

        result = _load_job_configs(cfg)

        assert [job["name"] for job in result] == ["good"]
        mock_logger.error.assert_called_once()
        args, kwargs = mock_logger.error.call_args
        assert args[1] == "bad"


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

    async def test_true_and_marks_failed_when_active_run_is_stale(self, monkeypatch):
        mock_logger = MagicMock()
        monkeypatch.setattr(scheduler_module, "logger", mock_logger)
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
        # Spec requires a warning log on orphan/crash recovery, with job name and kind.
        mock_logger.warning.assert_called_once()
        args, kwargs = mock_logger.warning.call_args
        assert "trends_batch" in args
        assert "trends" in args


import asyncio
import gc
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

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
        fake_proc = MagicMock()
        fake_proc.wait = MagicMock(return_value=0)
        popen_mock = MagicMock(return_value=fake_proc)
        monkeypatch.setattr(subprocess, "Popen", popen_mock)

        await scheduler_module._spawn(_job(), "general")

        args, kwargs = popen_mock.call_args
        assert args[0] == [sys.executable, "-m", "batch.trends_batch", "--env", "general"]
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
        fake_proc.wait.assert_called_once()

    async def test_logs_launch_and_nonzero_exit_code(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        fake_proc = MagicMock()
        fake_proc.wait = MagicMock(return_value=1)
        monkeypatch.setattr(subprocess, "Popen", MagicMock(return_value=fake_proc))
        mock_logger = MagicMock()
        monkeypatch.setattr(scheduler_module, "logger", mock_logger)

        await scheduler_module._spawn(_job(name="flaky"), "general")

        mock_logger.info.assert_any_call("scheduler: job %s launching subprocess", "flaky")
        mock_logger.warning.assert_called_once_with(
            "scheduler: job %s exited with code %s", "flaky", 1
        )

    async def test_logs_zero_exit_code_at_info_level(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        fake_proc = MagicMock()
        fake_proc.wait = MagicMock(return_value=0)
        monkeypatch.setattr(subprocess, "Popen", MagicMock(return_value=fake_proc))
        mock_logger = MagicMock()
        monkeypatch.setattr(scheduler_module, "logger", mock_logger)

        await scheduler_module._spawn(_job(name="ok"), "general")

        mock_logger.warning.assert_not_called()
        mock_logger.info.assert_any_call("scheduler: job %s exited with code %s", "ok", 0)


class TestWaitForProcess:
    """_wait_for_process bridges a subprocess.Popen's blocking .wait() back to the
    event loop via a manually-created daemon thread + loop.call_soon_threadsafe,
    NOT asyncio.to_thread. That distinction is load-bearing: asyncio.to_thread
    dispatches to the loop's *default* ThreadPoolExecutor, whose worker threads
    are non-daemon, so both asyncio.Runner.close() (shutdown_default_executor(),
    unbounded on Python 3.11) and the interpreter's own atexit hook
    (concurrent.futures.thread._python_exit) block waiting for that thread before
    letting the process exit -- empirically confirmed to hang backend shutdown
    for as long as a scheduled job is still running. A manually-created daemon
    thread is invisible to both of those, so shutdown isn't blocked by it.
    """

    async def test_returns_the_process_returncode(self):
        class _FakeProc:
            def wait(self):
                time.sleep(0.05)
                return 7

        returncode = await scheduler_module._wait_for_process(_FakeProc())

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

        await scheduler_module._wait_for_process(_FakeProc())

        assert len(captured_threads) == 1
        assert captured_threads[0].daemon is True


class TestRunTickPropagatesSpawnFailure:
    async def test_spawn_failure_propagates_for_safe_tick_to_log(self, monkeypatch):
        """_run_tick awaits _spawn inline (not detached into its own task) so that a
        _spawn failure naturally propagates up through _safe_tick's existing
        exception handling, which already logs with job-name context. This is the
        simpler design restored after switching _spawn to subprocess.Popen +
        _wait_for_process's daemon thread: Popen.__del__ never kills its child (see
        CPython's subprocess.py -- it only emits a ResourceWarning and, if the
        child is still running, keeps itself alive in the module-level _active
        list until it CAN be waited on), unlike the old asyncio subprocess
        transport, so there's no cancellation-safety reason left to keep _spawn
        off the job-loop task's own call stack.
        """
        monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", lambda: _FakeSession())
        monkeypatch.setattr(scheduler_module, "_should_run", AsyncMock(return_value=True))
        monkeypatch.setattr(
            scheduler_module, "_spawn", AsyncMock(side_effect=RuntimeError("spawn boom"))
        )

        with pytest.raises(RuntimeError, match="spawn boom"):
            await scheduler_module._run_tick(_job(), "general")


class TestSafeInitialDelay:
    async def test_returns_computed_delay_on_success(self, monkeypatch):
        monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", lambda: _FakeSession())
        monkeypatch.setattr(scheduler_module, "_initial_delay", AsyncMock(return_value=42.0))

        delay = await scheduler_module._safe_initial_delay(_job())

        assert delay == 42.0

    async def test_falls_back_to_zero_and_logs_when_initial_delay_raises(self, monkeypatch):
        """I2: a startup-time DB hiccup computing the restart-safe delay must not
        kill the job-loop task before it ever enters its loop -- fall back to an
        immediate first tick instead, and log so the failure isn't silent.
        """
        monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", lambda: _FakeSession())
        monkeypatch.setattr(
            scheduler_module, "_initial_delay", AsyncMock(side_effect=RuntimeError("db down"))
        )
        mock_logger = MagicMock()
        monkeypatch.setattr(scheduler_module, "logger", mock_logger)

        delay = await scheduler_module._safe_initial_delay(_job(name="flaky"))

        assert delay == 0.0
        mock_logger.exception.assert_called_once()
        args, kwargs = mock_logger.exception.call_args
        assert args[1] == "flaky"


class TestSpawnSurvivesRealShutdown:
    async def test_real_subprocess_survives_job_loop_cancellation(self, monkeypatch, tmp_path):
        """A synthetic, lighter-weight check: cancel the task running _run_tick
        (as stop_scheduler does to each job-loop task) while it's genuinely blocked
        inside _spawn's _wait_for_process (the daemon-thread bridge), and confirm
        the real child process still runs to completion. See
        test_real_subprocess_survives_asyncio_runner_close below (a standalone,
        non-async test) for the closer-to-real-uvicorn-shutdown version using
        asyncio.Runner, which this async version can't do (a running pytest-asyncio
        loop can't also host asyncio.Runner's own loop) -- and which additionally
        verifies shutdown isn't blocked (this test only verifies the child survives,
        not how quickly the surrounding code returns).
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "sleepy_job.py").write_text(
            "import time\n"
            "time.sleep(3)\n"
            "open('done.txt', 'w').close()\n"
        )
        job = _job(module="sleepy_job", name="sleepy")

        monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", lambda: _FakeSession())
        monkeypatch.setattr(scheduler_module, "_should_run", AsyncMock(return_value=True))

        done_marker = tmp_path / "done.txt"

        job_loop_task = asyncio.create_task(scheduler_module._run_tick(job, "general"))

        await asyncio.sleep(0.5)  # let Popen actually launch + the thread dispatch the wait
        assert not job_loop_task.done(), "spawn finished before we could cancel -- flaky timing"
        assert not done_marker.exists()

        job_loop_task.cancel()
        try:
            await job_loop_task
        except asyncio.CancelledError:
            pass

        gc.collect()
        await asyncio.sleep(0.1)

        for _ in range(100):
            if done_marker.exists():
                break
            await asyncio.sleep(0.1)
        assert done_marker.exists(), (
            "child did not run to completion -- it was killed as a side effect of "
            "cancelling the job-loop task"
        )


def test_real_subprocess_survives_asyncio_runner_close(tmp_path):
    """Regression test for two separate findings across two review rounds, both of
    which require exercising a *real* asyncio.Runner teardown (mocks can't expose
    either):

    1. (Earlier finding) cancelling a single task explicitly (stop_scheduler, or
       the lighter-weight async test above) is not the only cancellation a real
       backend shutdown does. uvicorn's actual shutdown path is Server.run() ->
       asyncio.run(...) -> `with asyncio.Runner(...) as runner` -> Runner.close()
       -> _cancel_all_tasks(loop) -- which cancels EVERY remaining task on the
       loop, not just the ones stop_scheduler explicitly cancels. A prior fix
       (detaching _spawn into its own asyncio.Task, tracked via a strong reference
       so stop_scheduler's cancellation of the job-loop task wouldn't touch it)
       held up against a single explicit cancel, but not against this: the
       detached task is still an ordinary task on the same loop, so Runner.close()
       cancels it too, one level removed. Fixed by switching subprocess creation
       to subprocess.Popen (see _spawn's docstring): Popen.__del__ never kills its
       child, so no cancellation of any task, anywhere, can reach it.

    2. (Later finding, introduced by fixing #1) awaiting completion via
       asyncio.to_thread(proc.wait) traded that problem for a new one:
       to_thread's non-daemon ThreadPoolExecutor worker thread makes
       Runner.close() (via shutdown_default_executor(), unbounded on Python 3.11)
       and the interpreter's own atexit hook block waiting for that thread --
       i.e. for as long as the child subprocess is still running (up to
       max_runtime_minutes) -- which is the opposite of the intent (exit
       promptly, leave the child running as an orphan). Empirically confirmed:
       Runner.close() blocked for the full duration of a 6-second-sleeping test
       child under the to_thread version of this code. Fixed by
       _wait_for_process's manually-created daemon thread (see its docstring),
       which neither shutdown_default_executor() nor _python_exit's atexit join
       know about.

    This test drives the real _run_tick -> _spawn -> _wait_for_process path (a
    real subprocess.Popen child that sleeps for several seconds, real daemon
    thread) inside an actual asyncio.Runner, then lets the `with` block's exit
    trigger Runner.close() -- the same _cancel_all_tasks + shutdown_default_executor
    + loop.close() sequence uvicorn's own shutdown goes through -- and asserts
    BOTH properties that must hold simultaneously: the `with` block returns
    promptly (not blocked for anywhere near the child's remaining sleep), AND the
    child still completes on its own afterward, unattended.

    Deliberately a plain (non-async) test function: it manages its own
    asyncio.Runner-owned loop rather than running inside pytest-asyncio's per-test
    loop, so pytest must not wrap it in one.
    """
    old_cwd = Path.cwd()
    child_sleep_seconds = 5
    (tmp_path / "sleepy_job.py").write_text(
        "import time\n"
        f"time.sleep({child_sleep_seconds})\n"
        "open('done.txt', 'w').close()\n"
    )
    job = _job(module="sleepy_job", name="sleepy")
    done_marker = tmp_path / "done.txt"

    os.chdir(tmp_path)
    try:
        async def _drive():
            # Patch inside the Runner's own loop context -- plain attribute
            # assignment (not pytest's monkeypatch fixture, which isn't safe to use
            # from a sync test driving its own separate event loop) is fine here
            # since we restore it manually in the `finally` block below.
            task = asyncio.create_task(scheduler_module._run_tick(job, "general"))
            await asyncio.sleep(0.5)  # let Popen actually launch + dispatch the wait
            assert not task.done(), "spawn finished before we could tear down the loop"
            assert not done_marker.exists()
            return task

        original_async_session_local = scheduler_module.AsyncSessionLocal
        original_should_run = scheduler_module._should_run
        scheduler_module.AsyncSessionLocal = lambda: _FakeSession()
        scheduler_module._should_run = AsyncMock(return_value=True)
        try:
            close_started_at = time.monotonic()
            with asyncio.Runner() as runner:
                runner.run(_drive())
            # Runner.close() (triggered by the `with` block exiting) has now cancelled
            # every remaining task on that loop, run shutdown_default_executor(), and
            # closed the loop entirely -- mirroring uvicorn's real shutdown sequence.
            close_elapsed = time.monotonic() - close_started_at
        finally:
            scheduler_module.AsyncSessionLocal = original_async_session_local
            scheduler_module._should_run = original_should_run
    finally:
        os.chdir(old_cwd)

    # Property 1: shutdown must not block on the still-running child. The `with`
    # block (including the initial 0.5s setup sleep) must return in well under
    # the child's remaining sleep time -- generous margin (2s) above the ~0.5s
    # expected, but nowhere near child_sleep_seconds=5, so a regression back to
    # the to_thread-based wait (which blocked for the full ~5-6s in manual
    # verification) fails this loudly rather than marginally.
    assert close_elapsed < 2.0, (
        f"asyncio.Runner's `with` block took {close_elapsed:.2f}s to return -- "
        "shutdown is blocking on the child subprocess instead of returning "
        "promptly and leaving it running as an orphan"
    )

    # Property 2: the child must still be alive and complete on its own,
    # unattended, well after the loop that spawned it is gone. Poll for the file
    # it only ever writes after finishing its multi-second sleep.
    for _ in range(100):
        if done_marker.exists():
            break
        time.sleep(0.1)
    assert done_marker.exists(), (
        "child did not run to completion -- it was killed as a side effect of "
        "asyncio.Runner.close()'s full-loop task cancellation"
    )


class TestStopSchedulerErrorHandling:
    async def test_swallows_cancelled_error_as_before(self):
        async def _blocks_forever():
            await asyncio.Event().wait()

        task = asyncio.create_task(_blocks_forever(), name="ok-job")
        await asyncio.sleep(0)  # let it actually start

        await stop_scheduler([task])  # must not raise

        assert task.cancelled()

    async def test_logs_and_does_not_propagate_when_task_died_of_something_else(
        self, monkeypatch
    ):
        """I3: if a job-loop task already died for a reason other than cancellation
        (e.g. I2's failure mode slipping past _safe_initial_delay somehow, or any
        other bug), `await task` inside stop_scheduler must not re-raise that
        exception out of lifespan -- log it and move on.
        """
        mock_logger = MagicMock()
        monkeypatch.setattr(scheduler_module, "logger", mock_logger)

        async def _dies():
            raise RuntimeError("db down")

        task = asyncio.create_task(_dies(), name="flaky-job")
        for _ in range(50):  # let it actually finish (with the exception) first
            if task.done():
                break
            await asyncio.sleep(0.01)

        await stop_scheduler([task])  # must not raise

        mock_logger.exception.assert_called_once()
        args, kwargs = mock_logger.exception.call_args
        assert args[1] == "flaky-job"


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

    async def test_start_logs_loaded_job_names(self, monkeypatch):
        monkeypatch.setattr(
            scheduler_module, "_load_job_configs", lambda: [_job(name="a"), _job(name="b")]
        )

        async def _fake_job_loop(job, app_env):
            await asyncio.Event().wait()

        monkeypatch.setattr(scheduler_module, "_job_loop", _fake_job_loop)
        mock_logger = MagicMock()
        monkeypatch.setattr(scheduler_module, "logger", mock_logger)

        tasks = await start_scheduler()

        mock_logger.info.assert_called_once()
        args, kwargs = mock_logger.info.call_args
        assert args[1] == 2
        assert "a" in args[2] and "b" in args[2]

        await stop_scheduler(tasks)
