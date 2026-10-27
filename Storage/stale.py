"""Shared helpers for rows marked stale (epic screenshot-recut, ADR-001).

A cut image receives copies of model-derived rows from its source, flagged `stale`. A job's
'refresh stale' mode uses these functions: find the images that still carry stale rows, delete those
rows, then rewrite them with the job's normal logic (rewritten rows take the default stale=False, so
nothing needs clearing). None of these functions commits; get_async_db owns commit and rollback.
"""
import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import Image, ImageDescription, ImageTag, OCRLemma, OCRText

STALE_MODELS = (OCRText, OCRLemma, ImageDescription, ImageTag)

# ids per DELETE statement; asyncpg allows 32767 bind parameters per statement
_CHUNK = 5000


def _require_stale_model(model: type) -> None:
    if model not in STALE_MODELS:
        raise ValueError(f"{getattr(model, '__name__', model)} has no stale column")


def _scope_to_source(statement, model: type, source: str | None):
    """Restrict a statement to one `source` (tags: OCR, Ollama, Note); only models with a source column accept it."""
    if source is None:
        return statement
    if not hasattr(model, "source"):
        raise ValueError(f"{model.__name__} has no source column")
    return statement.where(model.source == source)


async def images_with_stale_rows(session: AsyncSession, model: type, status: str,
                                 source: str | None = None) -> list[uuid.UUID]:
    """Ids of images in `status` having at least one stale row in `model` (of `source`, for tags), each reported once."""
    _require_stale_model(model)
    statement = (
        select(model.image_id)
        .join(Image, Image.id == model.image_id)
        .where(model.stale.is_(True), Image.status == status)
        .distinct()
        .order_by(model.image_id)
    )
    return list((await session.execute(_scope_to_source(statement, model, source))).scalars().all())


async def delete_stale_rows(session: AsyncSession, model: type, image_ids: Sequence[uuid.UUID],
                            source: str | None = None) -> int:
    """Delete the stale rows (of `source`, for tags) of the given images; rows that are not stale are never touched.
    Returns the count."""
    _require_stale_model(model)
    ids = list(image_ids)
    deleted = 0
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start:start + _CHUNK]
        statement = delete(model).where(model.stale.is_(True), model.image_id.in_(chunk))
        result = await session.execute(_scope_to_source(statement, model, source))
        deleted += result.rowcount
    return deleted


async def stale_among(session: AsyncSession, model: type, image_ids: Sequence[uuid.UUID],
                      source: str | None = None) -> set[uuid.UUID]:
    """Those of `image_ids` that still have a stale row in `model` (the upstream-ready check of a refresh)."""
    _require_stale_model(model)
    ids = list(image_ids)
    found: set[uuid.UUID] = set()
    for start in range(0, len(ids), _CHUNK):
        statement = (
            select(model.image_id)
            .where(model.stale.is_(True), model.image_id.in_(ids[start:start + _CHUNK]))
            .distinct()
        )
        found.update((await session.execute(_scope_to_source(statement, model, source))).scalars().all())
    return found


async def stale_counts(session: AsyncSession) -> dict[str, int]:
    """Stale row count per marked table, keyed by table name."""
    counts = {}
    for model in STALE_MODELS:
        statement = select(func.count()).select_from(model).where(model.stale.is_(True))
        counts[model.__tablename__] = (await session.execute(statement)).scalar_one()
    return counts
