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

        returncode = await spawn_and_track(
            [sys.executable, "-m", "batch.run_wrapper"], log_path, label="trends_batch"
        )

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

        await spawn_and_track(["flaky"], log_path, label="flaky")

        mock_logger.warning.assert_called_once_with(
            "batch_subprocess: %s exited with code %s", "flaky", 1
        )

    async def test_logs_zero_exit_code_at_info_level(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        fake_proc = MagicMock()
        fake_proc.wait = MagicMock(return_value=0)
        monkeypatch.setattr(subprocess, "Popen", MagicMock(return_value=fake_proc))
        mock_logger = MagicMock()
        monkeypatch.setattr(batch_subprocess, "logger", mock_logger)
        log_path = tmp_path / "logs" / "metal" / "ok_x.log"

        await spawn_and_track(["ok"], log_path, label="ok")

        mock_logger.warning.assert_not_called()
        mock_logger.info.assert_any_call("batch_subprocess: %s exited with code %s", "ok", 0)


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

    async def test_holds_task_in_in_flight_set_until_done(self):
        # Direct test of the invariant fire_and_forget provides: it must keep a
        # strong reference to the task (via the module-level _in_flight set) for as
        # long as the task is pending, and release it once done.
        #
        # An earlier version of this test tried to prove that behaviorally, via
        # gc.collect() timing (create the task, gc.collect(), then assert it still
        # ran to completion). That doesn't actually distinguish a correct
        # fire_and_forget from a buggy one with no tracking at all: empirically
        # verified (temporarily removing the _in_flight.add call entirely) that the
        # behavioral version of this test still passed without it, because asyncio's
        # own scheduling machinery -- the loop's ready queue while a task is
        # scheduled to run, and the loop's timer heap / done-callback chain while a
        # task is suspended inside asyncio.sleep()/Event.wait()/etc. -- keeps a
        # pending task reachable from a GC root on its own, independent of any
        # external strong reference, for as long as it's actually pending. So
        # gc.collect() never collects it either way, and the old test's pass/fail
        # outcome said nothing about _in_flight specifically. Asserting against
        # _in_flight directly instead tests the actual mechanism.
        release = asyncio.Event()

        async def _work():
            await release.wait()

        before = set(batch_subprocess._in_flight)
        await fire_and_forget(_work())
        added = set(batch_subprocess._in_flight) - before
        assert len(added) == 1, "fire_and_forget did not add the task to _in_flight"
        pending_task = next(iter(added))

        release.set()
        await asyncio.sleep(0.05)  # let the task run to completion and its done-callback fire

        assert pending_task not in batch_subprocess._in_flight

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
            spawn_and_track([sys.executable, "sleepy_job.py"], log_path, label="sleepy")
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
                spawn_and_track([sys.executable, "sleepy_job.py"], log_path, label="sleepy")
            )
            await asyncio.sleep(0.5)
            assert not task.done(), "spawn finished before we could tear down the loop"
            assert not done_marker.exists()
            return task

        close_started_at = time.monotonic()
        with asyncio.Runner() as runner:
            runner.run(_drive())
        close_elapsed = time.monotonic() - close_started_at
        # asyncio's subprocess transport participates in a reference cycle, so plain
        # refcounting does not free it the instant Runner.close() drops the loop's own
        # references -- only the cyclic GC does, and it isn't guaranteed to run on its
        # own before the poll loop below starts checking done_marker. Without this
        # explicit collect, a regression that reintroduces the killed-on-GC transport
        # (asyncio.create_subprocess_exec in place of subprocess.Popen) can slip
        # through: the transport would eventually be collected and its child killed,
        # but only after this test already observed done_marker and declared success,
        # or possibly never within this process's lifetime. Forcing collection here,
        # right after Runner.close() and before the poll loop, makes the kill (if any)
        # observable to the assertions below instead of racing it. Verified by
        # temporarily reintroducing asyncio.create_subprocess_exec + a bare
        # `await proc.wait()` into spawn_and_track: with this gc.collect() present,
        # the test correctly fails; on the real subprocess.Popen-based spawn_and_track,
        # it still passes -- see this task's report for the mutation-testing
        # re-verification of that claim.
        gc.collect()
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
