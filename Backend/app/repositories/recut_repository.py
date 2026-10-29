import uuid
from datetime import datetime, timezone

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import CutState, Image, ImageCut, MemeTextRating

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


# Bands of the live approve-rate table. Mirrors evaluation/recut_report.py (BAND_LABELS), so gold and live numbers compare
# band for band. Lower edge inclusive; anything under 0.7 is the first band (the detector's floor is 0.6); 1.0 is its own.
_BANDS = (("0.6-0.7", 0.6, 0.7), ("0.7-0.8", 0.7, 0.8), ("0.8-0.9", 0.8, 0.9), ("0.9-1.0", 0.9, 1.0),
          ("1.0", 1.0, 1.0), ("unscored", None, None))
_DECIDABLE = (CutState.PROPOSED, CutState.APPROVED, CutState.REJECTED)


def _band_expression():
    return case(
        (ImageCut.confidence.is_(None), "unscored"),
        (ImageCut.confidence >= 1.0, "1.0"),
        (ImageCut.confidence >= 0.9, "0.9-1.0"),
        (ImageCut.confidence >= 0.8, "0.8-0.9"),
        (ImageCut.confidence >= 0.7, "0.7-0.8"),
        else_="0.6-0.7",
    )


def _stats_band(label: str, lower, upper, counts: dict) -> dict:
    approved, rejected = counts.get(CutState.APPROVED, 0), counts.get(CutState.REJECTED, 0)
    decided = approved + rejected
    return {"label": label, "lower": lower, "upper": upper, "waiting": counts.get(CutState.PROPOSED, 0),
            "approved": approved, "rejected": rejected, "approveRate": None if decided == 0 else approved / decided}


def lock_statement(cut_id: uuid.UUID):
    """The proposal row with a row lock: two deciders of one proposal take turns, the second sees the first's state."""
    return select(ImageCut).where(ImageCut.id == cut_id).with_for_update()


class RecutRepository:
    """The screenshot-recut review queue (R4, read) and the helpers of a decision (R5a, write). Never commits."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def band_stats(self) -> dict:
        """Waiting / approved / rejected counts and the approve rate per confidence band, computed at read time (NFR-5).
        'waiting' counts every proposed row; the review queue additionally needs both images, so the two can differ by
        orphaned rows."""
        band = _band_expression().label("band")
        rows = (await self.session.execute(
            select(band, ImageCut.state, func.count()).where(ImageCut.state.in_(_DECIDABLE)).group_by(band, ImageCut.state)
        )).all()
        per_band: dict[str, dict] = {}
        for label, state, count in rows:
            per_band.setdefault(label, {})[state] = count
        totals: dict[str, int] = {}
        for counts in per_band.values():
            for state, count in counts.items():
                totals[state] = totals.get(state, 0) + count
        return {"bands": [_stats_band(label, lower, upper, per_band.get(label, {})) for label, lower, upper in _BANDS],
                "total": _stats_band("total", None, None, totals)}

    async def lock_proposal(self, cut_id: uuid.UUID) -> ImageCut | None:
        return (await self.session.execute(lock_statement(cut_id))).scalar_one_or_none()

    async def images_by_id(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, Image]:
        rows = (await self.session.execute(select(Image).where(Image.id.in_(ids)))).scalars().all()
        return {row.id: row for row in rows}

    async def set_image_status(self, image_id: uuid.UUID, status: str) -> None:
        await self.session.execute(update(Image).where(Image.id == image_id).values(status=status))

    async def move_ratings(self, from_id: uuid.UUID, to_id: uuid.UUID) -> int:
        result = await self.session.execute(
            update(MemeTextRating).where(MemeTextRating.image_id == from_id).values(image_id=to_id))
        return result.rowcount

    async def mark_decided(self, row: ImageCut, state: str) -> datetime:
        decided_at = datetime.now(timezone.utc)
        row.state = state
        row.decided_at = decided_at
        await self.session.flush()
        return decided_at

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
