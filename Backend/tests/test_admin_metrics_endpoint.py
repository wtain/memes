"""Tests for GET /api/admin/batches/metrics (service mocked, like test_admin_batch_endpoints.py)."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.admin import get_business_metrics_service, router as admin_router
from Backend.app.services.business_metrics_service import UnknownProcessError

app = FastAPI()
app.include_router(admin_router, prefix="/api")

PAYLOAD = {
    "process": "recut",
    "items": [{
        "run_id": "b3f1c2a4-0000-0000-0000-000000000001",
        "run_created_at": datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc),
        "counts": {"proposed": 4, "approved": 1, "rejected": 1},
        "metrics": {"approve_rate": 0.5, "decided_share": 0.5, "pending": 2},
    }],
    "unattributed": {"counts": {"proposed": 0, "approved": 0, "rejected": 0},
                     "metrics": {"approve_rate": None, "decided_share": None, "pending": 0}},
}


@pytest.fixture
def mock_service():
    return AsyncMock()


@pytest.fixture
def client(mock_service):
    async def override():
        yield mock_service

    app.dependency_overrides[get_business_metrics_service] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_returns_the_service_payload(client, mock_service):
    mock_service.batch_metrics.return_value = PAYLOAD

    response = client.get("/api/admin/batches/metrics?process=recut")

    assert response.status_code == 200
    body = response.json()
    assert body["process"] == "recut" and body["unattributed"]["counts"]["proposed"] == 0
    assert body["items"][0]["metrics"] == {"approve_rate": 0.5, "decided_share": 0.5, "pending": 2}
    assert body["items"][0]["run_created_at"].startswith("2026-10-18T09:00:00")
    mock_service.batch_metrics.assert_awaited_once_with("recut", limit=50)


def test_passes_the_limit_through(client, mock_service):
    mock_service.batch_metrics.return_value = {**PAYLOAD, "items": []}

    client.get("/api/admin/batches/metrics?process=recut&limit=7")

    mock_service.batch_metrics.assert_awaited_once_with("recut", limit=7)


def test_serialises_null_rates_and_a_present_unattributed_bucket(client, mock_service):
    bucket = {"counts": {"proposed": 2, "approved": 0, "rejected": 0},
              "metrics": {"approve_rate": None, "decided_share": 0.0, "pending": 2}}
    mock_service.batch_metrics.return_value = {**PAYLOAD, "items": [], "unattributed": bucket}

    body = client.get("/api/admin/batches/metrics?process=recut").json()

    assert body["unattributed"] == bucket


def test_unknown_process_is_404(client, mock_service):
    mock_service.batch_metrics.side_effect = UnknownProcessError("nope")

    assert client.get("/api/admin/batches/metrics?process=nope").status_code == 404


@pytest.mark.parametrize("query", ["process=recut&limit=0", "process=recut&limit=201", "limit=10"])
def test_limit_out_of_range_is_422(client, query):
    assert client.get(f"/api/admin/batches/metrics?{query}").status_code == 422
