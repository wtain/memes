"""
Tests for Backend/app/services/statistics_snapshot_service.py -- pure mapping and
orchestration; both repositories are mocked.
"""
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Backend.app.services.statistics_snapshot_service import (
    CORPUS_SNAPSHOT,
    refresh_corpus_snapshot,
    statistics_payload,
)


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


class TestStatisticsPayload:
    def test_maps_every_row_field_into_the_nested_api_shape(self):
        payload = statistics_payload(_fake_stats_row())

        assert payload == {
            "memes": {
                "total": 100, "pending": 6, "rejected": 2, "with_embeddings": 90, "with_ocr": 80,
                "with_tags": 70, "without_tags": 30, "with_descriptions": 60, "with_concept_tags": 40,
                "flagged": 5, "duplicate_clusters": 3,
                "ocr_missing_text_heavy_classification": 7, "text_heavy_missing_embeddings": 4,
                "embeddings_missing_lemmas": 2,
            },
            "content": {
                "ocr_texts": 200, "tags": 300, "tag_keys": 8, "tag_values": 90, "concepts": 10,
                "concept_image_sets": 12, "concept_images": 150,
                "descriptions_approved": 21, "descriptions_rejected": 3, "descriptions_feedback_total": 24,
            },
            "trends": {"runs": 4, "trend_sources": 2},
        }


class TestRefreshCorpusSnapshot:
    @pytest.mark.asyncio
    async def test_computes_upserts_and_returns_the_same_payload(self):
        diagnostics_repo = AsyncMock()
        diagnostics_repo.get_statistics.return_value = _fake_stats_row()
        snapshots_repo = AsyncMock()

        result = await refresh_corpus_snapshot(diagnostics_repo, snapshots_repo)

        assert result.payload == statistics_payload(_fake_stats_row())
        assert result.computed_at.tzinfo is not None
        assert result.computed_at.utcoffset() == timezone.utc.utcoffset(None)
        snapshots_repo.upsert.assert_awaited_once()
        args = snapshots_repo.upsert.await_args.args
        assert args[0] == CORPUS_SNAPSHOT
        assert args[1] == result.payload
        assert args[2] == result.computed_at
        assert isinstance(args[3], int) and args[3] >= 0

    @pytest.mark.asyncio
    async def test_compute_failure_propagates_and_writes_nothing(self):
        diagnostics_repo = AsyncMock()
        diagnostics_repo.get_statistics.side_effect = RuntimeError("db down")
        snapshots_repo = AsyncMock()

        with pytest.raises(RuntimeError, match="db down"):
            await refresh_corpus_snapshot(diagnostics_repo, snapshots_repo)

        snapshots_repo.upsert.assert_not_awaited()
