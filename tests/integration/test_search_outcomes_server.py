import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import func, select

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services import outcome_recorder
from Backend.app.services.image_service import ImageService
from Storage.models import MemeSave, SearchNotFound
from tests.integration.test_popularity_repository import make_image

pytestmark = pytest.mark.asyncio(loop_scope="session")


def make_service(session):
    return ImageService(ImageRepository(session), MagicMock(), MagicMock())


async def count(session, model):
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_save_stores_the_search_id(db_session):
    img, sid = await make_image(db_session, "a.jpg"), uuid.uuid4()
    await make_service(db_session).record_save(str(img.id), str(sid))
    row = (await db_session.execute(select(MemeSave))).scalars().one()
    assert row.search_history_id == sid


async def test_save_without_a_search_id_is_unchanged(db_session):
    img = await make_image(db_session, "a.jpg")
    await make_service(db_session).record_save(str(img.id))
    assert (await db_session.execute(select(MemeSave))).scalars().one().search_history_id is None


async def test_malformed_search_id_is_ignored_and_the_save_counts(db_session):
    img = await make_image(db_session, "a.jpg")
    await make_service(db_session).record_save(str(img.id), "not-a-uuid")
    assert (await db_session.execute(select(MemeSave))).scalars().one().search_history_id is None


async def test_failed_link_still_records_the_save(db_session):
    img = await make_image(db_session, "a.jpg")
    with patch("repository.meme_saves.MemeSaveRepository.record",
               side_effect=[RuntimeError("column missing"), None]) as record:
        await make_service(db_session).record_save(str(img.id), str(uuid.uuid4()))
    assert record.call_count == 2 and record.call_args_list[1].args == (img.id,)


async def test_switch_off_drops_the_link(db_session):
    img = await make_image(db_session, "a.jpg")
    with patch("Backend.app.services.image_service.outcomes_enabled", return_value=False):
        await make_service(db_session).record_save(str(img.id), str(uuid.uuid4()))
    assert (await db_session.execute(select(MemeSave))).scalars().one().search_history_id is None


async def test_press_is_recorded_once(db_session):
    sid = uuid.uuid4()
    await outcome_recorder.record_not_found(db_session, str(sid))
    await outcome_recorder.record_not_found(db_session, str(sid))
    assert await count(db_session, SearchNotFound) == 1


async def test_press_for_an_unknown_id_is_stored_and_a_malformed_one_is_ignored(db_session):
    await outcome_recorder.record_not_found(db_session, str(uuid.uuid4()))
    await outcome_recorder.record_not_found(db_session, "not-a-uuid")
    assert await count(db_session, SearchNotFound) == 1


async def test_press_with_the_switch_off_writes_nothing(db_session):
    with patch("Backend.app.services.outcome_recorder.outcomes_enabled", return_value=False):
        await outcome_recorder.record_not_found(db_session, str(uuid.uuid4()))
    assert await count(db_session, SearchNotFound) == 0


async def test_a_failing_press_write_is_swallowed(db_session):
    with patch("repository.search_outcomes.SearchNotFoundRepository.record", side_effect=RuntimeError("boom")):
        await outcome_recorder.record_not_found(db_session, str(uuid.uuid4()))
    assert await count(db_session, SearchNotFound) == 0
