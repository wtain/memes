import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.image_service import ImageService
from repository.meme_impressions import MemeImpressionRepository, window_days
from repository.popularity import PopularityRepository
from tests.integration.test_popularity_repository import make_image


def make_service(session):
    return ImageService(ImageRepository(session), MagicMock(), MagicMock())


@pytest.mark.asyncio(loop_scope="session")
async def test_fresh_image_has_no_impressions(db_session):
    img = await make_image(db_session, "a.jpg")
    result = await make_service(db_session).get_impressions(str(img.id))
    assert result.imageId == str(img.id)
    assert (result.count, result.countWindow, result.lastShownAt) == (0, 0, None)
    assert result.windowDays == window_days()


@pytest.mark.asyncio(loop_scope="session")
async def test_recorded_impressions_are_counted(db_session):
    img = await make_image(db_session, "a.jpg")
    repo = MemeImpressionRepository(db_session)
    await repo.record_many("search", [img.id])
    await repo.record_many("reply", [img.id])
    result = await make_service(db_session).get_impressions(str(img.id))
    assert (result.count, result.countWindow) == (2, 2) and result.lastShownAt is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_loser_impressions_show_on_winner(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    await PopularityRepository(db_session).absorb(l.id, w.id, "cluster_review")
    await MemeImpressionRepository(db_session).record_many("search", [l.id])
    assert (await make_service(db_session).get_impressions(str(w.id))).count == 1


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("bad", ["not-a-uuid", ""])
async def test_unparsable_id_is_404(db_session, bad):
    with pytest.raises(HTTPException) as exc:
        await make_service(db_session).get_impressions(bad)
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_id_is_404(db_session):
    with pytest.raises(HTTPException) as exc:
        await make_service(db_session).get_impressions(str(uuid.uuid4()))
    assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_flagged_and_non_active_images_can_be_read(db_session):
    flagged = await make_image(db_session, "f.jpg")
    pending = await make_image(db_session, "p.jpg")
    pending.status = "pending"
    await db_session.flush()
    service = make_service(db_session)
    await service.mark_flagged(str(flagged.id), "other")
    assert (await service.get_impressions(str(flagged.id))).count == 0
    assert (await service.get_impressions(str(pending.id))).count == 0
