import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.image_service import ImageService
from repository.popularity import PopularityRepository
from Storage.models import Image
from tests.integration.test_popularity_repository import edge_count, make_image


def make_service(session):
    return ImageService(ImageRepository(session), MagicMock(), MagicMock())


def shape(result):
    return result.popularity, result.status.value, [(r.source, r.count) for r in result.breakdown]


async def snapshot(session):
    pops = (await session.execute(select(Image.id, Image.popularity).order_by(Image.id))).all()
    return await edge_count(session), [tuple(r) for r in pops]


@pytest.mark.asyncio(loop_scope="session")
async def test_fresh_image_is_normal_with_own_row(db_session):
    img = await make_image(db_session, "a.jpg")
    before = await snapshot(db_session)
    result = await make_service(db_session).get_popularity(str(img.id))
    assert shape(result) == (1, "normal", [("own", 1)])
    assert await snapshot(db_session) == before


@pytest.mark.asyncio(loop_scope="session")
async def test_root_with_two_losers_from_different_sources(db_session):
    w = await make_image(db_session, "w.jpg")
    l1 = await make_image(db_session, "l1.jpg")
    l2 = await make_image(db_session, "l2.jpg")
    repo = PopularityRepository(db_session)
    await repo.absorb(l1.id, w.id, "cluster_review")
    await repo.absorb(l2.id, w.id, "agent_review")
    before = await snapshot(db_session)
    result = await make_service(db_session).get_popularity(str(w.id))
    assert shape(result) == (3, "normal", [("own", 1), ("agent_review", 1), ("cluster_review", 1)])
    assert sum(r.count for r in result.breakdown) == result.popularity
    assert await snapshot(db_session) == before


@pytest.mark.asyncio(loop_scope="session")
async def test_loser_is_discarded_with_no_rows(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    await PopularityRepository(db_session).absorb(l.id, w.id, "cluster_review")
    before = await snapshot(db_session)
    result = await make_service(db_session).get_popularity(str(l.id))
    assert shape(result) == (0, "discarded", [])
    assert await snapshot(db_session) == before


@pytest.mark.asyncio(loop_scope="session")
async def test_flagged_loser_is_discarded_not_flagged(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    service = make_service(db_session)
    await service.mark_flagged(str(l.id), "duplicate_review", str(w.id))
    before = await snapshot(db_session)
    assert shape(await service.get_popularity(str(l.id))) == (0, "discarded", [])
    assert await snapshot(db_session) == before


@pytest.mark.asyncio(loop_scope="session")
async def test_flagged_root_without_edge_is_flagged_with_its_number(db_session):
    img = await make_image(db_session, "a.jpg")
    service = make_service(db_session)
    await service.mark_flagged(str(img.id), "other")
    before = await snapshot(db_session)
    assert shape(await service.get_popularity(str(img.id))) == (1, "flagged", [("own", 1)])
    assert await snapshot(db_session) == before


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_and_malformed_ids_are_404(db_session):
    service = make_service(db_session)
    for bad in (str(uuid.uuid4()), "not-a-uuid"):
        with pytest.raises(HTTPException) as exc:
            await service.get_popularity(bad)
        assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_drifted_cache_number_from_cache_rows_from_log(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    await PopularityRepository(db_session).absorb(l.id, w.id, "cluster_review")
    await db_session.execute(update(Image).where(Image.id == w.id).values(popularity=9))
    await db_session.flush()
    before = await snapshot(db_session)
    result = await make_service(db_session).get_popularity(str(w.id))
    assert shape(result) == (9, "normal", [("own", 1), ("cluster_review", 1)])
    assert await snapshot(db_session) == before
