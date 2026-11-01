"""Search and similar record the images they return as impressions (epic meme-popularity, S1), best effort.
Real repositories on the rolled-back db_session; ImageService._record_history is stubbed because it opens its own
AsyncSessionLocal session outside the test transaction."""
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.image_service import ImageService
from repository.meme_impressions import MemeImpressionRepository
from Storage.models import Embedding, Image, ImageTag, MemeImpression, OCRLemma

_T0 = datetime(2026, 1, 1, 12, 0, 0)
_WORD = "quokkaberry"


def _vector(axis):
    values = [0.0] * 512
    values[axis] = 1.0
    return values


async def _image(db_session, minutes=0):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status="active", created_at=_T0 + timedelta(minutes=minutes))
    db_session.add(image)
    await db_session.flush()
    return image


async def _matching_images(db_session, count):
    images = [await _image(db_session, minutes=m) for m in range(count)]
    for image in images:
        db_session.add(OCRLemma(image_id=image.id, lemma=_WORD))
    await db_session.flush()
    return images


def _service(db_session, monkeypatch, history_id=None):
    monkeypatch.setattr(ImageService, "_record_history", AsyncMock(return_value=history_id))
    return ImageService(ImageRepository(db_session), AsyncMock(), AsyncMock())


async def _rows(db_session, ids):
    return (await db_session.execute(
        select(MemeImpression.image_id, MemeImpression.rank, MemeImpression.surface,
               MemeImpression.search_history_id)
        .where(MemeImpression.image_id.in_(ids)).order_by(MemeImpression.id))).all()


async def _count(db_session):
    return (await db_session.execute(select(func.count()).select_from(MemeImpression))).scalar_one()


@pytest.mark.asyncio(loop_scope="session")
async def test_search_records_one_row_per_returned_image_with_the_history_id(db_session, monkeypatch):
    images = await _matching_images(db_session, 3)
    sid = uuid.uuid4()
    page = await _service(db_session, monkeypatch, sid).search(
        q=_WORD, raw_facets=None, cursor=None, limit=20)

    returned = [uuid.UUID(item.id) for item in page.items]
    assert sorted(returned) == sorted(i.id for i in images)
    rows = await _rows(db_session, returned)
    assert [(r.image_id, r.rank, r.surface, r.search_history_id) for r in rows] == [
        (image_id, rank, "search", sid) for rank, image_id in enumerate(returned, 1)]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_show_more_page_adds_its_own_rows_with_page_local_ranks(db_session, monkeypatch):
    images = await _matching_images(db_session, 3)
    service = _service(db_session, monkeypatch)
    first = await service.search(q=_WORD, raw_facets=None, cursor=None, limit=2)
    second = await service.search(q=_WORD, raw_facets=None, cursor=first.nextCursor, limit=2)

    assert first.hasNext and len(second.items) == 1
    rows = await _rows(db_session, [i.id for i in images])
    assert [r.rank for r in rows] == [1, 2, 1]
    assert len({r.image_id for r in rows}) == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_an_empty_search_writes_nothing(db_session, monkeypatch):
    before = await _count(db_session)
    page = await _service(db_session, monkeypatch).search(
        q="nomatchzzyzx", raw_facets=None, cursor=None, limit=20)
    assert page.items == []
    assert await _count(db_session) == before


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_an_unfiltered_browse_listing_is_not_an_impression(db_session, monkeypatch, blank):
    images = await _matching_images(db_session, 2)
    page = await _service(db_session, monkeypatch).search(q=blank, raw_facets=None, cursor=None, limit=20)
    assert page.items
    assert await _rows(db_session, [i.id for i in images]) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_a_facet_only_search_is_recorded(db_session, monkeypatch):
    image = await _image(db_session)
    db_session.add(ImageTag(image_id=image.id, key="animal", value="quokka", source="rules"))
    await db_session.flush()
    page = await _service(db_session, monkeypatch).search(
        q=None, raw_facets="animal:quokka", cursor=None, limit=20)
    assert [i.id for i in page.items] == [str(image.id)]
    assert len(await _rows(db_session, [image.id])) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_similar_skips_the_source_image(db_session, monkeypatch):
    source, near, far = [await _image(db_session) for _ in range(3)]
    db_session.add_all([Embedding(image_id=source.id, embedding=_vector(0)),
                        Embedding(image_id=near.id, embedding=_vector(0)),
                        Embedding(image_id=far.id, embedding=_vector(1))])
    await db_session.flush()

    page = await _service(db_session, monkeypatch).get_similar(str(source.id), limit=10)

    returned = [uuid.UUID(item.id) for item in page.items if uuid.UUID(item.id) in {near.id, far.id}]
    rows = await _rows(db_session, [source.id, near.id, far.id])
    assert source.id not in {r.image_id for r in rows}
    assert [(r.image_id, r.surface, r.search_history_id) for r in rows
            if r.image_id in returned] == [(image_id, "similar", None) for image_id in returned]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failed_write_does_not_change_the_response(db_session, monkeypatch):
    await _matching_images(db_session, 3)
    expected = await _service(db_session, monkeypatch).search(q=_WORD, raw_facets=None, cursor=None, limit=20)
    service = _service(db_session, monkeypatch)

    async def broken(self, surface, image_ids, search_history_id=None):
        raise RuntimeError("write failed")

    monkeypatch.setattr(MemeImpressionRepository, "record_many", broken)
    page = await service.search(q=_WORD, raw_facets=None, cursor=None, limit=20)

    assert [i.id for i in page.items] == [i.id for i in expected.items]
    service._record_history.assert_awaited_once()


@pytest.mark.asyncio(loop_scope="session")
async def test_history_failure_still_records_impressions_with_a_null_search_id(db_session, monkeypatch):
    await _matching_images(db_session, 2)
    page = await _service(db_session, monkeypatch, None).search(q=_WORD, raw_facets=None, cursor=None, limit=20)
    rows = await _rows(db_session, [uuid.UUID(i.id) for i in page.items])
    assert len(rows) == 2 and {r.search_history_id for r in rows} == {None}
