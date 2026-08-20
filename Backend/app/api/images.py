import logging
import mimetypes
import uuid
from datetime import datetime
from typing import Optional, AsyncGenerator, Literal
from urllib.parse import quote

import unicodedata
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.db import get_async_db, AsyncSessionLocal
from Backend.app.repositories.duplicate_decisions_repository import DuplicateDecisionsRepository
from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.cache import image_cache_headers, no_cache_headers
from Backend.app.services.image_service import ImageService
from Backend.app.services.image_store import get_image_path, image_exists
from Backend.app.types.generated.meme import Schema as Meme
from Backend.app.types.generated.imagedescription import Schema as ImageDescription
from Backend.app.types.generated.memesearchresponse import Schema as MemeSearchResponse
from Backend.app.types.generated.descriptionfeedbackresponse import Schema as DescriptionFeedbackResponse

router = APIRouter(prefix="/images", tags=["images"])


class DuplicatePairModel(BaseModel):
    image_id1: str
    image_id2: str


class DuplicateDismissResponseModel(BaseModel):
    pairs: list[DuplicatePairModel]


class DuplicateUndoDismissRequest(BaseModel):
    pairs: list[DuplicatePairModel]


class DescriptionNoteRequest(BaseModel):
    text: str


class DuplicateDecisionItemModel(BaseModel):
    image_id1: str
    filename1: str
    image_id2: str
    filename2: str
    decided_at: datetime


class DuplicateDecisionListResponseModel(BaseModel):
    items: list[DuplicateDecisionItemModel]
    total: int


# dependencies.py
async def get_image_service(
    db: AsyncSessionLocal = Depends(get_async_db)
) -> AsyncGenerator[ImageService, None]:
    repository = ImageRepository(db)
    decision_repository = DuplicateDecisionsRepository(db)
    service = ImageService(repository, decision_repository)
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
    source: Literal["image", "description", "description_note"] = "image",
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_similar(image_id, limit=limit, source=source)


@router.get("/{image_id}/descriptions", response_model=list[ImageDescription])
async def get_image_descriptions(
    image_id: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_descriptions(image_id)


@router.put("/{image_id}/descriptions/{prompt_key}/approve", response_model=DescriptionFeedbackResponse)
async def approve_description(
    image_id: str,
    prompt_key: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    feedback = await service.approve_description_feedback(image_id, prompt_key)
    return DescriptionFeedbackResponse(feedback=feedback)


@router.put("/{image_id}/descriptions/{prompt_key}/reject", response_model=DescriptionFeedbackResponse)
async def reject_description(
    image_id: str,
    prompt_key: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    feedback = await service.reject_description_feedback(image_id, prompt_key)
    return DescriptionFeedbackResponse(feedback=feedback)


@router.put("/{image_id}/description-note")
async def set_description_note(
    image_id: str,
    body: DescriptionNoteRequest,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    await service.set_description_note(image_id, body.text)


@router.delete("/{image_id}/description-note")
async def delete_description_note(
    image_id: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    await service.clear_description_note(image_id)


@router.get("/meme/{image_id}", response_model=Meme)
async def get_meme(
    image_id: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_meme(image_id)


@router.put("/meme/{image_id}/mark_flagged")
async def mark_flagged(
    image_id: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    await service.mark_flagged(image_id)


@router.put("/meme/{image_id}/unmark_flagged")
async def unmark_flagged(
    image_id: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    await service.unmark_flagged(image_id)

@router.get("/meme/{image_id}/get_flagged", response_model=int)
async def get_flagged(
    image_id: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    is_flagged = await service.get_is_flagged(image_id)
    return 1 if is_flagged else 0


# Must be before /{image_id} endpoint
@router.get("/flagged", response_model=MemeSearchResponse)
async def get_flagged_images(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = None,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_flagged(cursor=cursor, limit=limit)


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
@router.get("/no-ocr", response_model=MemeSearchResponse)
async def get_no_ocr_images(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = None,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_no_ocr(cursor=cursor, limit=limit)


# Must be before /{image_id} endpoint
@router.get("/duplicates", response_model=MemeSearchResponse)
async def get_duplicate_images(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    threshold: float = Query(0.05, ge=0.0, le=1.0),
    cursor: Optional[str] = None,
    direction: Literal["forward", "backward"] = "forward",
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_duplicates_clustered(cursor=cursor, limit=limit, threshold=threshold, direction=direction)


@router.post("/duplicates/clusters/{cluster_id}/dismiss", response_model=DuplicateDismissResponseModel)
async def dismiss_duplicate_cluster(
    cluster_id: int,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    pairs = await service.dismiss_cluster(cluster_id)
    return DuplicateDismissResponseModel(
        pairs=[DuplicatePairModel(image_id1=str(a), image_id2=str(b)) for a, b in pairs]
    )


@router.post("/duplicates/pairs/undo-dismiss")
async def undo_dismiss_duplicates(
    body: DuplicateUndoDismissRequest,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    pairs = [(uuid.UUID(p.image_id1), uuid.UUID(p.image_id2)) for p in body.pairs]
    await service.undo_dismiss(pairs)


@router.get("/duplicates/decisions", response_model=DuplicateDecisionListResponseModel)
async def get_duplicate_decisions(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    items, total = await service.list_duplicate_decisions(limit=limit, offset=offset)
    return DuplicateDecisionListResponseModel(
        items=[
            DuplicateDecisionItemModel(
                image_id1=str(row[0]), filename1=row[1],
                image_id2=str(row[2]), filename2=row[3],
                decided_at=row[4],
            )
            for row in items
        ],
        total=total,
    )


def content_disposition(filename: str) -> str:
    # ASCII fallback: strip non-latin chars for older clients
    ascii_fallback = unicodedata.normalize('NFKD', filename)
    ascii_fallback = ascii_fallback.encode('ascii', 'ignore').decode('ascii')

    # RFC 5987 encoded version for full Unicode support
    encoded = quote(filename, encoding='utf-8')

    return f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


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
    headers['Access-Control-Allow-Origin'] = '*'
    return FileResponse(
        get_image_path(filename),
        headers=headers,
    )
