"""
Tests for the diagnostics endpoints.
Endpoints tested:
- health
- statistics (including the description-feedback counts)
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from Backend.app.api.diagnostics import router as diagnostics_router

app = FastAPI()
app.include_router(diagnostics_router, prefix="/api")


@pytest.fixture
def mock_diagnostics_repo():
    return AsyncMock()


@pytest.fixture
def client(mock_diagnostics_repo):
    async def override_get_diagnostics_repo():
        yield mock_diagnostics_repo

    from Backend.app.api.diagnostics import get_diagnostics_repo
    app.dependency_overrides[get_diagnostics_repo] = override_get_diagnostics_repo

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def _fake_stats_row(**overrides):
    defaults = dict(
        total_memes=100, pending=6, rejected=2, with_embeddings=90, with_ocr=80, with_tags=70,
        without_tags=30, with_descriptions=60, with_concept_tags=40,
        flagged=5, duplicate_clusters=3,
        ocr_texts=200, tags=300, concepts=10, concept_image_sets=12,
        concept_images=150,
        tag_keys=8, tag_values=90,
        trends_runs=4, trend_sources=2,
        descriptions_approved=21, descriptions_rejected=3, descriptions_feedback_total=24,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestStatistics:
    def test_statistics_includes_description_feedback_counts(self, client, mock_diagnostics_repo):
        mock_diagnostics_repo.get_statistics.return_value = _fake_stats_row()

        response = client.get("/api/diagnostics/statistics")

        assert response.status_code == 200
        data = response.json()
        assert data["content"]["descriptions_approved"] == 21
        assert data["content"]["descriptions_rejected"] == 3
        assert data["content"]["descriptions_feedback_total"] == 24

    def test_statistics_includes_pending_and_rejected_image_counts(self, client, mock_diagnostics_repo):
        mock_diagnostics_repo.get_statistics.return_value = _fake_stats_row(pending=6, rejected=2)

        response = client.get("/api/diagnostics/statistics")

        assert response.status_code == 200
        data = response.json()
        assert data["memes"]["pending"] == 6
        assert data["memes"]["rejected"] == 2

    def test_statistics_zero_feedback(self, client, mock_diagnostics_repo):
        mock_diagnostics_repo.get_statistics.return_value = _fake_stats_row(
            descriptions_approved=0, descriptions_rejected=0, descriptions_feedback_total=0,
        )

        response = client.get("/api/diagnostics/statistics")

        assert response.status_code == 200
        data = response.json()
        assert data["content"]["descriptions_approved"] == 0
        assert data["content"]["descriptions_rejected"] == 0
        assert data["content"]["descriptions_feedback_total"] == 0
