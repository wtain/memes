"""Comics API service (epic multi-image-comics, C3): shapes repository results for the router.
The repository is built from the request session; mutations only flush (get_async_db commits)."""
from __future__ import annotations  # the method named `list` would otherwise shadow list[...] in annotations

import asyncio
import os
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from batch.utils.comics_compose import ComposeError, compose_preview
from config.settings import settings
from repository.comics import ComicsError, ComicsRepository
from Storage.models import ComicsState, Image

MAX_ADDABILITY_IDS = 100
# Process-wide: at most two previews compose at once (each decodes every panel); `async with` releases on failure.
_PREVIEW_SEMAPHORE = asyncio.Semaphore(2)


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(part.capitalize() for part in rest)


def http_error(error: ComicsError) -> HTTPException:
    """The HTTP form of a ComicsError: detail {"code": ..., **detail} with camelCase keys and string ids."""
    if error.code == "not_found":
        status = 404
    elif error.code == "image_not_addable":
        status = 404 if error.detail.get("reason") == "not_found" else 409
    elif error.code in ("name_invalid", "reorder_mismatch"):
        status = 422
    else:  # name_taken, built, image_in_comics, not_buildable
        status = 409
    body = {_camel(k): (str(v) if isinstance(v, uuid.UUID) else v) for k, v in error.detail.items()}
    return HTTPException(status_code=status, detail={"code": error.code, **body})


def compose_http_error(error: ComposeError) -> HTTPException:
    """422 compose_failed; ComposeError.position is 0-based or None, the body's is 1-based or null."""
    position = error.position + 1 if error.position is not None else None
    return HTTPException(status_code=422, detail={"code": "compose_failed", "reason": error.reason,
                                                  "position": position})


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _sorted_panels(comics) -> list:
    return sorted(comics.panels, key=lambda p: p.position)


def _detail(comics) -> dict:
    return {
        "id": str(comics.id),
        "name": comics.name,
        "state": comics.state,
        "builtImageId": str(comics.built_image_id) if comics.built_image_id else None,
        "builtAt": _iso(comics.built_at),
        "panels": [{"id": str(p.id), "position": p.position,
                    "imageId": str(p.image_id) if p.image_id else None,
                    "sourceFilename": p.source_filename} for p in _sorted_panels(comics)],
    }


def _summary(comics) -> dict:
    panels = _sorted_panels(comics)
    if comics.state == ComicsState.BUILT and comics.built_image_id:
        cover = comics.built_image_id
    else:
        cover = panels[0].image_id if panels else None
    return {"id": str(comics.id), "name": comics.name, "state": comics.state, "panelCount": len(panels),
            "coverImageId": str(cover) if cover else None, "createdAt": _iso(comics.created_at),
            "builtAt": _iso(comics.built_at)}


class ComicsService:

    def __init__(self, session: AsyncSession, base_path: str | None = None):
        self.session = session
        self.repo = ComicsRepository(session)
        self.base_path = base_path

    async def list(self, q: str | None, limit: int, offset: int) -> dict:
        rows = await self.repo.search(q, limit + 1, offset)
        return {"items": [_summary(c) for c in rows[:limit]], "hasMore": len(rows) > limit}

    async def create(self, name: str) -> dict:
        return _detail(await self.repo.create(name))

    async def detail(self, comics_id: uuid.UUID) -> dict:
        return _detail(await self.repo.get(comics_id))

    async def rename(self, comics_id: uuid.UUID, name: str) -> dict:
        return _detail(await self.repo.rename(comics_id, name))

    async def delete(self, comics_id: uuid.UUID) -> None:
        await self.repo.delete(comics_id)

    async def add_panel(self, comics_id: uuid.UUID, image_id: uuid.UUID, move: bool) -> dict:
        if move:
            await self.repo.move_image(image_id, comics_id)
        else:
            await self.repo.add_image(comics_id, image_id)
        return _detail(await self.repo.get(comics_id))

    async def remove_panel(self, comics_id: uuid.UUID, panel_id: uuid.UUID) -> None:
        comics = await self.repo.get(comics_id)
        if all(p.id != panel_id for p in comics.panels):
            raise ComicsError("not_found")
        await self.repo.remove_panel(panel_id)

    async def reorder(self, comics_id: uuid.UUID, panel_ids: list[uuid.UUID]) -> dict:
        await self.repo.reorder(comics_id, panel_ids)
        return _detail(await self.repo.get(comics_id))

    async def addability(self, image_ids: list[uuid.UUID]) -> dict:
        found = await self.repo.addability(image_ids)
        return {"items": [{"imageId": str(a.image_id), "addable": a.addable, "reason": a.reason,
                           "comicsId": str(a.comics_id) if a.comics_id else None,
                           "comicsName": a.comics_name} for a in found.values()]}

    async def _filenames(self, image_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        rows = await self.session.execute(select(Image.id, Image.filename).where(Image.id.in_(image_ids)))
        return {row[0]: row[1] for row in rows}

    async def preview(self, comics_id: uuid.UUID, max_width: int) -> bytes:
        """JPEG preview of a draft's panels (read only, no write lock: a concurrent edit may show either state)."""
        comics = await self.repo.get(comics_id)
        if comics.state == ComicsState.BUILT:
            raise ComicsError("built")
        panels = _sorted_panels(comics)
        ids = [p.image_id for p in panels if p.image_id]
        found = await self._filenames(ids) if ids else {}
        base = self.base_path or settings.get("BASE_PATH")
        paths = []
        for index, panel in enumerate(panels):
            filename = found.get(panel.image_id) if panel.image_id else None
            if filename is None:  # the image row is gone (deleting it nulls a draft panel's image_id); report that panel as unreadable
                raise ComposeError("unreadable", index, "the panel's image is gone")
            paths.append(os.path.join(base, filename))
        async with _PREVIEW_SEMAPHORE:
            return await asyncio.to_thread(compose_preview, paths, max_width)

    async def by_image(self, image_id: uuid.UUID) -> dict:
        found = await self.repo.find_by_image(image_id)
        if found is None:
            return {"comics": None}
        comics, panel = found
        return {"comics": {"id": str(comics.id), "name": comics.name, "state": comics.state,
                           "position": panel.position}}
