import uuid

import pytest
from sqlalchemy import func, select, update

from repository.popularity import AlreadyDiscardedError, CycleError, PopularityRepository
from Storage.models import Image, PopularityAbsorption


async def make_image(session, filename):
    img = Image(filename=filename, status="active")
    session.add(img)
    await session.flush()
    return img


async def pop(session, image):
    return (await session.execute(select(Image.popularity).where(Image.id == image.id))).scalar_one()


async def edge_count(session):
    return (await session.execute(select(func.count()).select_from(PopularityAbsorption))).scalar_one()


@pytest.mark.asyncio(loop_scope="session")
async def test_absorb_moves_the_loser_into_the_winner(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    row = await PopularityRepository(db_session).absorb(b.id, a.id, "cluster_review")
    assert await pop(db_session, a) == 2
    assert await pop(db_session, b) == 0
    assert row.weight == 1 and row.kind == "absorb" and row.loser_filename == "b.jpg"


@pytest.mark.asyncio(loop_scope="session")
async def test_chain_of_three(db_session):
    a, b, c = [await make_image(db_session, f"{n}.jpg") for n in "abc"]
    repo = PopularityRepository(db_session)
    await repo.absorb(b.id, a.id, "cluster_review")
    await repo.absorb(a.id, c.id, "cluster_review")
    assert (await pop(db_session, c), await pop(db_session, a), await pop(db_session, b)) == (3, 0, 0)


@pytest.mark.asyncio(loop_scope="session")
async def test_naming_a_discarded_image_credits_its_root(db_session):
    a, b, c, d = [await make_image(db_session, f"{n}.jpg") for n in "abcd"]
    repo = PopularityRepository(db_session)
    await repo.absorb(b.id, a.id, "cluster_review")
    await repo.absorb(a.id, c.id, "cluster_review")
    row = await repo.absorb(d.id, b.id, "cluster_review")
    assert await pop(db_session, c) == 4
    assert row.winner_image_id == b.id


@pytest.mark.asyncio(loop_scope="session")
async def test_recut_edge_gives_the_cut_exactly_the_sources_popularity(db_session):
    a, x, y, cut = [await make_image(db_session, f"{n}.jpg") for n in ("a", "x", "y", "cut")]
    repo = PopularityRepository(db_session)
    await repo.absorb(x.id, a.id, "cluster_review")
    await repo.absorb(y.id, a.id, "cluster_review")
    row = await repo.absorb(a.id, cut.id, "recut")
    assert row.weight == 0
    assert (await pop(db_session, cut), await pop(db_session, a)) == (3, 0)
    s, cut2 = await make_image(db_session, "s.jpg"), await make_image(db_session, "cut2.jpg")
    await repo.absorb(s.id, cut2.id, "recut")
    assert await pop(db_session, cut2) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_retry_returns_the_same_row_and_changes_nothing(db_session):
    a, b, c = [await make_image(db_session, f"{n}.jpg") for n in "abc"]
    repo = PopularityRepository(db_session)
    first = await repo.absorb(b.id, a.id, "cluster_review")
    again = await repo.absorb(b.id, a.id, "cluster_review")
    assert again.id == first.id
    assert await pop(db_session, a) == 2
    await repo.absorb(a.id, c.id, "cluster_review")
    third = await repo.absorb(b.id, a.id, "cluster_review")
    assert third.id == first.id
    assert (await pop(db_session, c), await pop(db_session, a)) == (3, 0)


@pytest.mark.asyncio(loop_scope="session")
async def test_absorbing_into_a_different_tree_raises_already_discarded(db_session):
    a, b, c = [await make_image(db_session, f"{n}.jpg") for n in "abc"]
    repo = PopularityRepository(db_session)
    await repo.absorb(b.id, a.id, "cluster_review")
    with pytest.raises(AlreadyDiscardedError) as exc:
        await repo.absorb(b.id, c.id, "cluster_review")
    assert exc.value.existing.winner_image_id == a.id
    assert (await pop(db_session, a), await pop(db_session, c)) == (2, 1)
    assert await edge_count(db_session) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_cycle_is_refused(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    repo = PopularityRepository(db_session)
    with pytest.raises(CycleError):
        await repo.absorb(a.id, a.id, "cluster_review")
    await repo.absorb(b.id, a.id, "cluster_review")
    with pytest.raises(CycleError):
        await repo.absorb(a.id, b.id, "cluster_review")
    assert await edge_count(db_session) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_ghost_winner_is_accepted(db_session):
    b = await make_image(db_session, "b.jpg")
    row = await PopularityRepository(db_session).absorb(b.id, uuid.uuid4(), "cluster_review")
    assert row.id is not None
    assert await pop(db_session, b) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_a_drifted_loser_cache_does_not_spread(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    await db_session.execute(update(Image).where(Image.id == b.id).values(popularity=50))
    await PopularityRepository(db_session).absorb(b.id, a.id, "cluster_review")
    assert await pop(db_session, a) == 2
