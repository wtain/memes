"""
Unit tests for batch/run_tracking.py's two tracking context managers. Repository/session
interactions are mocked -- no real DB, matching Backend/tests/test_scheduler.py's style.
"""
from unittest.mock import AsyncMock, patch

import pytest

from batch.run_tracking import finish_existing_run, tracked_run


class TestTrackedRun:
    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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
    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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
