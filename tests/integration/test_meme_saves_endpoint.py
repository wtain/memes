import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.image_service import ImageService
from repository.popularity import PopularityRepository
from Storage.models import MemeSave
from tests.integration.test_popularity_repository import make_image


def make_service(session):
    return ImageService(ImageRepository(session), MagicMock(), MagicMock())


async def save_rows(session):
    return (await session.execute(select(func.count()).select_from(MemeSave))).scalar_one()


@pytest.mark.asyncio(loop_scope="session")
async def test_fresh_image_has_no_saves(db_session):
    img = await make_image(db_session, "a.jpg")
    result = await make_service(db_session).get_saves(str(img.id))
    assert result.count == 0 and result.lastSavedAt is None and result.imageId == str(img.id)


@pytest.mark.asyncio(loop_scope="session")
async def test_two_saves_count_twice(db_session):
    img = await make_image(db_session, "a.jpg")
    service = make_service(db_session)
    await service.record_save(str(img.id))
    await service.record_save(str(img.id))
    result = await service.get_saves(str(img.id))
    assert result.count == 2 and result.lastSavedAt is not None and result.imageId == str(img.id)


@pytest.mark.asyncio(loop_scope="session")
async def test_loser_save_shows_on_winner(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    await PopularityRepository(db_session).absorb(l.id, w.id, "cluster_review")
    service = make_service(db_session)
    await service.record_save(str(l.id))
    assert (await service.get_saves(str(w.id))).count == 1


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("bad", ["not-a-uuid", ""])
async def test_unparsable_id_is_404(db_session, bad):
    before = await save_rows(db_session)
    service = make_service(db_session)
    for call in (service.record_save, service.get_saves):
        with pytest.raises(HTTPException) as exc:
            await call(bad)
        assert exc.value.status_code == 404
    assert await save_rows(db_session) == before


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_id_is_404_and_inserts_nothing(db_session):
    before = await save_rows(db_session)
    service = make_service(db_session)
    unknown = str(uuid.uuid4())
    for call in (service.record_save, service.get_saves):
        with pytest.raises(HTTPException) as exc:
            await call(unknown)
        assert exc.value.status_code == 404
    assert await save_rows(db_session) == before


@pytest.mark.asyncio(loop_scope="session")
async def test_flagged_and_non_active_images_can_be_recorded(db_session):
    flagged = await make_image(db_session, "f.jpg")
    pending = await make_image(db_session, "p.jpg")
    pending.status = "pending"
    await db_session.flush()
    service = make_service(db_session)
    await service.mark_flagged(str(flagged.id), "other")
    await service.record_save(str(flagged.id))
    await service.record_save(str(pending.id))
    assert (await service.get_saves(str(flagged.id))).count == 1
    assert (await service.get_saves(str(pending.id))).count == 1
