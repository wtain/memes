import logging
import random
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response

from Storage.db import get_async_db, AsyncSessionLocal
from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.repositories.recommendations_repository import RecommendationsRepository
from Backend.app.services.cache import no_cache_headers
from Backend.app.services.recommendations_service import RecommendationsService
from Backend.app.types.generated.memesearchresponse import Schema as MemeSearchResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


async def get_recommendations_service(
    db: AsyncSessionLocal = Depends(get_async_db),
):
    repo = RecommendationsRepository(db)
    image_repo = ImageRepository(db)
    try:
        yield RecommendationsService(repo, image_repo)
    finally:
        pass


@router.get("", response_model=MemeSearchResponse)
async def get_recommendations(
    response: Response,
    q: Optional[str] = None,
    seed: Optional[int] = None,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = None,
    service: RecommendationsService = Depends(get_recommendations_service),
):
    response.headers.update(no_cache_headers())

    if cursor:
        cursor_seed, last_hash = RecommendationsService.decode_cursor(cursor)
        if seed is not None and seed != cursor_seed:
            logger.warning(
                "seed mismatch: query param seed=%d ignored, using cursor seed=%d",
                seed, cursor_seed,
            )
        effective_seed = cursor_seed
    else:
        effective_seed = seed if seed is not None else random.randint(0, 2**31 - 1)
        last_hash = None

    return await service.get_recommendations(
        q=q if q else None,
        seed=effective_seed,
        last_hash=last_hash,
        limit=limit,
    )