from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from Storage.db import AsyncSessionLocal, get_async_db
from Backend.app.repositories.diagnostics_repository import DiagnosticsRepository

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


class HealthResponse(BaseModel):
    backend: bool
    database: bool


class MemeStats(BaseModel):
    total: int
    with_embeddings: int
    with_ocr: int
    with_tags: int
    without_tags: int
    with_descriptions: int
    with_concept_tags: int
    excluded: int


class ContentStats(BaseModel):
    ocr_texts: int
    tags: int
    tag_keys: int
    tag_values: int
    concepts: int
    concept_image_sets: int
    concept_images: int


class TrendsStats(BaseModel):
    runs: int
    feed_sources: int


class StatisticsResponse(BaseModel):
    memes: MemeStats
    content: ContentStats
    trends: TrendsStats


async def get_diagnostics_repo(
    db: AsyncSessionLocal = Depends(get_async_db),
) -> AsyncGenerator[DiagnosticsRepository, None]:
    try:
        yield DiagnosticsRepository(db)
    finally:
        pass


@router.get("/health", response_model=HealthResponse)
async def health(repo: DiagnosticsRepository = Depends(get_diagnostics_repo)):
    db_ok = await repo.check_database()
    return HealthResponse(backend=True, database=db_ok)


@router.get("/statistics", response_model=StatisticsResponse)
async def statistics(repo: DiagnosticsRepository = Depends(get_diagnostics_repo)):
    row = await repo.get_statistics()
    return StatisticsResponse(
        memes=MemeStats(
            total=row.total_memes,
            with_embeddings=row.with_embeddings,
            with_ocr=row.with_ocr,
            with_tags=row.with_tags,
            without_tags=row.without_tags,
            with_descriptions=row.with_descriptions,
            with_concept_tags=row.with_concept_tags,
            excluded=row.excluded,
        ),
        content=ContentStats(
            ocr_texts=row.ocr_texts,
            tags=row.tags,
            tag_keys=row.tag_keys,
            tag_values=row.tag_values,
            concepts=row.concepts,
            concept_image_sets=row.concept_image_sets,
            concept_images=row.concept_images,
        ),
        trends=TrendsStats(
            runs=row.trends_runs,
            feed_sources=row.feed_sources,
        ),
    )