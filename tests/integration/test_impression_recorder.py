"""The impression recorder: one bulk write inside a savepoint that can never fail or poison the caller."""
import logging
import uuid

import pytest
from sqlalchemy import select, text

from Backend.app.services import impression_recorder
from Backend.app.services.impression_recorder import record_impressions
from repository.meme_impressions import MemeImpressionRepository
from Storage.models import MemeImpression
from tests.integration.test_popularity_repository import make_image


async def rows_for(session, *ids):
    return (await session.execute(
        select(MemeImpression.image_id, MemeImpression.rank, MemeImpression.surface,
               MemeImpression.search_history_id)
        .where(MemeImpression.image_id.in_(ids)).order_by(MemeImpression.rank))).all()


@pytest.mark.asyncio(loop_scope="session")
async def test_records_rows_with_surface_and_search_id(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    sid = uuid.uuid4()
    await record_impressions(db_session, "similar", [a.id, b.id], sid)
    assert [tuple(r) for r in await rows_for(db_session, a.id, b.id)] == [
        (a.id, 1, "similar", sid), (b.id, 2, "similar", sid)]


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_list_writes_nothing(db_session):
    before = (await db_session.execute(select(MemeImpression.id))).all()
    await record_impressions(db_session, "search", [])
    assert (await db_session.execute(select(MemeImpression.id))).all() == before


@pytest.mark.asyncio(loop_scope="session")
async def test_switch_off_writes_nothing(db_session, monkeypatch):
    img = await make_image(db_session, "a.jpg")
    monkeypatch.setattr(impression_recorder, "impressions_enabled", lambda: False)
    await record_impressions(db_session, "search", [img.id])
    assert await rows_for(db_session, img.id) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_failure_is_swallowed_and_session_stays_usable(db_session, monkeypatch, caplog):
    img = await make_image(db_session, "a.jpg")

    async def broken(self, surface, image_ids, search_history_id=None):
        await self.session.execute(text("SELECT * FROM table_that_does_not_exist"))

    monkeypatch.setattr(MemeImpressionRepository, "record_many", broken)
    with caplog.at_level(logging.WARNING):
        await record_impressions(db_session, "search", [img.id])

    assert "impression write failed: surface=search" in caplog.text
    await db_session.execute(select(1))
    monkeypatch.undo()
    await record_impressions(db_session, "search", [img.id])
    assert len(await rows_for(db_session, img.id)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_log_line_has_no_exception_message(db_session, monkeypatch, caplog):
    img = await make_image(db_session, "a.jpg")

    async def broken(self, surface, image_ids, search_history_id=None):
        raise RuntimeError("secret message text")

    monkeypatch.setattr(MemeImpressionRepository, "record_many", broken)
    with caplog.at_level(logging.DEBUG):
        await record_impressions(db_session, "reply", [img.id])

    assert "secret message text" not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio(loop_scope="session")
async def test_string_ids_are_accepted_and_a_malformed_id_is_swallowed(db_session, caplog):
    img = await make_image(db_session, "a.jpg")
    await record_impressions(db_session, "search", [str(img.id)])
    assert len(await rows_for(db_session, img.id)) == 1
    with caplog.at_level(logging.WARNING):
        await record_impressions(db_session, "search", ["img-1"])
    assert "impression write failed: surface=search error=ValueError" in caplog.text
