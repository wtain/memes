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
    def test_returns_clusters_for_tier(self, client, mock_service):
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        mock_service.list_clusters.return_value = [
            {
                "members": [
                    {"image_id": a, "filename": "a.jpg", "status": "pending", "ocr_text": None},
                    {"image_id": b, "filename": "b.jpg", "status": "active", "ocr_text": "existing meme text"},
                ],
                "edges": [
                    {"image_id1": a, "image_id2": b, "distance": 0.02, "match_source": "cross_corpus"},
                ],
            }
        ]

        response = client.get("/api/ingestion/clusters/tier_a")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert len(data[0]["members"]) == 2
        assert data[0]["edges"][0]["match_source"] == "cross_corpus"
        mock_service.list_clusters.assert_awaited_once_with("tier_a")

    def test_rejects_unknown_tier(self, client, mock_service):
        response = client.get("/api/ingestion/clusters/tier_z")

        assert response.status_code == 422


class TestResolveCluster:
    def test_applies_reject_and_keep_decisions(self, client, mock_service):
        reject_id = str(uuid.uuid4())
        keep_id = str(uuid.uuid4())
        mock_service.resolve.return_value = {"rejected": [reject_id], "kept": [keep_id]}

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
