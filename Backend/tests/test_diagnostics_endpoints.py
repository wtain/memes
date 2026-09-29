"""
Tests for the diagnostics endpoints.
Endpoints tested:
- health
- statistics (including the description-feedback counts)
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from Backend.app.api.diagnostics import router as diagnostics_router
from Backend.app.services.statistics_snapshot_service import statistics_payload

app = FastAPI()
app.include_router(diagnostics_router, prefix="/api")


@pytest.fixture
def mock_diagnostics_repo():
    return AsyncMock()


@pytest.fixture
def mock_snapshots_repo():
    repo = AsyncMock()
    repo.get.return_value = None
    return repo


@pytest.fixture
def client(mock_diagnostics_repo, mock_snapshots_repo):
    async def override_get_diagnostics_repo():
        yield mock_diagnostics_repo

    async def override_get_snapshots_repo():
        yield mock_snapshots_repo

    from Backend.app.api.diagnostics import get_diagnostics_repo, get_statistics_snapshots_repo
    app.dependency_overrides[get_diagnostics_repo] = override_get_diagnostics_repo
    app.dependency_overrides[get_statistics_snapshots_repo] = override_get_snapshots_repo

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
        ocr_missing_text_heavy_classification=7, text_heavy_missing_embeddings=4, embeddings_missing_lemmas=2,
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

    def test_statistics_includes_coverage_gap_counts(self, client, mock_diagnostics_repo):
        mock_diagnostics_repo.get_statistics.return_value = _fake_stats_row(
            ocr_missing_text_heavy_classification=7, text_heavy_missing_embeddings=4, embeddings_missing_lemmas=2,
        )

        response = client.get("/api/diagnostics/statistics")

        assert response.status_code == 200
        data = response.json()
        assert data["memes"]["ocr_missing_text_heavy_classification"] == 7
        assert data["memes"]["text_heavy_missing_embeddings"] == 4
        assert data["memes"]["embeddings_missing_lemmas"] == 2

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

    def test_serves_stored_snapshot_without_recomputing(self, client, mock_diagnostics_repo, mock_snapshots_repo):
        mock_snapshots_repo.get.return_value = SimpleNamespace(
            payload=statistics_payload(_fake_stats_row(total_memes=555)),
            computed_at=datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc),
        )

        response = client.get("/api/diagnostics/statistics")

        assert response.status_code == 200
        data = response.json()
        assert data["memes"]["total"] == 555
        assert data["computed_at"].startswith("2026-09-29T12:00:00")
        mock_diagnostics_repo.get_statistics.assert_not_awaited()
        mock_snapshots_repo.upsert.assert_not_awaited()

    def test_without_snapshot_computes_live_stores_it_and_reports_computed_at(
        self, client, mock_diagnostics_repo, mock_snapshots_repo,
    ):
        mock_diagnostics_repo.get_statistics.return_value = _fake_stats_row(total_memes=321)

        response = client.get("/api/diagnostics/statistics")

        assert response.status_code == 200
        data = response.json()
        assert data["memes"]["total"] == 321
        assert data["computed_at"] is not None
        mock_snapshots_repo.upsert.assert_awaited_once()

    def test_unusable_stored_payload_is_recomputed_instead_of_failing(
        self, client, mock_diagnostics_repo, mock_snapshots_repo,
    ):
        # e.g. an older snapshot written before a new stat field existed
        mock_snapshots_repo.get.return_value = SimpleNamespace(
            payload={"memes": {"total": 1}},
            computed_at=datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc),
        )
        mock_diagnostics_repo.get_statistics.return_value = _fake_stats_row(total_memes=777)

        response = client.get("/api/diagnostics/statistics")

        assert response.status_code == 200
        assert response.json()["memes"]["total"] == 777
        mock_snapshots_repo.upsert.assert_awaited_once()
