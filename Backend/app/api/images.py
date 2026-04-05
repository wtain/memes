from typing import Optional, AsyncGenerator
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.external import get_async_db, AsyncSessionLocal
from app.repositories.image_repository import ImageRepository
from app.services.cache import short_cache_headers, image_cache_headers
from app.services.image_service import ImageService
from app.services.image_store import IMAGES_DIR
from app.types.generated.meme import Schema as Meme
from app.types.generated.memesearchresponse import Schema as MemeSearchResponse

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
    response: Response,
    q: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    facets: Optional[str] = None,
    cursor: Optional[str] = None,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(short_cache_headers(30))
    return await service.search(q=q, raw_facets=facets, cursor=cursor, limit=limit)


@router.get("/{image_id}/similar", response_model=MemeSearchResponse)
async def get_similar_images(
    image_id: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    return await service.get_similar(image_id)


@router.get("/meme/{image_id}", response_model=Meme)
async def get_meme(
    image_id: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    return await service.get_meme(image_id)


@router.get("/{image_id}")
async def get_image(image_id: str, response: Response, db: AsyncSession = Depends(get_async_db)):
    repo = ImageRepository(db)
    filename = await repo.get_filename(image_id)
    if not filename:
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(
        IMAGES_DIR / filename,
        media_type="application/octet-stream",
        filename=image_id,
        headers=image_cache_headers(),
    )
