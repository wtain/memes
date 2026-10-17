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


async def images_with_stale_rows(session: AsyncSession, model: type, status: str) -> list[uuid.UUID]:
    """Ids of images in `status` having at least one stale row in `model`, each reported once."""
    _require_stale_model(model)
    statement = (
        select(model.image_id)
        .join(Image, Image.id == model.image_id)
        .where(model.stale.is_(True), Image.status == status)
        .distinct()
        .order_by(model.image_id)
    )
    return list((await session.execute(statement)).scalars().all())


async def delete_stale_rows(session: AsyncSession, model: type, image_ids: Sequence[uuid.UUID]) -> int:
    """Delete the stale rows of the given images (rows that are not stale are never touched). Returns the count."""
    _require_stale_model(model)
    ids = list(image_ids)
    deleted = 0
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start:start + _CHUNK]
        result = await session.execute(
            delete(model).where(model.stale.is_(True), model.image_id.in_(chunk))
        )
        deleted += result.rowcount
    return deleted


async def stale_counts(session: AsyncSession) -> dict[str, int]:
    """Stale row count per marked table, keyed by table name."""
    counts = {}
    for model in STALE_MODELS:
        statement = select(func.count()).select_from(model).where(model.stale.is_(True))
        counts[model.__tablename__] = (await session.execute(statement)).scalar_one()
    return counts
