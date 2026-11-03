"""Data access for comics (epic multi-image-comics, C1): the lifecycle of a named, ordered list of images.
Never commits: the caller (the request's get_async_db session) controls commit timing.

Every refusal is a ComicsError with a stable `code` and a `detail` dict:
  name_invalid (reason), name_taken, not_found, built, image_in_comics (comics_id, comics_name),
  image_not_addable (reason: not_found, not_active, flagged, live_cut), reorder_mismatch,
  not_buildable (reason: too_few_panels, panel_missing, image_not_active, image_already_built).

Concurrency: every mutator takes the comics row lock (SELECT ... FOR UPDATE) as its first read. A caller that
reads the panels to compose an image and then calls mark_built (C4) must take the lock at the start with
get(comics_id, lock=True) in the same transaction, so no panel change or build can slip in between.
"""
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from repository.comics_name import ComicsNameError, name_key_of, normalize_name
from Storage.models import (Comics, ComicsPanel, ComicsState, CutState, Image, ImageCut, ImageExtras,
                            ImageStatus)

MIN_BUILD_PANELS = 2


class ComicsError(Exception):
    def __init__(self, code: str, **detail):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_key(query: str) -> str:
    """The query in the form name_key is stored in (NFC, casefolded). No name validation: a search never raises."""
    return name_key_of(query)


def _is_name_key_violation(exc: IntegrityError) -> bool:
    return "uq_comics_name_key" in str(exc.orig)


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

    async def _load(self, comics_id: uuid.UUID, lock: bool = False) -> Comics:
        """The comics with its panels in order, refreshed from the database; raises not_found.
        lock=True takes the comics row lock (FOR UPDATE) so concurrent panel changes of one comics serialise."""
        query = (select(Comics).where(Comics.id == comics_id)
                 .options(selectinload(Comics.panels)).execution_options(populate_existing=True))
        if lock:
            query = query.with_for_update(of=Comics)
        comics = (await self.session.execute(query)).scalar_one_or_none()
        if comics is None:
            raise ComicsError("not_found")
        return comics

    async def _load_draft(self, comics_id: uuid.UUID, lock: bool = False) -> Comics:
        comics = await self._load(comics_id, lock=lock)
        if comics.state != ComicsState.DRAFT:
            raise ComicsError("built")
        return comics

    async def create(self, name: str) -> Comics:
        name, name_key = self._normalize(name)
        await self._ensure_name_free(name_key)
        comics = Comics(name=name, name_key=name_key, state=ComicsState.DRAFT)
        try:
            async with self.session.begin_nested():
                self.session.add(comics)
                await self.session.flush()
        except IntegrityError as exc:
            if not _is_name_key_violation(exc):
                raise
            raise ComicsError("name_taken") from exc
        return await self._load(comics.id)

    async def rename(self, comics_id: uuid.UUID, name: str) -> Comics:
        comics = await self._load_draft(comics_id, lock=True)
        name, name_key = self._normalize(name)
        await self._ensure_name_free(name_key, exclude_id=comics.id)
        comics_id = comics.id
        try:
            async with self.session.begin_nested():
                comics.name = name
                comics.name_key = name_key
                await self.session.flush()
        except IntegrityError as exc:
            if not _is_name_key_violation(exc):
                raise
            raise ComicsError("name_taken") from exc
        return await self._load(comics_id)

    async def get(self, comics_id: uuid.UUID, lock: bool = False) -> Comics:
        return await self._load(comics_id, lock=lock)

    async def search(self, query: str | None, limit: int = 50, offset: int = 0) -> list[Comics]:
        stmt = select(Comics).options(selectinload(Comics.panels))
        if query:
            stmt = stmt.where(Comics.name_key.like(f"%{_escape_like(_search_key(query))}%", escape="\\"))
        stmt = stmt.order_by(Comics.created_at.desc(), Comics.id).limit(limit).offset(offset)
        return list((await self.session.execute(stmt)).scalars().all())

    async def delete(self, comics_id: uuid.UUID) -> None:
        comics = await self._load_draft(comics_id, lock=True)
        await self.session.delete(comics)
        await self.session.flush()

    async def mark_built(self, comics_id: uuid.UUID, built_image_id: uuid.UUID, built_content_hash: str) -> Comics:
        comics = await self._load_draft(comics_id, lock=True)
        if len(comics.panels) < MIN_BUILD_PANELS:
            raise ComicsError("not_buildable", reason="too_few_panels")
        if any(p.image_id is None for p in comics.panels):
            raise ComicsError("not_buildable", reason="panel_missing")
        status = (await self.session.execute(
            select(Image.status).where(Image.id == built_image_id))).scalar_one_or_none()
        if status != ImageStatus.ACTIVE:
            raise ComicsError("not_buildable", reason="image_not_active")
        used = (await self.session.execute(
            select(Comics.id).where(Comics.built_image_id == built_image_id).limit(1))).first()
        if used is not None:
            raise ComicsError("not_buildable", reason="image_already_built")
        comics.state = ComicsState.BUILT
        comics.built_image_id = built_image_id
        comics.built_content_hash = built_content_hash
        comics.built_at = func.now()
        await self.session.flush()
        await self.session.refresh(comics, ["built_at"])
        return comics

    # ---- panel operations (draft comics only) ----

    async def _compact(self, comics_id: uuid.UUID) -> None:
        """Renumber the comics' panels 0..n-1 in their current order; the deferred unique constraint lets
        the whole renumbering happen in one flush."""
        query = (select(ComicsPanel).where(ComicsPanel.comics_id == comics_id)
                 .order_by(ComicsPanel.position).execution_options(populate_existing=True))
        panels = (await self.session.execute(query)).scalars().all()
        for index, panel in enumerate(panels):
            if panel.position != index:
                panel.position = index
        await self.session.flush()

    async def _panel_owner(self, image_id: uuid.UUID):
        """(id, name) of the comics that holds the image as a panel, or None."""
        return (await self.session.execute(
            select(Comics.id, Comics.name).join(ComicsPanel, ComicsPanel.comics_id == Comics.id)
            .where(ComicsPanel.image_id == image_id))).first()

    async def _check_addable(self, image_id: uuid.UUID) -> Image:
        image = (await self.session.execute(
            select(Image).where(Image.id == image_id).execution_options(populate_existing=True))).scalar_one_or_none()
        if image is None:
            raise ComicsError("image_not_addable", reason="not_found")
        if image.status != ImageStatus.ACTIVE:
            raise ComicsError("image_not_addable", reason="not_active")
        flagged = (await self.session.execute(
            select(ImageExtras.flagged).where(ImageExtras.image_id == image_id))).scalar_one_or_none()
        if flagged:
            raise ComicsError("image_not_addable", reason="flagged")
        live = (await self.session.execute(
            select(ImageCut.id).where(ImageCut.state.in_(CutState.LIVE),
                                      or_(ImageCut.source_image_id == image_id, ImageCut.cut_image_id == image_id))
            .limit(1))).first()
        if live is not None:
            raise ComicsError("image_not_addable", reason="live_cut")
        return image

    async def add_image(self, comics_id: uuid.UUID, image_id: uuid.UUID) -> ComicsPanel:
        comics = await self._load_draft(comics_id, lock=True)
        owner = await self._panel_owner(image_id)
        if owner is not None:
            raise ComicsError("image_in_comics", comics_id=owner.id, comics_name=owner.name)
        image = await self._check_addable(image_id)
        panel = ComicsPanel(comics_id=comics.id, image_id=image.id, position=len(comics.panels),
                            source_image_id=image.id, source_filename=image.filename,
                            source_content_hash=image.content_hash)
        try:
            async with self.session.begin_nested():
                self.session.add(panel)
                await self.session.flush()
        except IntegrityError as exc:
            if "uq_comics_panels_image_id" not in str(exc.orig):
                raise
            owner = await self._panel_owner(image_id)
            if owner is None:
                raise
            raise ComicsError("image_in_comics", comics_id=owner.id, comics_name=owner.name) from exc
        return panel

    async def move_image(self, image_id: uuid.UUID, to_comics_id: uuid.UUID) -> ComicsPanel:
        panel = (await self.session.execute(
            select(ComicsPanel).where(ComicsPanel.image_id == image_id)
            .execution_options(populate_existing=True))).scalar_one_or_none()
        if panel is None:
            raise ComicsError("not_found")
        from_comics_id = panel.comics_id
        # Lock in a fixed order so two opposite moves cannot deadlock.
        loaded = {}
        for cid in sorted({from_comics_id, to_comics_id}, key=str):
            loaded[cid] = await self._load_draft(cid, lock=True)
        target = loaded[to_comics_id]
        panel = next((p for p in loaded[from_comics_id].panels if p.id == panel.id), None)
        if panel is None:  # moved by someone else while we waited for the locks
            raise ComicsError("not_found")
        panel.comics_id = to_comics_id
        others = [p.position for p in target.panels if p.id != panel.id]
        panel.position = max(others) + 1 if others else 0
        await self.session.flush()
        await self._compact(from_comics_id)
        return panel

    async def remove_panel(self, panel_id: uuid.UUID) -> None:
        comics_id = (await self.session.execute(
            select(ComicsPanel.comics_id).where(ComicsPanel.id == panel_id))).scalar_one_or_none()
        if comics_id is None:
            raise ComicsError("not_found")
        comics = await self._load_draft(comics_id, lock=True)
        panel = next((p for p in comics.panels if p.id == panel_id), None)
        if panel is None:
            raise ComicsError("not_found")
        await self.session.delete(panel)
        await self.session.flush()
        await self._compact(comics_id)

    async def reorder(self, comics_id: uuid.UUID, panel_ids: list[uuid.UUID]) -> None:
        comics = await self._load_draft(comics_id, lock=True)
        current = {p.id: p for p in comics.panels}
        if len(panel_ids) != len(current) or set(panel_ids) != set(current):
            raise ComicsError("reorder_mismatch")
        for index, panel_id in enumerate(panel_ids):
            current[panel_id].position = index
        await self.session.flush()
