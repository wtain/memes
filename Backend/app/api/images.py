from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from typing import Optional
from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.services.cache import short_cache_headers

from app.services.cache import image_cache_headers

from app.models.external import Image, OCRText, AsyncSessionLocal

from app.services.image_store import (
    IMAGES_DIR,
)
from app.types.generated.meme import Schema as Meme
from app.types.generated.memesearchresponse import Schema as MemeSearchResponse

router = APIRouter(prefix="/images", tags=["images"])


@router.get("", response_model=MemeSearchResponse)
async def get_images(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = None,
):

    img = aliased(Image)
    ocr = aliased(OCRText)
    async with AsyncSessionLocal() as session:
        query = (
            select(
                img.id, img.filename
            )
        )
        if cursor:
            query = query.where(img.id > cursor)
        query = query.limit(limit).order_by(img.id)
        result = await session.execute(query)

        response.headers.update(short_cache_headers(30))
        items = [Meme(id=str(r.id), imageUrl=f"/api/images/{str(r.id)}", text=[],) for r in result]

        ids = set(map(lambda meme: meme.id, items))

        index = {meme.id: meme for meme in items}

        query_texts = (
            select(
                ocr.image_id, ocr.text, ocr.confidence
            )
            .where(ocr.image_id.in_(ids))
        )

        result_texts = await session.execute(query_texts)

        for t in result_texts:
            index[str(t.image_id)].text.append(t.text)

        response_memes = MemeSearchResponse(items=items, nextCursor=str(items[-1].id), )
        return response_memes


@router.get("/{image_id}")
async def get_image(image_id: str, response: Response):

    async with AsyncSessionLocal() as session:
        query = (
            select(Image.filename)
                .where(Image.id == image_id)
        )
        result = await session.execute(query)
        images = [r for r in result]
        if len(images) == 0:
            raise HTTPException(status_code=404, detail="Image not found")

    path = IMAGES_DIR / images[0].filename

    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=image_id,
        headers=image_cache_headers(),
    )

