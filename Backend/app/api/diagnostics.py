import logging
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ValidationError

from Storage.db import AsyncSessionLocal, get_async_db
from Backend.app.repositories.diagnostics_repository import DiagnosticsRepository
from Backend.app.services.statistics_snapshot_service import (
    CORPUS_SNAPSHOT,
    SNAPSHOT_MAX_AGE,
    refresh_corpus_snapshot,
)
from repository.statistics_snapshots import StatisticsSnapshotsRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


class HealthResponse(BaseModel):
    backend: bool
    database: bool


class MemeStats(BaseModel):
    total: int
    pending: int
    rejected: int
    with_embeddings: int
    with_ocr: int
    with_tags: int
    without_tags: int
    with_descriptions: int
    with_concept_tags: int
    flagged: int
    duplicate_clusters: int
    ocr_missing_text_heavy_classification: int
    text_heavy_missing_embeddings: int
    embeddings_missing_lemmas: int


class ContentStats(BaseModel):
    ocr_texts: int
    tags: int
    tag_keys: int
    tag_values: int
    concepts: int
    concept_image_sets: int
    concept_images: int
    descriptions_approved: int
    descriptions_rejected: int
    descriptions_feedback_total: int


class TrendsStats(BaseModel):
    runs: int
    trend_sources: int


class StatisticsResponse(BaseModel):
    memes: MemeStats
    content: ContentStats
    trends: TrendsStats
    computed_at: datetime | None = None


async def get_diagnostics_repo(
    db: AsyncSessionLocal = Depends(get_async_db),
) -> AsyncGenerator[DiagnosticsRepository, None]:
    try:
        yield DiagnosticsRepository(db)
    finally:
        pass


async def get_statistics_snapshots_repo(
    db: AsyncSessionLocal = Depends(get_async_db),
) -> AsyncGenerator[StatisticsSnapshotsRepository, None]:
    yield StatisticsSnapshotsRepository(db)


@router.get("/health", response_model=HealthResponse)
async def health(repo: DiagnosticsRepository = Depends(get_diagnostics_repo)):
    db_ok = await repo.check_database()
    return HealthResponse(backend=True, database=db_ok)


@router.get("/statistics", response_model=StatisticsResponse)
async def statistics(
    live: bool = Query(
        False,
        description="Compute on the fly instead of serving the stored snapshot; the result is "
                    "also stored as the new snapshot.",
    ),
    diagnostics_repo: DiagnosticsRepository = Depends(get_diagnostics_repo),
    snapshots_repo: StatisticsSnapshotsRepository = Depends(get_statistics_snapshots_repo),
):
    """Serves the precomputed snapshot (see batch/build_statistics.py). Falls back to a live
    compute-and-store when there is no snapshot yet, the snapshot is older than
    SNAPSHOT_MAX_AGE (so a deployment without a running scheduler stays bounded), or the
    stored payload no longer matches the response shape (e.g. written before a stat field
    was added).

    live=true skips the snapshot entirely: it computes on the fly, stores the result as the
    new snapshot (write-through, so every live request also refreshes what plain requests
    serve) and returns it. The statistics page uses this to replace the instantly-shown
    snapshot with current numbers."""
    if not live:
        snapshot = await snapshots_repo.get(CORPUS_SNAPSHOT)
        if snapshot is not None and datetime.now(timezone.utc) - snapshot.computed_at <= SNAPSHOT_MAX_AGE:
            try:
                return StatisticsResponse(**snapshot.payload, computed_at=snapshot.computed_at)
            except ValidationError:
                # fall through to a fresh compute, which also overwrites the unusable row
                logger.warning(
                    "Stored statistics snapshot no longer matches the response shape; recomputed live"
                )
    result = await refresh_corpus_snapshot(diagnostics_repo, snapshots_repo)
    return StatisticsResponse(**result.payload, computed_at=result.computed_at)