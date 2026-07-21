from typing import Optional

from sqlalchemy import select, func, cast, String, literal, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from repository.ocr_lemmas import matching_image_ids
from Storage.models import Image, ImageExtras


class RecommendationsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_recommendations(
        self,
        q: Optional[str],
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

        matching_ids = await matching_image_ids(self.session, q)
        if matching_ids is not None:
            if not matching_ids:
                return []
            query = query.where(img.id.in_(matching_ids))

        if last_hash is not None:
            query = query.where(hash_expr > last_hash)

        query = query.order_by(hash_expr.asc()).limit(limit + 1)

        result = await self.session.execute(query)
        return result.all()
