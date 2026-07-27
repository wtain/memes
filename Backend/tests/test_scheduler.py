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
