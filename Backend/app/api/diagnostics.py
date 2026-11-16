import logging
import os
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import Depends, Query
from Backend.app.stage_policy import READER, make_router, verdict_for
from Backend.app.types.generated.capabilitiesfeatures import Schema as CapabilitiesFeatures
from Backend.app.types.generated.capabilitiesresponse import Schema as CapabilitiesResponse
from config.settings import settings
from config.stage import is_prod
from parse.config import load_config
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

router = make_router(prefix="/diagnostics", tags=["diagnostics"])


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


class ComicsStats(BaseModel):
    draft: int
    built: int
    panelsKept: int
    panelsRetired: int
    builtUnprocessed: int


class StatisticsResponse(BaseModel):
    memes: MemeStats
    content: ContentStats
    trends: TrendsStats
    comics: ComicsStats
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


# Feature flag -> the (method, full path) routes that have to be served for the feature to exist. On dev every route
# is served; on prod a feature is on only if the policy makes all of its routes readers.
_FEATURE_ROUTES: dict[str, tuple[tuple[str, str], ...]] = {
    "concepts": (("GET", "/api/concepts"),),
    "trends": (("GET", "/api/trends/history"),),
    "comics": (("GET", "/api/comics"),),
    "ratings": (("GET", "/api/meme-from-text/ratings"),),
    "statistics": (("GET", "/api/diagnostics/statistics"),),
    "uploads": (("POST", "/api/uploads"),),
    "history": (("GET", "/api/history"),),
    "admin": (("GET", "/api/admin/batches/runs"),),
}


def _feature_served(routes: tuple[tuple[str, str], ...]) -> bool:
    return not is_prod() or all(verdict_for(method, path) == READER for method, path in routes)


def _meme_from_text_available() -> bool:
    """True only when a query parser is configured (parse.model); pure, it does not build or register the parser."""
    try:
        return load_config(settings.get) is not None and _feature_served((("POST", "/api/meme-from-text/search"),))
    except Exception:
        return False


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities():
    """What this backend serves. Public; deliberately no revision, schema head or git SHA (see backend_api.md)."""
    features = {"memeFromText": _meme_from_text_available()}
    features.update({name: _feature_served(routes) for name, routes in _FEATURE_ROUTES.items()})
    return CapabilitiesResponse(
        stage="prod" if is_prod() else "dev",
        environment=os.environ.get("APP_ENV", "general"),
        features=CapabilitiesFeatures(**features),
    )


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