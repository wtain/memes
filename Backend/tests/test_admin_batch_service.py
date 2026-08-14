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
              created_at=None, completed_at=None):
    return MagicMock(
        run_id=run_id, kind=kind, trigger=trigger, status=status, error=error,
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
        mock_repo.list_runs.assert_awaited_once_with(
            kinds=[
                "trends", "move_flagged", "unregister_deleted_images",
                "ingestion_auto_prep", "build_tags_from_ocr", "build_ocr_lemmas",
                "build_tags_from_descriptions", "build_concept_embeddings",
                "detect_entities_and_tag", "tag_images_from_concepts", "build_bow",
            ],
            limit=50, offset=0,
        )
