import pytest
from sqlalchemy import delete, func, select

from repository.meme_saves import MemeSaveRepository
from repository.popularity import PopularityRepository
from Storage.models import Image, MemeSave
from tests.integration.test_popularity_repository import make_image


@pytest.mark.asyncio(loop_scope="session")
async def test_summary_of_unsaved_image_is_zero_none(db_session):
    img = await make_image(db_session, "a.jpg")
    assert await MemeSaveRepository(db_session).summary(img.id) == (0, None)


@pytest.mark.asyncio(loop_scope="session")
async def test_every_record_counts(db_session):
    img = await make_image(db_session, "a.jpg")
    repo = MemeSaveRepository(db_session)
    await repo.record(img.id)
    await repo.record(img.id)
    count, last = await repo.summary(img.id)
    newest = (await db_session.execute(
        select(func.max(MemeSave.created_at)).where(MemeSave.image_id == img.id))).scalar_one()
    assert count == 2
    assert last is not None and last == newest


@pytest.mark.asyncio(loop_scope="session")
async def test_loser_saves_count_for_winner(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    await PopularityRepository(db_session).absorb(l.id, w.id, "cluster_review")
    repo = MemeSaveRepository(db_session)
    await repo.record(l.id)
    await repo.record(w.id)
    assert (await repo.summary(w.id))[0] == 2
    assert (await repo.summary(l.id))[0] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_chain_credits_final_root(db_session):
    a, b, c = [await make_image(db_session, f"{n}.jpg") for n in "abc"]
    pop = PopularityRepository(db_session)
    await pop.absorb(c.id, b.id, "cluster_review")
    await pop.absorb(b.id, a.id, "cluster_review")
    repo = MemeSaveRepository(db_session)
    await repo.record(c.id)
    assert (await repo.summary(a.id))[0] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_reversed_edge_hands_saves_back(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    pop = PopularityRepository(db_session)
    await pop.absorb(l.id, w.id, "cluster_review")
    repo = MemeSaveRepository(db_session)
    await repo.record(l.id)
    await repo.record(w.id)
    assert (await repo.summary(w.id))[0] == 2
    await pop.reverse_open_edge(l.id, "unflag")
    assert (await repo.summary(w.id))[0] == 1
    assert (await repo.summary(l.id))[0] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_deleted_image_saves_still_count(db_session):
    img = await make_image(db_session, "a.jpg")
    repo = MemeSaveRepository(db_session)
    await repo.record(img.id)
    image_id = img.id
    await db_session.execute(delete(Image).where(Image.id == image_id))
    assert (await repo.summary(image_id))[0] == 1
