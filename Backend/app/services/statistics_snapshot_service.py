"""Builds and stores the precomputed statistics snapshot.

Single implementation shared by batch/build_statistics.py, the run_wrapper post-batch
hook, and the /api/diagnostics/statistics fallback path, so all three produce an identical
payload. See docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md.
"""
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

CORPUS_SNAPSHOT = "corpus"
# Twice the hourly refresh interval (environments/settings.yaml, build_statistics job): where the
# scheduler runs, a snapshot is never this old; where it doesn't (e.g. the Docker image),
# the read path recomputes instead of serving a frozen row forever.
SNAPSHOT_MAX_AGE = timedelta(hours=2)


@dataclass(frozen=True)
class StatisticsSnapshotResult:
    payload: dict
    computed_at: datetime


def statistics_payload(row) -> dict:
    """Maps DiagnosticsRepository.get_statistics()'s flat row into the nested
    StatisticsResponse shape (plain dict, JSON-serializable)."""
    return {
        "memes": {
            "total": row.total_memes,
            "pending": row.pending,
            "rejected": row.rejected,
            "with_embeddings": row.with_embeddings,
            "with_ocr": row.with_ocr,
            "with_tags": row.with_tags,
            "without_tags": row.without_tags,
            "with_descriptions": row.with_descriptions,
            "with_concept_tags": row.with_concept_tags,
            "flagged": row.flagged,
            "duplicate_clusters": row.duplicate_clusters,
            "ocr_missing_text_heavy_classification": row.ocr_missing_text_heavy_classification,
            "text_heavy_missing_embeddings": row.text_heavy_missing_embeddings,
            "embeddings_missing_lemmas": row.embeddings_missing_lemmas,
        },
        "content": {
            "ocr_texts": row.ocr_texts,
            "tags": row.tags,
            "tag_keys": row.tag_keys,
            "tag_values": row.tag_values,
            "concepts": row.concepts,
            "concept_image_sets": row.concept_image_sets,
            "concept_images": row.concept_images,
            "descriptions_approved": row.descriptions_approved,
            "descriptions_rejected": row.descriptions_rejected,
            "descriptions_feedback_total": row.descriptions_feedback_total,
        },
        "trends": {
            "runs": row.trends_runs,
            "trend_sources": row.trend_sources,
        },
    }


async def refresh_corpus_snapshot(diagnostics_repo, snapshots_repo) -> StatisticsSnapshotResult:
    """Runs the (slow) statistics query, upserts the 'corpus' snapshot, and returns what was
    stored. Does not commit -- the caller owns the session/commit. If the query raises,
    nothing is written and the previous snapshot stays in place."""
    started = time.perf_counter()
    row = await diagnostics_repo.get_statistics()
    duration_ms = round((time.perf_counter() - started) * 1000)
    payload = statistics_payload(row)
    computed_at = datetime.now(timezone.utc)
    await snapshots_repo.upsert(CORPUS_SNAPSHOT, payload, computed_at, duration_ms)
    return StatisticsSnapshotResult(payload=payload, computed_at=computed_at)
