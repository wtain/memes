import logging
import uuid

import pytest
from sqlalchemy import select, text

from repository.popularity import PopularityRepository, WinnerGoneError, guarded
from tests.integration.test_popularity_repository import copies, edge_count, make_image, make_run, pop


@pytest.mark.asyncio(loop_scope="session")
async def test_guarded_returns_the_value_on_success(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    repo = PopularityRepository(db_session)
    row = await guarded(db_session, "x", lambda: repo.absorb(b.id, a.id, "cluster_review"))
    assert row is not None
    assert await pop(db_session, a) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_guarded_swallows_a_repository_error_and_keeps_earlier_work(db_session):
    a, b, c = [await make_image(db_session, n) for n in ("a.jpg", "b.jpg", "c.jpg")]
    repo = PopularityRepository(db_session)
    await repo.absorb(c.id, a.id, "cluster_review")
    result = await guarded(db_session, "x", lambda: repo.absorb(b.id, a.id, "bogus_source"))
    assert result is None
    assert await pop(db_session, a) == 2
    await db_session.execute(select(1))


@pytest.mark.asyncio(loop_scope="session")
async def test_guarded_rolls_back_a_database_error_without_poisoning_the_transaction(db_session):
    a = await make_image(db_session, "a.jpg")

    async def bad():
        await db_session.execute(text("UPDATE images SET popularity = -1"))

    assert await guarded(db_session, "x", bad) is None
    assert await pop(db_session, a) == 1
    await db_session.execute(select(1))


@pytest.mark.asyncio(loop_scope="session")
async def test_guarded_logs_the_label(db_session, caplog):
    async def boom():
        raise RuntimeError("kaput")

    with caplog.at_level(logging.WARNING):
        assert await guarded(db_session, "my-label", boom) is None
    assert any(r.levelno == logging.WARNING and "my-label" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio(loop_scope="session")
async def test_reverse_open_edge_returns_none_without_an_edge(db_session):
    a = await make_image(db_session, "a.jpg")
    assert await PopularityRepository(db_session).reverse_open_edge(a.id, "abort") is None


@pytest.mark.asyncio(loop_scope="session")
async def test_reverse_open_edge_respects_the_sources_filter(db_session):
    a, b = await make_image(db_session, "a.jpg"), await make_image(db_session, "b.jpg")
    repo = PopularityRepository(db_session)
    await repo.absorb(b.id, a.id, "cluster_review")
    assert await repo.reverse_open_edge(b.id, "abort", sources={"ingest_tier_a"}) is None
    assert await pop(db_session, a) == 2
    row = await repo.reverse_open_edge(b.id, "abort", sources={"cluster_review"})
    assert row is not None
    assert (await pop(db_session, a), await pop(db_session, b)) == (1, 1)


@pytest.mark.asyncio(loop_scope="session")
async def test_absorb_into_a_winner_with_no_image_row_raises_winner_gone(db_session):
    b = await make_image(db_session, "b.jpg")
    with pytest.raises(WinnerGoneError):
        await PopularityRepository(db_session).absorb(b.id, uuid.uuid4(), "cluster_review")
    assert await edge_count(db_session) == 0
    assert await pop(db_session, b) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_dropped_copies_into_a_winner_with_no_image_row_raise_winner_gone(db_session):
    run = await make_run(db_session)
    with pytest.raises(WinnerGoneError):
        await PopularityRepository(db_session).record_dropped_copies(uuid.uuid4(), copies("x"), run)
    assert await edge_count(db_session) == 0
