from collections import defaultdict
from datetime import datetime
from typing import Optional
import uuid

import sqlalchemy
from sqlalchemy import select, tuple_, distinct, and_, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.external import Image, OCRText, Embedding, ImageTag


class ImageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def search(
        self,
        q: Optional[str],
        tags: dict[str, set],
        cursor_created_at: Optional[datetime],
        cursor_id: Optional[uuid.UUID],
        limit: int,
    ):
        img = aliased(Image)
        ocr = aliased(OCRText)
        image_tag = aliased(ImageTag)

        query = select(img.id, img.filename, img.created_at)

        if cursor_created_at and cursor_id:
            query = query.where(
                tuple_(img.created_at, img.id) < tuple_(cursor_created_at, cursor_id)
            )

        if q:
            text_filter = (
                select(distinct(img.id))
                .join(ocr, ocr.image_id == img.id)
                .where(sqlalchemy.func.upper(ocr.text).contains(str.upper(q)))
            )
            ids_result = await self.session.execute(text_filter)
            query = query.where(img.id.in_([id for (id,) in ids_result.all()]))

        if tags:
            queries = [
                select(distinct(image_tag.image_id)).where(
                    and_(image_tag.key == key, image_tag.value.in_(values))
                )
                for key, values in tags.items()
            ]
            tags_filter = union_all(*queries)
            ids_result = await self.session.execute(tags_filter)
            query = query.where(img.id.in_([id for (id,) in ids_result.all()]))

        subquery = query.subquery()

        facets_query = (
            select(image_tag.key, image_tag.value)
            .where(image_tag.image_id.in_(select(subquery.c.id).subquery()))
            .group_by(image_tag.key, image_tag.value)
        )
        facets_result = await self.session.execute(facets_query)
        raw_facets = defaultdict(set)
        for k, v in facets_result.all():
            raw_facets[k].add(v)

        results = await self.session.execute(
            query.order_by(img.created_at.desc(), img.id.desc()).limit(limit + 1)
        )

        return results.all(), dict(raw_facets)

    async def get_filename(self, image_id: str) -> Optional[str]:
        result = await self.session.execute(
            select(Image.filename).where(Image.id == image_id)
        )
        return result.scalar_one_or_none()

    async def get_texts(self, image_ids: set[str], min_confidence: float = 0.8):
        result = await self.session.execute(
            select(OCRText.image_id, OCRText.text, OCRText.confidence)
            .where(OCRText.image_id.in_(image_ids), OCRText.confidence > min_confidence)
        )
        return result.all()

    async def get_tags(self, image_ids: set[str]):
        result = await self.session.execute(
            select(ImageTag.image_id, ImageTag.key, ImageTag.value, ImageTag.source)
            .where(ImageTag.image_id.in_(image_ids))
        )
        return result.all()

    async def get_embedding(self, image_id: str):
        result = await self.session.execute(
            select(Embedding.embedding).where(Embedding.image_id == image_id)
        )
        return result.scalar_one()

    async def get_similar(self, image_id: str, embedding, limit: int = 10):
        img = aliased(Image)
        embed = aliased(Embedding)
        result = await self.session.execute(
            select(embed.image_id, embed.embedding.cosine_distance(embedding), img.filename)
            .join(img, img.id == embed.image_id)
            .filter(embed.image_id != image_id)
            .order_by(embed.embedding.cosine_distance(embedding))
            .limit(limit)
        )
        return result.all()

    async def get_meme_data(self, image_id: str):
        filename = await self.get_filename(image_id)

        texts_result = await self.session.execute(
            select(
                OCRText.text
            )
            .where(
                OCRText.image_id == image_id
            )
        )
        texts = [t for (t,) in texts_result]

        tags_result = await self.session.execute(
            select(ImageTag.key, ImageTag.value).where(ImageTag.image_id == image_id)
        )
        tags = tags_result.all()

        return filename, texts, tags