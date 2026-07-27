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
