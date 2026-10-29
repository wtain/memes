import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.image_service import ImageService
from repository.popularity import PopularityRepository
from Storage.models import Image
from tests.integration.test_popularity_repository import edge_count, make_image, pop


def make_service(session):
    return ImageService(ImageRepository(session), MagicMock(), MagicMock())


def shape(result):
    return result.popularity, result.status.value, [(r.source, r.count) for r in result.breakdown]


@pytest.mark.asyncio(loop_scope="session")
async def test_set_on_active_root_returns_number_and_delta(db_session):
    img = await make_image(db_session, "a.jpg")
    result = await make_service(db_session).set_popularity(str(img.id), 40)
    assert shape(result) == (40, "normal", [("own", 1), ("adjustment", 39)])
    assert await pop(db_session, img) == 40
    assert await PopularityRepository(db_session).verify([img.id]) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_second_identical_call_changes_nothing(db_session):
    img = await make_image(db_session, "a.jpg")
    service = make_service(db_session)
    first = await service.set_popularity(str(img.id), 40)
    edges = await edge_count(db_session)
    second = await service.set_popularity(str(img.id), 40)
    assert shape(second) == shape(first)
    assert await edge_count(db_session) == edges


@pytest.mark.asyncio(loop_scope="session")
async def test_lowering_records_negative_adjustment_last(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    await PopularityRepository(db_session).absorb(l.id, w.id, "cluster_review")
    result = await make_service(db_session).set_popularity(str(w.id), 1)
    assert shape(result) == (1, "normal", [("own", 1), ("cluster_review", 1), ("adjustment", -1)])


@pytest.mark.asyncio(loop_scope="session")
async def test_pending_image_is_409_and_writes_nothing(db_session):
    img = Image(filename="p.jpg", status="pending")
    db_session.add(img)
    await db_session.flush()
    edges = await edge_count(db_session)
    with pytest.raises(HTTPException) as exc:
        await make_service(db_session).set_popularity(str(img.id), 5)
    assert exc.value.status_code == 409
    assert await edge_count(db_session) == edges


@pytest.mark.asyncio(loop_scope="session")
async def test_loser_is_409_and_unchanged(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    await PopularityRepository(db_session).absorb(l.id, w.id, "cluster_review")
    edges = await edge_count(db_session)
    with pytest.raises(HTTPException) as exc:
        await make_service(db_session).set_popularity(str(l.id), 5)
    assert exc.value.status_code == 409
    assert await edge_count(db_session) == edges
    assert await pop(db_session, l) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_and_malformed_ids_are_404(db_session):
    service = make_service(db_session)
    unknown = uuid.uuid4()
    for bad in (str(unknown), "not-a-uuid", f"{{{unknown}}}", unknown.hex):
        with pytest.raises(HTTPException) as exc:
            await service.set_popularity(bad, 5)
        assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_flagged_active_image_can_be_set(db_session):
    img = await make_image(db_session, "f.jpg")
    service = make_service(db_session)
    await service.mark_flagged(str(img.id), "other")
    result = await service.set_popularity(str(img.id), 7)
    assert shape(result) == (7, "flagged", [("own", 1), ("adjustment", 6)])
    assert await PopularityRepository(db_session).verify([img.id]) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_nothing_commits_inside_the_request(db_session, monkeypatch):
    img = await make_image(db_session, "c.jpg")
    commit = AsyncMock()
    monkeypatch.setattr(db_session, "commit", commit)
    await make_service(db_session).set_popularity(str(img.id), 9)
    commit.assert_not_called()
