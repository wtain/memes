import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.history_repository import HistoryRepository
from Backend.app.services.meme_text_rating_service import (
    MemeTextRatingService, RatingValidationError, UnknownImageError,
)
from Backend.app.types.generated.ratingrequest import Schema as RatingRequest
from Backend.app.types.generated.ratingresponse import Schema as RatingResponse
from Backend.app.types.generated.ratingsresponse import Schema as RatingsResponse
from config.settings import settings
from repository.meme_text_ratings import MemeTextRatingRepository
from Storage.db import get_async_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meme-from-text", tags=["meme-from-text"])


async def get_rating_service(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[MemeTextRatingService, None]:
    yield MemeTextRatingService(MemeTextRatingRepository(db), int(settings.get("parse.max_input_chars")),
                                HistoryRepository(db))


def _fail(error: Exception) -> HTTPException:
    """Map an error without ever echoing the user's text: fixed details only; unexpected ones log the type."""
    if isinstance(error, RatingValidationError):
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(error, UnknownImageError):
        return HTTPException(status_code=404, detail="image not found")
    logger.warning("rating request failed: %s", type(error).__name__)
    return HTTPException(status_code=500, detail="rating request failed")


@router.put("/ratings", response_model=RatingResponse)
async def put_rating(request: RatingRequest, service: MemeTextRatingService = Depends(get_rating_service)):
    try:
        return await service.put(request)
    except Exception as error:
        raise _fail(error) from None


@router.delete("/ratings", status_code=204)
async def delete_rating(query_hash: str = Query(alias="queryHash"), image_id: str = Query(alias="imageId"),
                        service: MemeTextRatingService = Depends(get_rating_service)):
    try:
        await service.delete(query_hash, image_id)
    except Exception as error:
        raise _fail(error) from None
    return Response(status_code=204)


@router.get("/ratings", response_model=RatingsResponse)
async def list_ratings(query_hash: str = Query(alias="queryHash"),
                       service: MemeTextRatingService = Depends(get_rating_service)):
    try:
        return await service.list(query_hash)
    except Exception as error:
        raise _fail(error) from None


@router.delete("/ratings/text", status_code=204)
async def forget_rating_text(query_hash: str = Query(alias="queryHash"),
                             service: MemeTextRatingService = Depends(get_rating_service)):
    try:
        await service.forget_text(query_hash)
    except Exception as error:
        raise _fail(error) from None
    return Response(status_code=204)
