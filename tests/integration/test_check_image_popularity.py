import pytest
from sqlalchemy import select, update

from batch.check_image_popularity import run
from repository.popularity import PopularityRepository
from Storage.models import Image


async def make_image(session, filename):
    img = Image(filename=filename, status="active")
    session.add(img)
    await session.flush()
    return img


async def pop(session, image):
    return (await session.execute(select(Image.popularity).where(Image.id == image.id))).scalar_one()


async def make_drifted(session):
    a, b = await make_image(session, "a.jpg"), await make_image(session, "b.jpg")
    await PopularityRepository(session).absorb(b.id, a.id, "cluster_review")
    await session.execute(update(Image).where(Image.id == a.id).values(popularity=7))
    return a


@pytest.mark.asyncio(loop_scope="session")
async def test_run_without_fix_reports_and_changes_nothing(db_session):
    a = await make_drifted(db_session)
    drifts = await run(db_session, fix=False)
    assert [(d.image_id, d.cached, d.expected) for d in drifts] == [(a.id, 7, 2)]
    assert await pop(db_session, a) == 7


@pytest.mark.asyncio(loop_scope="session")
async def test_run_with_fix_rewrites_the_cache(db_session):
    a = await make_drifted(db_session)
    drifts = await run(db_session, fix=True)
    assert len(drifts) == 1
    assert await pop(db_session, a) == 2
    assert await run(db_session, fix=True) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_run_is_clean_on_a_consistent_database(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    await PopularityRepository(db_session).absorb(b.id, a.id, "cluster_review")
    assert await run(db_session, fix=False) == []
