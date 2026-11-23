"""
Unit tests for AdminBatchService. Repository/registry/subprocess interactions are all
mocked -- matching IngestionService's own test style (no real DB, no real subprocess).
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from Backend.app.services.admin_batch_service import AdminBatchService
from repository.batch_runs import BatchAlreadyRunningError


def _fake_run(*, run_id, kind, trigger="manual", status="started", error=None,
              created_at=None, completed_at=None, progress=None):
    return MagicMock(
        run_id=run_id, kind=kind, trigger=trigger, status=status, error=error, progress=progress,
        created_at=created_at or datetime.now(timezone.utc), completed_at=completed_at,
    )


@pytest.fixture
def mock_repo():
    return AsyncMock()


@pytest.fixture
def mock_session():
    return AsyncMock()


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    registry.all_names.return_value = ["trends_batch"]
    registry.get.return_value = {"module": "batch.trends_batch", "kind": "trends"}
    registry.name_for_kind.return_value = "trends_batch"
    return registry


@pytest.fixture
def service(mock_repo, mock_session, mock_registry):
    return AdminBatchService(mock_repo, mock_session, mock_registry)


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

    async def test_commits_session_before_spawning(self, service, mock_repo, mock_session, monkeypatch):
        """The deliberate exception to the usual get_async_db-commits-after-handler
        convention: the spawned subprocess is a separate OS process that must see the
        new row immediately, so this commits explicitly, before spawning. Commits via
        the AsyncSession the service was constructed with directly (not by reaching
        into the repository's "private" _session attribute)."""
        mock_repo.create_run.return_value = uuid.uuid4()
        call_order = []
        mock_session.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))
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

    async def test_to_response_includes_progress(self, service, mock_repo):
        progress = {
            "label": "images", "unit": "images", "done": 5, "total": 10, "rate_per_min": 2.5,
            "step": {"index": 1, "count": 3, "label": "embed"},
            "updated_at": "2026-11-23T10:00:00+00:00",
        }
        run_id = uuid.uuid4()
        mock_repo.get_run.return_value = _fake_run(run_id=run_id, kind="trends", progress=progress)
        assert (await service.get_run(run_id))["progress"] == progress

        mock_repo.get_run.return_value = _fake_run(run_id=run_id, kind="trends", progress=None)
        result = await service.get_run(run_id)
        assert "progress" in result and result["progress"] is None

    async def test_progress_without_step_is_normalised(self, service, mock_repo):
        run_id = uuid.uuid4()
        stored = {"label": "images", "unit": "images", "done": 1, "total": 2,
                  "rate_per_min": 1.0, "updated_at": "2026-11-23T10:00:00+00:00"}
        mock_repo.get_run.return_value = _fake_run(run_id=run_id, kind="trends", progress=stored)
        result = await service.get_run(run_id)
        assert result["progress"]["step"] is None
        assert result["progress"]["done"] == 1

    async def test_step_only_progress_validates(self, service, mock_repo):
        run_id = uuid.uuid4()
        stored = {"label": "x", "step": {"index": 1, "count": 2, "label": "s"},
                  "updated_at": "2026-11-23T10:00:00+00:00"}
        mock_repo.get_run.return_value = _fake_run(run_id=run_id, kind="trends", progress=stored)
        progress = (await service.get_run(run_id))["progress"]
        assert progress["done"] is None and progress["step"]["label"] == "s"

    @pytest.mark.parametrize("bad", [
        {"label": "x", "updated_at": "not a date"},
        {"label": "x", "step": {"count": 2, "label": "s"}, "updated_at": "2026-11-23T10:00:00+00:00"},
    ])
    async def test_malformed_progress_becomes_none(self, service, mock_repo, bad):
        run_id = uuid.uuid4()
        mock_repo.get_run.return_value = _fake_run(run_id=run_id, kind="trends", progress=bad)
        result = await service.get_run(run_id)
        assert result["progress"] is None
        assert result["batch_name"] == "trends_batch" and result["run_id"] == str(run_id)

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
        mock_repo.list_runs.assert_awaited_once_with(kinds=["trends"], limit=50, offset=0)

    async def test_derives_kinds_from_every_registered_name(self, service, mock_repo, mock_registry):
        """_admin_kinds must not be a separate hardcoded list -- it has to reflect
        whatever the registry currently returns, for every name, not just the first."""
        mock_registry.all_names.return_value = ["trends_batch", "build_bow"]
        mock_registry.get.side_effect = lambda name: {
            "trends_batch": {"module": "batch.trends_batch", "kind": "trends"},
            "build_bow": {"module": "batch.build_bow", "kind": "build_bow"},
        }[name]
        mock_repo.list_runs.return_value = ([], 0)

        await service.list_runs(limit=50, offset=0)

        mock_repo.list_runs.assert_awaited_once_with(kinds=["trends", "build_bow"], limit=50, offset=0)


class TestListBatchNames:
    async def test_returns_registry_names(self, service, mock_registry):
        mock_registry.all_names.return_value = ["trends_batch", "build_bow"]

        result = await service.list_batch_names()

        assert result == {"names": ["trends_batch", "build_bow"]}


class TestFailRunIfStillStarted:
    """SYN-3: the callback spawn_and_track runs on abnormal exit fails the run row, but only
    from `started`, so a run the child already finished is never overwritten."""

    async def _run(self, monkeypatch, status):
        from contextlib import asynccontextmanager
        import Backend.app.services.admin_batch_service as mod

        repo = AsyncMock()
        repo.get_run.return_value = _fake_run(run_id=uuid.uuid4(), kind="trends", status=status)
        session = AsyncMock()

        class _Ctx:
            async def __aenter__(self_inner):
                return session
            async def __aexit__(self_inner, *a):
                return False

        monkeypatch.setattr(mod, "AsyncSessionLocal", lambda: _Ctx())
        monkeypatch.setattr(mod, "BatchRunRepository", lambda s: repo)
        run_id = uuid.uuid4()
        await mod.fail_run_if_still_started(run_id, "exited with code 1")
        return repo, session, run_id

    async def test_fails_started_run(self, monkeypatch):
        repo, session, run_id = await self._run(monkeypatch, "started")
        repo.fail.assert_awaited_once()
        assert "exited with code 1" in repo.fail.await_args.kwargs["error"]
        session.commit.assert_awaited_once()

    async def test_leaves_finished_run_alone(self, monkeypatch):
        for status in ("completed", "failed", "aborted"):
            repo, session, _ = await self._run(monkeypatch, status)
            repo.fail.assert_not_awaited()

    async def test_trigger_passes_callback_to_spawn(self, service, mock_repo, monkeypatch):
        mock_repo.create_run.return_value = uuid.uuid4()
        captured = {}

        def fake_spawn(args, log_path, label, on_abnormal_exit=None):
            captured["cb"] = on_abnormal_exit
            async def _noop():
                return 0
            return _noop()

        monkeypatch.setattr("Backend.app.services.admin_batch_service.spawn_and_track", fake_spawn)
        monkeypatch.setattr("Backend.app.services.admin_batch_service.fire_and_forget", AsyncMock())

        await service.trigger_run("trends_batch")

        assert callable(captured["cb"])
