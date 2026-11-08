"""Suggested comics service (epic multi-image-comics, C9): lists, accepts and dismisses candidate groups.
Mutations only flush; get_async_db commits. Accept is atomic by itself (a savepoint), so a failure after the
draft was created leaves neither the draft nor a decided candidate."""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.services.comics_service import detail
from repository.comics import ComicsError, ComicsRepository
from repository.comics_candidates import MIN_GROUP, CandidateError, ComicsCandidatesRepository
from repository.comics_name import default_comics_name
from Storage.models import CandidateState, Image


def candidate_http_error(error: CandidateError) -> HTTPException:
    """not_found 404, candidate_decided 409, candidate_incomplete 409, subset_invalid 422; detail {"code": ..., **detail}."""
    status = {"not_found": 404, "candidate_decided": 409, "subset_invalid": 422}.get(error.code, 409)
    return HTTPException(status_code=status, detail={"code": error.code, **error.detail})


class ComicsCandidatesService:

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ComicsCandidatesRepository(session)
        self.comics = ComicsRepository(session)

    async def list(self, offset: int, limit: int) -> dict:
        rows = await self.repo.list_open(offset, limit + 1)
        return {"items": [{"id": str(r.id), "maxDistance": r.max_distance, "maxGapSeconds": r.max_gap_seconds,
                           "images": [{"id": str(i.id), "filename": i.filename, "width": i.width,
                                       "height": i.height} for i in r.images]} for r in rows[:limit]],
                "hasMore": len(rows) > limit}

    async def dismiss(self, candidate_id: uuid.UUID) -> None:
        candidate = await self.repo.get(candidate_id, lock=True)
        if candidate.state == CandidateState.DISMISSED:
            return  # idempotent
        await self.repo.decide(candidate_id, CandidateState.DISMISSED)  # accepted -> candidate_decided

    @staticmethod
    def _chosen(candidate, image_ids: list[uuid.UUID] | None) -> list[uuid.UUID]:
        in_group = [row.image_id for row in sorted(candidate.images, key=lambda r: r.position)]
        if image_ids is None:
            if len(in_group) != candidate.size or len(in_group) < MIN_GROUP:
                raise CandidateError("candidate_incomplete", size=candidate.size, remaining=len(in_group))
            return in_group
        if len(set(image_ids)) != len(image_ids):
            raise CandidateError("subset_invalid", reason="duplicate_image")
        if any(i not in set(in_group) for i in image_ids):
            raise CandidateError("subset_invalid", reason="unknown_image")
        if len(image_ids) < MIN_GROUP:
            raise CandidateError("subset_invalid", reason="too_few_images")
        return list(image_ids)

    async def _check_addable(self, ids: list[uuid.UUID]) -> None:
        """The first image (in the chosen order) that cannot be added fails with the comics service's reason."""
        for image_id, found in (await self.comics.addability(ids)).items():
            if found.addable:
                continue
            if found.reason == "in_comics":
                raise ComicsError("image_in_comics", comics_id=found.comics_id, comics_name=found.comics_name,
                                  image_id=image_id)
            raise ComicsError("image_not_addable", reason=found.reason, image_id=image_id)

    async def accept(self, candidate_id: uuid.UUID, image_ids: list[uuid.UUID] | None) -> dict:
        candidate = await self.repo.get(candidate_id, lock=True)
        if candidate.state != CandidateState.OPEN:
            raise CandidateError("candidate_decided", state=candidate.state)
        ids = self._chosen(candidate, image_ids)
        await self._check_addable(ids)
        first = ids[0]
        filenames = {first: (await self.session.execute(select(Image.filename).where(Image.id == first))).scalar()}
        async with self.session.begin_nested():
            try:
                created = await self.comics.create(default_comics_name(filenames.get(first), first))
            except ComicsError as error:
                if error.code != "name_taken":
                    raise
                created = await self.comics.create(default_comics_name(filenames.get(first), first, True))
            for image_id in ids:
                await self.comics.add_image(created.id, image_id)
            await self.repo.decide(candidate_id, CandidateState.ACCEPTED, created.id)
        return detail(await self.comics.get(created.id))
