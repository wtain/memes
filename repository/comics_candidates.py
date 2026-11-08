"""Data access for suggested comics (epic multi-image-comics, C9): groups of images the detector thinks may be one story.
Never commits: the caller (the request's get_async_db session, or the batch job) controls commit timing.

Every refusal is a CandidateError with a stable `code`: not_found, candidate_decided.

A candidate is listed only while every image of it is active, not flagged, in no comics panel and still present
(checked in SQL at read time; the candidate row itself stays). Deleted images drop out of comics_candidate_images by
cascade, so a group left with fewer than two images is not listed either.
"""
import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import and_, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload

from Storage.models import (CandidateState, ComicsCandidate, ComicsCandidateImage, ComicsPanel, Image, ImageExtras,
                            ImageStatus)

MIN_GROUP = 2


class CandidateError(Exception):
    def __init__(self, code: str, **detail):
        super().__init__(code)
        self.code = code
        self.detail = detail


def candidate_key(image_ids) -> str:
    """The unique key of a group: md5 of its sorted image ids, so the order of discovery does not matter."""
    return hashlib.md5("".join(sorted(str(i) for i in image_ids)).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class CandidateImageRow:
    id: uuid.UUID
    filename: str
    width: int | None
    height: int | None


@dataclass(frozen=True)
class CandidateRow:
    id: uuid.UUID
    max_gap_seconds: float
    max_distance: float
    detector_version: str
    images: list[CandidateImageRow]


class ComicsCandidatesRepository:

    def __init__(self, session):
        self.session = session

    async def insert_group(self, image_ids_in_order: list[uuid.UUID], max_gap_seconds: float, max_distance: float,
                           detector_version: str) -> uuid.UUID | None:
        """Stores an open candidate; None when a candidate with the same key exists (any state)."""
        key = candidate_key(image_ids_in_order)
        candidate_id = uuid.uuid4()
        inserted = (await self.session.execute(
            insert(ComicsCandidate).values(
                id=candidate_id, key=key, state=CandidateState.OPEN, detector_version=detector_version,
                max_gap_seconds=max_gap_seconds, max_distance=max_distance)
            .on_conflict_do_nothing(index_elements=[ComicsCandidate.key]).returning(ComicsCandidate.id))).scalar_one_or_none()
        if inserted is None:
            return None
        await self.session.execute(insert(ComicsCandidateImage), [
            {"candidate_id": candidate_id, "image_id": image_id, "position": position}
            for position, image_id in enumerate(image_ids_in_order)])
        return candidate_id

    async def overlaps_decided(self, image_ids: list[uuid.UUID]) -> bool:
        """True when two or more of the images belong to one dismissed or accepted candidate (all its rows count)."""
        shared = (select(ComicsCandidateImage.candidate_id)
                  .join(ComicsCandidate, ComicsCandidate.id == ComicsCandidateImage.candidate_id)
                  .where(ComicsCandidateImage.image_id.in_(image_ids),
                         ComicsCandidate.state.in_((CandidateState.DISMISSED, CandidateState.ACCEPTED)))
                  .group_by(ComicsCandidateImage.candidate_id)
                  .having(func.count() >= MIN_GROUP).limit(1))
        return (await self.session.execute(shared)).first() is not None

    async def list_open(self, offset: int, limit: int) -> list[CandidateRow]:
        """Open candidates whose images are all still eligible, strongest first (smallest max_distance, then id)."""
        ineligible = (
            select(ComicsCandidateImage.candidate_id)
            .select_from(ComicsCandidateImage)
            .join(Image, Image.id == ComicsCandidateImage.image_id)
            .where(ComicsCandidateImage.candidate_id == ComicsCandidate.id)
            .where(
                (Image.status != ImageStatus.ACTIVE)
                | exists().where(and_(ImageExtras.image_id == Image.id, ImageExtras.flagged.is_(True)))
                | exists().where(ComicsPanel.image_id == Image.id)))
        remaining = (select(func.count()).select_from(ComicsCandidateImage)
                     .where(ComicsCandidateImage.candidate_id == ComicsCandidate.id).scalar_subquery())
        candidates = (await self.session.execute(
            select(ComicsCandidate)
            .where(ComicsCandidate.state == CandidateState.OPEN, ~exists(ineligible), remaining >= MIN_GROUP)
            .order_by(ComicsCandidate.max_distance, ComicsCandidate.id)
            .offset(offset).limit(limit))).scalars().all()
        if not candidates:
            return []
        rows = await self.session.execute(
            select(ComicsCandidateImage.candidate_id, Image.id, Image.filename, Image.width, Image.height)
            .join(Image, Image.id == ComicsCandidateImage.image_id)
            .where(ComicsCandidateImage.candidate_id.in_([c.id for c in candidates]))
            .order_by(ComicsCandidateImage.candidate_id, ComicsCandidateImage.position))
        images: dict[uuid.UUID, list[CandidateImageRow]] = {c.id: [] for c in candidates}
        for row in rows:
            images[row.candidate_id].append(CandidateImageRow(row.id, row.filename, row.width, row.height))
        return [CandidateRow(c.id, c.max_gap_seconds, c.max_distance, c.detector_version, images[c.id])
                for c in candidates]

    async def get(self, candidate_id: uuid.UUID, lock: bool = False) -> ComicsCandidate:
        """The candidate with its image rows in order, refreshed from the database; raises not_found.
        lock=True takes the row lock (FOR UPDATE) so two decisions of one candidate serialise."""
        query = (select(ComicsCandidate).where(ComicsCandidate.id == candidate_id)
                 .options(selectinload(ComicsCandidate.images)).execution_options(populate_existing=True))
        if lock:
            query = query.with_for_update(of=ComicsCandidate)
        candidate = (await self.session.execute(query)).scalar_one_or_none()
        if candidate is None:
            raise CandidateError("not_found")
        return candidate

    async def decide(self, candidate_id: uuid.UUID, state: str, comics_id: uuid.UUID | None = None) -> ComicsCandidate:
        """Moves an open candidate to accepted or dismissed; raises not_found and candidate_decided."""
        if state not in (CandidateState.ACCEPTED, CandidateState.DISMISSED):
            raise ValueError(f"cannot decide a candidate as {state!r}")
        candidate = await self.get(candidate_id, lock=True)
        if candidate.state != CandidateState.OPEN:
            raise CandidateError("candidate_decided", state=candidate.state)
        await self.session.execute(
            update(ComicsCandidate).where(ComicsCandidate.id == candidate_id)
            .values(state=state, comics_id=comics_id, decided_at=func.now()))
        return await self.get(candidate_id)
