from typing import Optional

from sqlalchemy import select, distinct, func, cast, String, union, literal, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from Storage.models import Image, OCRText, ImageTag, ImageExtras

OCR_CONFIDENCE_THRESHOLD = 0.8


class RecommendationsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_recommendations(
        self,
        words: list[str],
        seed: int,
        last_hash: Optional[str],
        limit: int,
    ) -> list:
        img = aliased(Image)
        extras = aliased(ImageExtras)

        hash_expr = func.md5(func.concat(cast(img.id, String), literal(str(seed))))

        query = (
            select(img.id, img.filename, img.created_at, extras.flagged)
            .outerjoin(extras, img.id == extras.image_id)
            .where(or_(extras.flagged.is_(None), extras.flagged == False))
        )

        if words:
            matching_ids = await self._get_matching_ids(words)
            if not matching_ids:
                return []
            query = query.where(img.id.in_(matching_ids))

        if last_hash is not None:
            query = query.where(hash_expr > last_hash)

        query = query.order_by(hash_expr.asc()).limit(limit + 1)

        result = await self.session.execute(query)
        return result.all()

    async def _get_matching_ids(self, words: list[str]) -> set:
        matching_ids: Optional[set] = None

        for word in words:
            combined = func.string_agg(OCRText.text, ' ')
            ocr_subq = (
                select(OCRText.image_id)
                .where(OCRText.confidence > OCR_CONFIDENCE_THRESHOLD)
                .group_by(OCRText.image_id)
                .having(func.upper(combined).contains(word.upper()))
            )

            tag_subq = (
                select(distinct(ImageTag.image_id))
                .where(func.upper(ImageTag.value).contains(word.upper()))
            )

            result = await self.session.execute(union(ocr_subq, tag_subq))
            word_ids = {row[0] for row in result.all()}

            if matching_ids is None:
                matching_ids = word_ids
            else:
                matching_ids &= word_ids

            if not matching_ids:
                break

        return matching_ids or set()