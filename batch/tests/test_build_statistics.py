"""
Unit tests for batch/build_statistics.py: main()'s self-tracking contract (matching
test_build_tags_from_ocr.py's TestMain style), refresh_snapshot()'s session wiring, and the
registry / scheduler config that makes the script runnable. No real DB.
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

import batch.build_statistics as module
from batch.registry import BatchRegistry

REPO_ROOT = Path(__file__).parent.parent.parent


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


def _session_factory():
    session = AsyncMock()
    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return session, session_factory


class TestMain:
    @pytest.mark.asyncio
    async def test_self_tracked_path_uses_statistics_kind(self):
        refresh_mock = AsyncMock()

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "refresh_snapshot", refresh_mock):
            await module.main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="statistics", trigger="scheduled")
        refresh_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_pre_created_run_id_is_finished_not_recreated(self):
        refresh_mock = AsyncMock()

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "refresh_snapshot", refresh_mock):
            await module.main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        refresh_mock.assert_awaited_once_with()


class TestRefreshSnapshot:
    @pytest.mark.asyncio
    async def test_runs_the_shared_refresh_in_one_session_and_commits(self):
        session, session_factory = _session_factory()
        refresh_mock = AsyncMock()

        with patch.object(module, "AsyncSessionLocal", session_factory), \
             patch.object(module, "DiagnosticsRepository") as diagnostics_cls, \
             patch.object(module, "StatisticsSnapshotsRepository") as snapshots_cls, \
             patch.object(module, "refresh_corpus_snapshot", refresh_mock):
            await module.refresh_snapshot()

        diagnostics_cls.assert_called_once_with(session)
        snapshots_cls.assert_called_once_with(session)
        refresh_mock.assert_awaited_once_with(diagnostics_cls.return_value, snapshots_cls.return_value)
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_compute_failure_propagates_without_committing(self):
        session, session_factory = _session_factory()

        with patch.object(module, "AsyncSessionLocal", session_factory), \
             patch.object(module, "DiagnosticsRepository"), \
             patch.object(module, "StatisticsSnapshotsRepository"), \
             patch.object(module, "refresh_corpus_snapshot", AsyncMock(side_effect=RuntimeError("db down"))):
            with pytest.raises(RuntimeError, match="db down"):
                await module.refresh_snapshot()

        session.commit.assert_not_awaited()


class TestRegistrationAndSchedule:
    def test_registered_as_admin_triggerable_script(self):
        registry = BatchRegistry(base_dir=REPO_ROOT / "environments")

        assert registry.get("build_statistics") == {
            "module": "batch.build_statistics",
            "kind": "statistics",
        }

    def test_scheduled_hourly_with_a_registered_script(self):
        with open(REPO_ROOT / "environments" / "settings.yaml", encoding="utf-8") as f:
            jobs = yaml.safe_load(f)["scheduler"]["jobs"]
        job = next(j for j in jobs if j["name"] == "build_statistics")

        assert job["script"] == "build_statistics"
        assert job["batch_run_kind"] == "statistics"
        assert job["interval_minutes"] == 60
        assert job["max_runtime_minutes"] == 10
        assert job.get("enabled", True) is True
