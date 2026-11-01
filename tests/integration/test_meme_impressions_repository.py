from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError
import uuid

from repository.meme_impressions import MemeImpressionRepository
from repository.popularity import PopularityRepository
from Storage.models import Image, MemeImpression
from tests.integration.test_popularity_repository import make_image


@pytest.mark.asyncio(loop_scope="session")
async def test_summary_of_unshown_image_is_zero(db_session):
    img = await make_image(db_session, "a.jpg")
    assert await MemeImpressionRepository(db_session).summary(img.id, 30) == (0, 0, None)


@pytest.mark.asyncio(loop_scope="session")
async def test_record_many_assigns_ranks_in_order(db_session):
    a, b, c = [await make_image(db_session, f"{n}.jpg") for n in "abc"]
    await MemeImpressionRepository(db_session).record_many("search", [a.id, b.id, c.id])
    rows = (await db_session.execute(
        select(MemeImpression.image_id, MemeImpression.rank, MemeImpression.surface,
               MemeImpression.search_history_id)
        .where(MemeImpression.image_id.in_([a.id, b.id, c.id])).order_by(MemeImpression.rank))).all()
    assert [(r.image_id, r.rank, r.surface, r.search_history_id) for r in rows] == [
        (a.id, 1, "search", None), (b.id, 2, "search", None), (c.id, 3, "search", None)]


@pytest.mark.asyncio(loop_scope="session")
async def test_search_history_id_is_stored(db_session):
    img = await make_image(db_session, "a.jpg")
    sid = uuid.uuid4()
    await MemeImpressionRepository(db_session).record_many("reply", [img.id], sid)
    stored = (await db_session.execute(
        select(MemeImpression.search_history_id).where(MemeImpression.image_id == img.id))).scalar_one()
    assert stored == sid


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_list_is_a_noop(db_session):
    before = (await db_session.execute(select(MemeImpression.id))).all()
    await MemeImpressionRepository(db_session).record_many("search", [])
    assert (await db_session.execute(select(MemeImpression.id))).all() == before


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_surface_is_rejected(db_session):
    img = await make_image(db_session, "a.jpg")
    with pytest.raises(ValueError):
        await MemeImpressionRepository(db_session).record_many("trending", [img.id])
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                insert(MemeImpression).values(image_id=img.id, rank=1, surface="trending"))


@pytest.mark.asyncio(loop_scope="session")
async def test_window_boundary(db_session):
    img = await make_image(db_session, "a.jpg")
    now = datetime.now(timezone.utc)
    inside = now - timedelta(days=29, hours=23)
    outside = now - timedelta(days=31)
    for shown_at in (inside, outside):
        await db_session.execute(insert(MemeImpression).values(
            image_id=img.id, rank=1, surface="search", shown_at=shown_at))
    count, in_window, last = await MemeImpressionRepository(db_session).summary(img.id, 30)
    assert (count, in_window) == (2, 1)
    assert last == inside


@pytest.mark.asyncio(loop_scope="session")
async def test_loser_impressions_count_for_winner(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    await PopularityRepository(db_session).absorb(l.id, w.id, "cluster_review")
    repo = MemeImpressionRepository(db_session)
    await repo.record_many("search", [l.id])
    await repo.record_many("search", [w.id])
    assert (await repo.summary(w.id, 30))[0] == 2
    assert (await repo.summary(l.id, 30))[0] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_chain_credits_final_root(db_session):
    a, b, c = [await make_image(db_session, f"{n}.jpg") for n in "abc"]
    pop = PopularityRepository(db_session)
    await pop.absorb(c.id, b.id, "cluster_review")
    await pop.absorb(b.id, a.id, "cluster_review")
    repo = MemeImpressionRepository(db_session)
    await repo.record_many("search", [c.id])
    assert (await repo.summary(a.id, 30))[0] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_reversed_edge_hands_impressions_back(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    pop = PopularityRepository(db_session)
    await pop.absorb(l.id, w.id, "cluster_review")
    repo = MemeImpressionRepository(db_session)
    await repo.record_many("search", [l.id, w.id])
    assert (await repo.summary(w.id, 30))[0] == 2
    await pop.reverse_open_edge(l.id, "unflag")
    assert (await repo.summary(w.id, 30))[0] == 1
    assert (await repo.summary(l.id, 30))[0] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_deleted_image_rows_still_count(db_session):
    img = await make_image(db_session, "a.jpg")
    repo = MemeImpressionRepository(db_session)
    await repo.record_many("search", [img.id])
    image_id = img.id
    await db_session.execute(delete(Image).where(Image.id == image_id))
    assert (await repo.summary(image_id, 30))[0] == 1
