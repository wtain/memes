import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import CutState, ImageCut

# The review queue: proposals with a cut file to show, best first. confidence is nullable and PostgreSQL sorts NULLs
# first on DESC, so unscored proposals are put last explicitly; id makes the order total so paging never repeats a row.
_QUEUE_FILTER = (
    ImageCut.state == CutState.PROPOSED,
    ImageCut.source_image_id.is_not(None),
    ImageCut.cut_image_id.is_not(None),
)
_QUEUE_ORDER = (ImageCut.confidence.desc().nulls_last(), ImageCut.id.asc())


def _item(row: ImageCut) -> dict:
    return {
        "id": str(row.id),
        "sourceImageId": str(row.source_image_id),
        "cutImageId": str(row.cut_image_id),
        "confidence": row.confidence,
        "box": {"x": row.crop_x, "y": row.crop_y, "w": row.crop_w, "h": row.crop_h},
    }


class RecutRepository:
    """Read side of the screenshot-recut review (R4). Never writes and never commits."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_proposed(self, offset: int, limit: int) -> tuple[list[dict], int]:
        rows = (await self.session.execute(
            select(ImageCut).where(*_QUEUE_FILTER).order_by(*_QUEUE_ORDER).offset(offset).limit(limit)
        )).scalars().all()
        total = (await self.session.execute(
            select(func.count()).select_from(ImageCut).where(*_QUEUE_FILTER)
        )).scalar_one()
        return [_item(row) for row in rows], total

    async def get(self, cut_id: uuid.UUID) -> dict | None:
        row = (await self.session.execute(
            select(ImageCut).where(
                ImageCut.id == cut_id, ImageCut.source_image_id.is_not(None), ImageCut.cut_image_id.is_not(None))
        )).scalar_one_or_none()
        if row is None:
            return None
        previous_id = next_id = None
        if row.state == CutState.PROPOSED:
            previous_id, next_id = await self._neighbours(cut_id)
        return {
            **_item(row),
            "state": row.state,
            "detector": row.detector,
            "detectorVersion": row.detector_version,
            "detectRunId": str(row.detect_run_id) if row.detect_run_id is not None else None,
            "createdAt": row.created_at.isoformat(),
            "previousId": previous_id,
            "nextId": next_id,
        }

    async def _neighbours(self, cut_id: uuid.UUID) -> tuple[str | None, str | None]:
        window = {"order_by": list(_QUEUE_ORDER)}
        ranked = (
            select(
                ImageCut.id.label("id"),
                func.lag(ImageCut.id).over(**window).label("previous_id"),
                func.lead(ImageCut.id).over(**window).label("next_id"),
            ).where(*_QUEUE_FILTER).subquery()
        )
        found = (await self.session.execute(
            select(ranked.c.previous_id, ranked.c.next_id).where(ranked.c.id == cut_id)
        )).one_or_none()
        if found is None:
            return None, None
        previous_id, next_id = found
        return (str(previous_id) if previous_id is not None else None,
                str(next_id) if next_id is not None else None)
