import logging
from typing import AsyncGenerator, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.services.meme_profile_service import ProfileValidationError
from Backend.app.services.profile_coverage_service import ProfileCoverageService
from Backend.app.types.generated.profilecoverage import Schema as ProfileCoverage
from Backend.app.types.generated.profilecoverageimages import Schema as ProfileCoverageImages
from config.settings import settings
from repository.batch_runs import BatchRunRepository
from repository.meme_profiles import MemeProfileRepository
from Storage.db import get_async_db
from taxonomy import load as load_taxonomy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/profile-coverage", tags=["admin"])

Category = Literal["without_profile", "needs_review", "stale_taxonomy", "failed", "human_verified", "pending_vector"]


async def get_profile_coverage_service(
        db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[ProfileCoverageService, None]:
    yield ProfileCoverageService(
        MemeProfileRepository(db), BatchRunRepository(db),
        taxonomy_version=load_taxonomy().version,
        embedding_model=str(settings.get("taxonomy.embedding_model")),
    )


def _fail(error: Exception) -> HTTPException:
    if isinstance(error, ProfileValidationError):
        return HTTPException(status_code=422, detail=str(error))
    logger.warning("profile coverage request failed: %s", type(error).__name__)
    return HTTPException(status_code=500, detail="profile coverage request failed")


@router.get("", response_model=ProfileCoverage)
async def get_coverage(service: ProfileCoverageService = Depends(get_profile_coverage_service)):
    try:
        return await service.coverage()
    except Exception as error:
        raise _fail(error) from None


@router.get("/images", response_model=ProfileCoverageImages)
async def get_coverage_images(category: Category, cursor: str | None = None,
                              limit: int = Query(48, ge=1, le=100),
                              service: ProfileCoverageService = Depends(get_profile_coverage_service)):
    try:
        return await service.images(category, cursor, limit)
    except Exception as error:
        raise _fail(error) from None
