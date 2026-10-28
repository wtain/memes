from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.image_service import ImageService
from repository.popularity import PopularityRepository
from Storage.models import ImageExtras, PopularityAbsorption
from tests.integration.test_popularity_repository import edge_count, make_image, pop


def make_service(session):
    return ImageService(ImageRepository(session), MagicMock(), MagicMock())


async def flagged(session, image):
    return (await session.execute(
        select(ImageExtras.flagged).where(ImageExtras.image_id == image.id))).scalar_one_or_none()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("reason,source", [("duplicate_review", "cluster_review"), ("agent_review", "agent_review")])
async def test_flag_with_duplicate_of_raises_the_winner_and_zeroes_the_loser(db_session, reason, source):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    await make_service(db_session).mark_flagged(str(l.id), reason, str(w.id))
    assert (await pop(db_session, w), await pop(db_session, l)) == (2, 0)
    assert await flagged(db_session, l) is True
    edge = (await db_session.execute(select(PopularityAbsorption))).scalar_one()
    assert edge.source == source


@pytest.mark.asyncio(loop_scope="session")
async def test_flag_without_duplicate_of_changes_no_popularity(db_session):
    l = await make_image(db_session, "l.jpg")
    await make_service(db_session).mark_flagged(str(l.id), "duplicate_review")
    assert await pop(db_session, l) == 1
    assert await edge_count(db_session) == 0
    assert await flagged(db_session, l) is True


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("reason", ["other", None])
async def test_duplicate_of_with_another_or_no_reason_is_422_and_writes_nothing(db_session, reason):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    with pytest.raises(HTTPException) as exc:
        await make_service(db_session).mark_flagged(str(l.id), reason, str(w.id))
    assert exc.value.status_code == 422
    assert await flagged(db_session, l) is None
    assert await edge_count(db_session) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_duplicate_of_not_a_uuid_or_equal_to_the_image_is_422(db_session):
    l = await make_image(db_session, "l.jpg")
    service = make_service(db_session)
    for bad in ("not-a-uuid", str(l.id)):
        with pytest.raises(HTTPException) as exc:
            await service.mark_flagged(str(l.id), "duplicate_review", bad)
        assert exc.value.status_code == 422
    assert await flagged(db_session, l) is None
    assert await edge_count(db_session) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_unflag_restores_both_and_records_reason_unflag(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    service = make_service(db_session)
    await service.mark_flagged(str(l.id), "duplicate_review", str(w.id))
    await service.unmark_flagged(str(l.id))
    assert (await pop(db_session, w), await pop(db_session, l)) == (1, 1)
    assert await flagged(db_session, l) is False
    reverse = (await db_session.execute(
        select(PopularityAbsorption).where(PopularityAbsorption.kind == "reverse"))).scalar_one()
    assert reverse.reason == "unflag"


@pytest.mark.asyncio(loop_scope="session")
async def test_unflag_without_an_edge_is_a_no_op(db_session):
    l = await make_image(db_session, "l.jpg")
    await make_service(db_session).unmark_flagged(str(l.id))
    assert await pop(db_session, l) == 1
    assert await edge_count(db_session) == 0
    assert await flagged(db_session, l) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_reflag_with_the_same_winner_is_idempotent(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    service = make_service(db_session)
    await service.mark_flagged(str(l.id), "duplicate_review", str(w.id))
    await service.mark_flagged(str(l.id), "duplicate_review", str(w.id))
    assert (await pop(db_session, w), await pop(db_session, l)) == (2, 0)
    assert await edge_count(db_session) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_reflag_against_another_winner_keeps_the_flag_and_the_first_edge(db_session):
    w1, w2, l = [await make_image(db_session, n) for n in ("w1.jpg", "w2.jpg", "l.jpg")]
    service = make_service(db_session)
    await service.mark_flagged(str(l.id), "duplicate_review", str(w1.id))
    await service.mark_flagged(str(l.id), "agent_review", str(w2.id))
    assert await flagged(db_session, l) is True
    assert (await pop(db_session, w1), await pop(db_session, w2), await pop(db_session, l)) == (2, 1, 0)
    assert await edge_count(db_session) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_a_popularity_error_does_not_fail_the_flag(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")

    async def boom(self, *a, **k):
        raise RuntimeError("kaput")

    with patch.object(PopularityRepository, "absorb", boom):
        await make_service(db_session).mark_flagged(str(l.id), "duplicate_review", str(w.id))
    assert await flagged(db_session, l) is True
    assert await pop(db_session, w) == 1
    await db_session.execute(select(1))
