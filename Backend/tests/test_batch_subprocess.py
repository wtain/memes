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
        # Microsecond-resolution timestamp -- collision-free for this call frequency
        # in real usage (calls are seconds-to-minutes apart in practice). A tiny sleep
        # here guards against environments where datetime.now() is backed by a coarser
        # clock (e.g. some Windows Python builds resolve GetSystemTimeAsFileTime() at
        # ~15.6ms rather than the precise variant), where two calls issued back-to-back
        # with no real time passing between them can otherwise land on the exact same
        # microsecond and make this assertion fail deterministically, not flakily.
        path_a = build_log_path("metal", "trends_batch")
        time.sleep(0.02)
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
