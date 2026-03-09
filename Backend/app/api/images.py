import base64
import json
import uuid
from datetime import datetime

import sqlalchemy
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from typing import Optional, List
from fastapi import Response
from sqlalchemy import select, tuple_, distinct
from sqlalchemy.orm import aliased

from app.services.cache import short_cache_headers

from app.services.cache import image_cache_headers

from app.models.external import Image, OCRText, Embedding, ImageTag, AsyncSessionLocal

from app.services.image_store import (
    IMAGES_DIR,
)
from app.types.generated.meme import Schema as Meme
from app.types.generated.memetag import Schema as MemeTag
from app.types.generated.memesearchresponse import Schema as MemeSearchResponse

router = APIRouter(prefix="/images", tags=["images"])

def b64_encode(sample_string):
    sample_string_bytes = sample_string.encode("ascii")

    base64_bytes = base64.b64encode(sample_string_bytes)
    return base64_bytes.decode("ascii")


def b64_decode(base64_string):
    base64_bytes = base64_string.encode("ascii")

    sample_string_bytes = base64.b64decode(base64_bytes)
    return sample_string_bytes.decode("ascii")

# todo: structure for query
@router.get("", response_model=MemeSearchResponse)
async def get_images(
    response: Response,
    q: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    # tags: List[MemeTag] = Query(),
    cursor: Optional[str] = None,
):
    img = aliased(Image)
    ocr = aliased(OCRText)
    embed = aliased(Embedding)
    image_tag = aliased(ImageTag)
    async with AsyncSessionLocal() as session:

        query_filter = None
        if q:
            query_filter = (
                select(
                    distinct(img.id)
                )
                .join(ocr, ocr.image_id == img.id)
                .where(
                    # sqlalchemy.func.upper(q).in_(sqlalchemy.func.upper(ocr.text))
                    sqlalchemy.func.upper(ocr.text).contains(str.upper(q))
                )
            )

        query = (
            select(
                img.id, img.filename, img.created_at,
                # ocr.text, ocr.confidence
            )
            # .join(ocr, ocr.image_id == img.id)
        )
        if cursor:
            cursor_obj = json.loads(b64_decode(cursor))
            cursor_created_at = datetime.fromisoformat(cursor_obj["created_at"])
            cursor_id = uuid.UUID(cursor_obj["id"])
            query = query.where(tuple_(img.created_at, img.id) < tuple_(cursor_created_at, cursor_id))

        if query_filter is not None:
            ids_results = await session.execute(query_filter)
            ids = [id for (id,) in ids_results.all()]
            query = query.where(img.id.in_(ids))

        query = (
            query
            .order_by(
                img.created_at.desc(),
                img.id.desc()
            )
            .limit(limit+1)
        )
        result = await session.execute(query)
        results = result.all()

        next_cursor_string = None
        if len(results) > 0:
            last_result = results[-1]
            next_cursor = json.dumps({
                "id": str(last_result.id),
                "created_at": last_result.created_at.isoformat()
            })
            next_cursor_string = base64.urlsafe_b64encode(next_cursor.encode()).decode()

        response.headers.update(short_cache_headers(30))
        items = [Meme(id=str(r.id), imageUrl=f"/api/images/{str(r.id)}", text=[], tags=[],) for r in results]

        ids = set(map(lambda meme: meme.id, items))

        index = {meme.id: meme for meme in items}

        query_texts = (
            select(
                ocr.image_id, ocr.text, ocr.confidence
            )
            .where(
                (ocr.image_id.in_(ids)) &
                (ocr.confidence > 0.8)
            )
        )

        result_texts = await session.execute(query_texts)

        # todo: change response structure to incorporate confidence and move this logic to frontend
        for t in result_texts:
            index[str(t.image_id)].text.append(f"{t.text} ({t.confidence})")

        # query_embeddings = (
        #     select(
        #         embed.image_id, embed.text, embed.confidence
        #     )
        #     .where(embed.image_id.in_(ids))
        # )
        #
        # result_embeddings = await session.execute(query_embeddings)
        #
        # for t in result_embeddings:
        #     index[str(t.image_id)].tags.append(MemeTag(name=t.text, category="OCR", score=t.confidence))

        query_tags = (
            select(
                image_tag.image_id, image_tag.key, image_tag.value, image_tag.source
            )
            .where(image_tag.image_id.in_(ids))
        )

        result_tags = await session.execute(query_tags)

        for (image_id, key, value, source) in result_tags:
            index[str(image_id)].tags.append(MemeTag(name=value, category=key, score=1))

        has_next = len(items) > limit

        if has_next:
            items = items[:limit]

        # todo: has_next
        # limit+1?
        # frontend: what happens on failure? how can we retry later?
        response_memes = MemeSearchResponse(items=items,
                                            nextCursor=next_cursor_string,
                                            hasNext=has_next, )
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

