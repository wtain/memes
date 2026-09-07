"""
Tests for the ingestion endpoints (Tier A/B review — see
docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md).
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from Backend.app.api.ingestion import router as ingestion_router

app = FastAPI()
app.include_router(ingestion_router, prefix="/api")


@pytest.fixture
def mock_service():
    return AsyncMock()


@pytest.fixture
def client(mock_service):
    async def override_get_ingestion_service():
        yield mock_service

    from Backend.app.api.ingestion import get_ingestion_service
    app.dependency_overrides[get_ingestion_service] = override_get_ingestion_service

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


class TestGetRunStatus:
    def test_returns_run_status(self, client, mock_service):
        mock_service.get_run_status.return_value = {
            "run_id": str(uuid.uuid4()), "status": "started", "stage": "tier_a_review",
            "stats": {"intake": 3}, "created_at": datetime.now(timezone.utc), "completed_at": None,
        }

        response = client.get("/api/ingestion/run")

        assert response.status_code == 200
        assert response.json()["status"] == "started"
        assert response.json()["stage"] == "tier_a_review"


class TestListPending:
    def test_returns_pending_images(self, client, mock_service):
        image_id = str(uuid.uuid4())
        mock_service.list_pending.return_value = [
            {"image_id": image_id, "filename": "new.jpg", "created_at": datetime.now(timezone.utc)},
        ]

        response = client.get("/api/ingestion/pending")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["image_id"] == image_id
        assert data[0]["filename"] == "new.jpg"


class TestListClusters:
    def test_returns_cluster_page_for_tier(self, client, mock_service):
        mock_service.list_clusters.return_value = {
            "items": [{
                "members": [
                    {"image_id": "11111111-1111-1111-1111-111111111111", "filename": "a.jpg",
                     "status": "pending", "ocr_text": "Не смешно"},
                ],
                "edges": [],
            }],
            "next_cursor": "0.05|11111111-1111-1111-1111-111111111111",
            "has_next": True,
        }
        response = client.get("/api/ingestion/clusters/tier_a")
        assert response.status_code == 200
        body = response.json()
        assert body["has_next"] is True
        assert body["next_cursor"] == "0.05|11111111-1111-1111-1111-111111111111"
        assert body["items"][0]["members"][0]["ocr_text"] == "Не смешно"
        mock_service.list_clusters.assert_awaited_once_with("tier_a", cursor=None, limit=40)

    def test_forwards_cursor_and_limit(self, client, mock_service):
        mock_service.list_clusters.return_value = {"items": [], "next_cursor": None, "has_next": False}
        response = client.get("/api/ingestion/clusters/tier_b?cursor=0.1%7Cabc&limit=10")
        assert response.status_code == 200
        mock_service.list_clusters.assert_awaited_once_with("tier_b", cursor="0.1|abc", limit=10)

    def test_rejects_out_of_range_limit(self, client, mock_service):
        assert client.get("/api/ingestion/clusters/tier_a?limit=0").status_code == 422
        assert client.get("/api/ingestion/clusters/tier_a?limit=999").status_code == 422

    def test_rejects_unknown_tier(self, client, mock_service):
        assert client.get("/api/ingestion/clusters/tier_z").status_code == 422


class TestResolveCluster:
    def test_applies_reject_and_keep_decisions(self, client, mock_service):
        reject_id = str(uuid.uuid4())
        keep_id = str(uuid.uuid4())
        mock_service.resolve.return_value = {
            "rejected": [reject_id], "kept": [keep_id], "failed": [], "move_failed": [],
        }

        response = client.post(
            "/api/ingestion/clusters/tier_a/resolve",
            json={"decisions": [
                {"image_id": reject_id, "decision": "reject"},
                {"image_id": keep_id, "decision": "keep"},
            ]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["rejected"] == [reject_id]
        assert data["kept"] == [keep_id]
        assert data["failed"] == []
        assert data["move_failed"] == []

    def test_returns_failed_and_move_failed_entries(self, client, mock_service):
        failed_id = str(uuid.uuid4())
        move_failed_id = str(uuid.uuid4())
        mock_service.resolve.return_value = {
            "rejected": [move_failed_id],
            "kept": [],
            "failed": [{"image_id": failed_id, "decision": "keep", "error": "db down"}],
            "move_failed": [{"image_id": move_failed_id, "error": "file locked"}],
        }

        response = client.post(
            "/api/ingestion/clusters/tier_a/resolve",
            json={"decisions": [
                {"image_id": failed_id, "decision": "keep"},
                {"image_id": move_failed_id, "decision": "reject"},
            ]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["failed"] == [{"image_id": failed_id, "decision": "keep", "error": "db down"}]
        assert data["move_failed"] == [{"image_id": move_failed_id, "error": "file locked"}]

    def test_rejects_unknown_decision_value(self, client, mock_service):
        response = client.post(
            "/api/ingestion/clusters/tier_a/resolve",
            json={"decisions": [{"image_id": str(uuid.uuid4()), "decision": "maybe"}]},
        )

        assert response.status_code == 422  # pydantic Literal validation, service never called
        mock_service.resolve.assert_not_awaited()


class TestUndoReject:
    def test_reverts_to_pending(self, client, mock_service):
        image_id = str(uuid.uuid4())
        mock_service.undo_reject.return_value = {"image_id": image_id, "status": "pending"}

        response = client.post(f"/api/ingestion/images/{image_id}/undo-reject")

        assert response.status_code == 200
        assert response.json()["status"] == "pending"
