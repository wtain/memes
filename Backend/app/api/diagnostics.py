from datetime import datetime
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ValidationError

from Storage.db import AsyncSessionLocal, get_async_db
from Backend.app.repositories.diagnostics_repository import DiagnosticsRepository
from Backend.app.services.statistics_snapshot_service import CORPUS_SNAPSHOT, refresh_corpus_snapshot
from repository.statistics_snapshots import StatisticsSnapshotsRepository

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
    diagnostics_repo: DiagnosticsRepository = Depends(get_diagnostics_repo),
    snapshots_repo: StatisticsSnapshotsRepository = Depends(get_statistics_snapshots_repo),
):
    """Serves the precomputed snapshot (see batch/build_statistics.py). Falls back to a live
    compute-and-store when there is no snapshot yet, or the stored payload no longer matches
    the response shape (e.g. written before a stat field was added)."""
    snapshot = await snapshots_repo.get(CORPUS_SNAPSHOT)
    if snapshot is not None:
        try:
            return StatisticsResponse(**snapshot.payload, computed_at=snapshot.computed_at)
        except ValidationError:
            pass  # fall through to a fresh compute, which also overwrites the unusable row
    result = await refresh_corpus_snapshot(diagnostics_repo, snapshots_repo)
    return StatisticsResponse(**result.payload, computed_at=result.computed_at)