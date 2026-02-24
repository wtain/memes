from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from typing import List, Optional
from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.services.cache import short_cache_headers

from app.services.cache import image_cache_headers

from app.models.external import ImageMetrics, __all__, Image, OCRText, SessionLocal, AsyncSessionLocal, init_db

from app.models.image import ImageSummary, ImageDetail
from app.services.image_store import (
    list_images,
    image_exists,
    get_image_path, IMAGES_DIR,
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
        # query = (
        #     select(
        #         # img, ocr,
        #         img.id, img.filename, ocr.text, ocr.confidence
        #     ).join(ocr, ocr.image_id == img.id)
        # )
        query = (
            select(
                # img, ocr,
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

        # print([t for t in result_texts])

        # print(index)

        for t in result_texts:
            index[str(t.image_id)].text.append(t.text)

        response_memes = MemeSearchResponse(items=items, nextCursor=str(items[-1].id), )
        return response_memes
        # return session.query(Image.id, Image.filename).all()

    # response.headers.update(short_cache_headers(30))
    # """
    # List images (dummy pagination for now).
    # Cursor is an image_id; everything after it is returned.
    # """
    # all_images = sorted(list_images())
    #
    # start_index = 0
    # if cursor and cursor in all_images:
    #     start_index = all_images.index(cursor) + 1
    #
    # selected = all_images[start_index:start_index + limit]
    #
    # return [
    #     Meme(
    #         id=image_id,
    #         image_url=f"/api/images/{image_id}/preview",
    #     )
    #     # ImageSummary(
    #     #     id=image_id,
    #     #     preview_url=f"/api/images/{image_id}/preview",
    #     # )
    #     for image_id in selected
    # ]


@router.get("/{image_id}") # , response_model=ImageDetail
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

    # if not image_exists(image_id):
    #     raise HTTPException(status_code=404, detail="Image not found")

    # path = get_image_path(image_id)
    path = IMAGES_DIR / images[0].filename

    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=image_id,
        headers=image_cache_headers(),
    )
    # response.headers.update(short_cache_headers(30))
    # if not image_exists(image_id):
    #     raise HTTPException(status_code=404, detail="Image not found")
    #
    # # Dummy metadata for now
    # return ImageDetail(
    #     id=image_id,
    #     content_url=f"/api/images/{image_id}/content",
    #     preview_url=f"/api/images/{image_id}/preview",
    #     categories=["placeholder"],
    #     ocr_text=None,
    # )


# @router.get("/{image_id}/content")
# def get_image_content(
#     image_id: str,
#     w: Optional[int] = Query(None, gt=0),
#     h: Optional[int] = Query(None, gt=0),
#     format: Optional[str] = None,
# ):
#     if not image_exists(image_id):
#         raise HTTPException(status_code=404, detail="Image not found")
#
#     path = get_image_path(image_id)
#
#     return FileResponse(
#         path,
#         media_type="application/octet-stream",
#         filename=image_id,
#         headers=image_cache_headers(),
#     )
#
#
# @router.get("/{image_id}/preview")
# def get_image_preview(image_id: str):
#     if not image_exists(image_id):
#         raise HTTPException(status_code=404, detail="Image not found")
#
#     path = get_image_path(image_id)
#
#     return FileResponse(
#         path,
#         media_type="application/octet-stream",
#         filename=image_id,
#         headers=image_cache_headers(),
#     )

