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
            "completed_at": None, "error": None,
        }

        response = client.get(f"/api/admin/batches/runs/{run_id}")

        assert response.status_code == 200
        assert response.json()["status"] == "running"

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
        mock_service.list_runs.assert_awaited_once_with(limit=10, offset=0)
