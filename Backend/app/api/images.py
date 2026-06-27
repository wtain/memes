import logging
import mimetypes
from typing import Optional, AsyncGenerator
from urllib.parse import quote

import unicodedata
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

logger = logging.getLogger(__name__)
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.db import get_async_db, AsyncSessionLocal
from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.cache import short_cache_headers, image_cache_headers, no_cache_headers
from Backend.app.services.image_service import ImageService
from Backend.app.services.image_store import get_image_path, image_exists
from Backend.app.types.generated.meme import Schema as Meme
from Backend.app.types.generated.memesearchresponse import Schema as MemeSearchResponse

router = APIRouter(prefix="/images", tags=["images"])


# dependencies.py
async def get_image_service(
    db: AsyncSessionLocal = Depends(get_async_db)
) -> AsyncGenerator[ImageService, None]:
    repository = ImageRepository(db)
    service = ImageService(repository)
    try:
        yield service
    finally:
        # Optionally do cleanup if needed
        pass
        # The session will be closed by get_async_db's finally block


@router.get("", response_model=MemeSearchResponse)
async def get_images(
    request: Request,
    response: Response,
    q: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    facets: Optional[str] = None,
    cursor: Optional[str] = None,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    ua = request.headers.get("user-agent", "")
    return await service.search(q=q, raw_facets=facets, cursor=cursor, limit=limit, user_agent=ua)


@router.get("/{image_id}/similar", response_model=MemeSearchResponse)
async def get_similar_images(
    image_id: str,
    response: Response,
    limit: int = Query(10, ge=1, le=100),
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_similar(image_id, limit=limit)


@router.get("/meme/{image_id}", response_model=Meme)
async def get_meme(
    image_id: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_meme(image_id)


@router.put("/meme/{image_id}/mark_excluded")
async def mark_excluded(
    image_id: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    await service.mark_excluded(image_id)


@router.put("/meme/{image_id}/unmark_excluded")
async def unmark_excluded(
    image_id: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    await service.unmark_excluded(image_id)

@router.get("/meme/{image_id}/get_excluded", response_model=int)
async def get_excluded(
    image_id: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    is_excluded = await service.get_is_excluded(image_id)
    return 1 if is_excluded else 0


# Must be before /{image_id} endpoint
@router.get("/excluded", response_model=MemeSearchResponse)
async def get_excluded_images(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = None,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_excluded(cursor=cursor, limit=limit)


# Must be before /{image_id} endpoint
@router.get("/untagged", response_model=MemeSearchResponse)
async def get_untagged_images(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = None,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_untagged(cursor=cursor, limit=limit)


# Must be before /{image_id} endpoint
@router.get("/duplicates", response_model=MemeSearchResponse)
async def get_duplicate_images(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    threshold: float = Query(0.05, ge=0.0, le=1.0),
    cursor: Optional[str] = None,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    # return await service.get_duplicates(cursor=cursor, limit=limit, threshold=threshold)
    return await service.get_duplicates_clustered(cursor=cursor, limit=limit, threshold=threshold)


def content_disposition(filename: str) -> str:
    # ASCII fallback: strip non-latin chars for older clients
    ascii_fallback = unicodedata.normalize('NFKD', filename)
    ascii_fallback = ascii_fallback.encode('ascii', 'ignore').decode('ascii')

    # RFC 5987 encoded version for full Unicode support
    encoded = quote(filename, encoding='utf-8')

    return f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"
    # filename_encoded = quote(filename, encoding='utf-8')
    # return f"inline; filename*=UTF-8''{filename_encoded}"


@router.get("/{image_id}")
async def get_image(image_id: str, response: Response, db: AsyncSession = Depends(get_async_db)):
    repo = ImageRepository(db)
    filename = await repo.get_filename(image_id)

    if not filename:
        logger.warning("get_image 404: id=%s not in DB", image_id)
        raise HTTPException(status_code=404, detail="Image not found")

    if not image_exists(filename):
        logger.warning("get_image 404: id=%s filename=%r not on disk", image_id, filename)
        raise HTTPException(status_code=404, detail="Image file missing")

    mime_type, _ = mimetypes.guess_type(filename)

    headers = image_cache_headers()
    cd = content_disposition(filename)
    headers['Content-Disposition'] = cd
    return FileResponse(
        get_image_path(filename),
        # media_type=mime_type,
        headers=headers,
    )
