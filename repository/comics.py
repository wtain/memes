"""Data access for comics (epic multi-image-comics, C1): the lifecycle of a named, ordered list of images.
Never commits: the caller (the request's get_async_db session) controls commit timing.

Every refusal is a ComicsError with a stable `code` and a `detail` dict:
  name_invalid (reason), name_taken, not_found, built, image_in_comics (comics_id, comics_name),
  image_not_addable (reason: not_found, not_active, flagged, live_cut), reorder_mismatch,
  not_buildable (reason: too_few_panels, panel_missing, image_not_active).
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from repository.comics_name import ComicsNameError, normalize_name
from Storage.models import Comics, ComicsState, Image, ImageStatus

MIN_BUILD_PANELS = 2


class ComicsError(Exception):
    def __init__(self, code: str, **detail):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ComicsRepository:

    def __init__(self, session):
        self.session = session

    @staticmethod
    def _normalize(raw: str) -> tuple[str, str]:
        try:
            return normalize_name(raw)
        except ComicsNameError as exc:
            raise ComicsError("name_invalid", reason=exc.reason) from exc

    async def _ensure_name_free(self, name_key: str, exclude_id: uuid.UUID | None = None) -> None:
        query = select(Comics.id).where(Comics.name_key == name_key)
        if exclude_id is not None:
            query = query.where(Comics.id != exclude_id)
        if (await self.session.execute(query.limit(1))).first() is not None:
            raise ComicsError("name_taken")

    async def _load(self, comics_id: uuid.UUID) -> Comics:
        """The comics with its panels in order, refreshed from the database; raises not_found."""
        query = (select(Comics).where(Comics.id == comics_id)
                 .options(selectinload(Comics.panels)).execution_options(populate_existing=True))
        comics = (await self.session.execute(query)).scalar_one_or_none()
        if comics is None:
            raise ComicsError("not_found")
        return comics

    async def _load_draft(self, comics_id: uuid.UUID) -> Comics:
        comics = await self._load(comics_id)
        if comics.state != ComicsState.DRAFT:
            raise ComicsError("built")
        return comics

    async def create(self, name: str) -> Comics:
        name, name_key = self._normalize(name)
        await self._ensure_name_free(name_key)
        comics = Comics(name=name, name_key=name_key, state=ComicsState.DRAFT)
        self.session.add(comics)
        await self.session.flush()
        return await self._load(comics.id)

    async def rename(self, comics_id: uuid.UUID, name: str) -> Comics:
        comics = await self._load_draft(comics_id)
        name, name_key = self._normalize(name)
        await self._ensure_name_free(name_key, exclude_id=comics.id)
        comics.name = name
        comics.name_key = name_key
        await self.session.flush()
        return comics

    async def get(self, comics_id: uuid.UUID) -> Comics:
        return await self._load(comics_id)

    async def search(self, query: str | None, limit: int = 50, offset: int = 0) -> list[Comics]:
        stmt = select(Comics).options(selectinload(Comics.panels))
        if query:
            stmt = stmt.where(Comics.name.ilike(f"%{_escape_like(query)}%", escape="\\"))
        stmt = stmt.order_by(Comics.created_at.desc(), Comics.id).limit(limit).offset(offset)
        return list((await self.session.execute(stmt)).scalars().all())

    async def delete(self, comics_id: uuid.UUID) -> None:
        comics = await self._load_draft(comics_id)
        await self.session.delete(comics)
        await self.session.flush()

    async def mark_built(self, comics_id: uuid.UUID, built_image_id: uuid.UUID, built_content_hash: str) -> Comics:
        comics = await self._load_draft(comics_id)
        if len(comics.panels) < MIN_BUILD_PANELS:
            raise ComicsError("not_buildable", reason="too_few_panels")
        if any(p.image_id is None for p in comics.panels):
            raise ComicsError("not_buildable", reason="panel_missing")
        status = (await self.session.execute(
            select(Image.status).where(Image.id == built_image_id))).scalar_one_or_none()
        if status != ImageStatus.ACTIVE:
            raise ComicsError("not_buildable", reason="image_not_active")
        comics.state = ComicsState.BUILT
        comics.built_image_id = built_image_id
        comics.built_content_hash = built_content_hash
        comics.built_at = func.now()
        await self.session.flush()
        await self.session.refresh(comics, ["built_at"])
        return comics
