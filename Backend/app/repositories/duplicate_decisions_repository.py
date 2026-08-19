import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from Storage.models import DuplicateDecision, Image


class DuplicateDecisionsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _normalize(image_id1: uuid.UUID, image_id2: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
        return (image_id1, image_id2) if image_id1 < image_id2 else (image_id2, image_id1)

    async def record_decisions_bulk(self, pairs: list[tuple[uuid.UUID, uuid.UUID]]) -> None:
        if not pairs:
            return
        normalized = [self._normalize(a, b) for a, b in pairs]
        stmt = pg_insert(DuplicateDecision).values([
            {"image_id1": a, "image_id2": b} for a, b in normalized
        ]).on_conflict_do_nothing(index_elements=["image_id1", "image_id2"])
        await self.session.execute(stmt)

    async def delete_decisions(self, pairs: list[tuple[uuid.UUID, uuid.UUID]]) -> None:
        for a, b in pairs:
            id1, id2 = self._normalize(a, b)
            await self.session.execute(
                delete(DuplicateDecision).where(
                    DuplicateDecision.image_id1 == id1,
                    DuplicateDecision.image_id2 == id2,
                )
            )

    async def list_recent(
        self, limit: int, offset: int
    ) -> tuple[list[tuple[uuid.UUID, str, uuid.UUID, str, datetime]], int]:
        img1 = aliased(Image)
        img2 = aliased(Image)
        query = (
            select(
                DuplicateDecision.image_id1,
                img1.filename,
                DuplicateDecision.image_id2,
                img2.filename,
                DuplicateDecision.decided_at,
            )
            .join(img1, img1.id == DuplicateDecision.image_id1)
            .join(img2, img2.id == DuplicateDecision.image_id2)
            .order_by(DuplicateDecision.decided_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(query)).all()
        total = (await self.session.execute(select(func.count()).select_from(DuplicateDecision))).scalar_one()
        return [tuple(row) for row in rows], total
