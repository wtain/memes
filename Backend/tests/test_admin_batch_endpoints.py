"""
Tests for the admin batch controller endpoints.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.admin import router as admin_router

app = FastAPI()
app.include_router(admin_router, prefix="/api")


@pytest.fixture
def mock_service():
    return AsyncMock()


@pytest.fixture
def client(mock_service):
    async def override_get_admin_batch_service():
        yield mock_service

    from Backend.app.api.admin import get_admin_batch_service
    app.dependency_overrides[get_admin_batch_service] = override_get_admin_batch_service

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


class TestListBatchNames:
    def test_returns_names(self, client, mock_service):
        mock_service.list_batch_names.return_value = {"names": ["trends_batch", "build_bow"]}

        response = client.get("/api/admin/batches/names")

        assert response.status_code == 200
        assert response.json() == {"names": ["trends_batch", "build_bow"]}


class TestTriggerRun:
    def test_returns_run_id_and_running_status(self, client, mock_service):
        mock_service.trigger_run.return_value = {"run_id": "abc-123", "status": "running"}

        response = client.post("/api/admin/batches/trends_batch/run")

        assert response.status_code == 200
        assert response.json() == {"run_id": "abc-123", "status": "running"}
        mock_service.trigger_run.assert_awaited_once_with("trends_batch")

    def test_propagates_service_http_exceptions(self, client, mock_service):
        from fastapi import HTTPException
        mock_service.trigger_run.side_effect = HTTPException(status_code=409, detail="already running")

        response = client.post("/api/admin/batches/trends_batch/run")

        assert response.status_code == 409


class TestGetRun:
    def test_returns_run_status(self, client, mock_service):
        from datetime import datetime, timezone
        run_id = "123e4567-e89b-12d3-a456-426614174000"
        mock_service.get_run.return_value = {
            "run_id": run_id, "batch_name": "trends_batch", "trigger": "manual",
            "status": "running", "created_at": datetime.now(timezone.utc),
            "completed_at": None, "error": None, "progress": None,
        }

        response = client.get(f"/api/admin/batches/runs/{run_id}")

        assert response.status_code == 200
        assert response.json()["status"] == "running"

    def test_get_run_returns_progress(self, client, mock_service):
        from datetime import datetime, timezone
        run_id = "123e4567-e89b-12d3-a456-426614174000"
        progress = {
            "label": "images", "unit": "images", "done": 5, "total": 10, "rate_per_min": 2.5,
            "step": {"index": 1, "count": 3, "label": "embed"},
            "updated_at": datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc),
        }
        mock_service.get_run.return_value = {
            "run_id": run_id, "batch_name": "trends_batch", "trigger": "manual",
            "status": "running", "created_at": datetime.now(timezone.utc),
            "completed_at": None, "error": None, "progress": progress,
        }

        body = client.get(f"/api/admin/batches/runs/{run_id}").json()

        assert body["progress"]["done"] == 5
        assert body["progress"]["step"] == {"index": 1, "count": 3, "label": "embed"}
        assert body["progress"]["updated_at"].startswith("2026-11-23T10:00:00")

    def test_not_found(self, client, mock_service):
        from fastapi import HTTPException
        mock_service.get_run.side_effect = HTTPException(status_code=404, detail="not found")

        response = client.get("/api/admin/batches/runs/00000000-0000-0000-0000-000000000000")

        assert response.status_code == 404


class TestListRuns:
    def test_returns_items_and_total(self, client, mock_service):
        mock_service.list_runs.return_value = {"items": [], "total": 0}

        response = client.get("/api/admin/batches/runs?limit=10&offset=0")

        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0}
        mock_service.list_runs.assert_awaited_once_with(limit=10, offset=0, status=None, name=None)

    def test_passes_status_and_name_filters(self, client, mock_service):
        mock_service.list_runs.return_value = {"items": [], "total": 0}

        response = client.get("/api/admin/batches/runs?status=failed&name=describe")

        assert response.status_code == 200
        mock_service.list_runs.assert_awaited_once_with(limit=50, offset=0, status="failed", name="describe")

    def test_unknown_status_is_422(self, client, mock_service):
        response = client.get("/api/admin/batches/runs?status=banana")

        assert response.status_code == 422
        mock_service.list_runs.assert_not_awaited()


    def test_list_runs_progress_null_for_untracked(self, client, mock_service):
        from datetime import datetime, timezone
        mock_service.list_runs.return_value = {"items": [{
            "run_id": "123e4567-e89b-12d3-a456-426614174000", "batch_name": "trends_batch",
            "trigger": "manual", "status": "completed", "created_at": datetime.now(timezone.utc),
            "completed_at": None, "error": None, "progress": None,
        }], "total": 1}

        item = client.get("/api/admin/batches/runs").json()["items"][0]

        assert "progress" in item
        assert item["progress"] is None

    async def test_list_runs_with_good_and_malformed_progress_returns_both(self):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        from Backend.app.services.admin_batch_service import AdminBatchService
        now = datetime.now(timezone.utc)

        def run(progress):
            return MagicMock(run_id="123e4567-e89b-12d3-a456-426614174000", kind="trends",
                             trigger="manual", status="completed", error=None,
                             created_at=now, completed_at=None, progress=progress)

        repo = AsyncMock()
        repo.list_runs.return_value = ([run({"label": "x", "updated_at": now.isoformat()}),
                                        run({"updated_at": "not a date"})], 2)
        registry = MagicMock()
        registry.name_for_kind.return_value = "trends_batch"
        registry.all_names.return_value = []
        svc = AdminBatchService(repo, AsyncMock(), registry)
        out = await svc.list_runs(limit=10, offset=0)

        from Backend.app.types.generated import runlistresponse
        parsed = runlistresponse.Schema.model_validate(out)
        assert len(parsed.items) == 2
        assert parsed.items[0].progress is not None and parsed.items[0].progress.step is None
        assert parsed.items[1].progress is None


def test_status_filter_values_cover_every_run_status_enum_value():
    """The filter accepts exactly the API form of every stored RunStatus, so a new enum value cannot be forgotten."""
    from typing import get_args

    from Backend.app.api import admin
    from Backend.app.services.admin_batch_service import _STATUS_MAP
    from Storage.models import RunStatus

    accepted = set(get_args(get_args(admin.list_runs.__annotations__["status"])[0]))
    assert accepted == {_STATUS_MAP.get(s.value, s.value) for s in RunStatus}
