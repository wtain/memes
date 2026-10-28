"""ingest_abort returns popularity to surviving winners (epic meme-popularity, P2)."""
from unittest.mock import patch

import pytest
from sqlalchemy import select

from batch.ingest_abort import run
from repository.popularity import DroppedCopy, PopularityRepository
from Storage.models import Image, PopularityAbsorption
from tests.integration.test_ingest_abort import _make_image, _make_run
from tests.integration.test_popularity_repository import make_image, pop


def _dirs(tmp_path):
    base, inbox = tmp_path / "base", tmp_path / "inbox"
    base.mkdir()
    inbox.mkdir()
    return str(inbox), str(base)


async def _reverses(session):
    return (await session.execute(
        select(PopularityAbsorption).where(PopularityAbsorption.kind == "reverse")
    )).scalars().all()


@pytest.mark.asyncio(loop_scope="session")
async def test_abort_returns_the_surviving_winners_popularity(db_session, tmp_path):
    inbox, base = _dirs(tmp_path)
    batch_id = await _make_run(db_session)
    w = await make_image(db_session, "w.jpg")
    r_id = await _make_image(db_session, "rejected", batch_id, "r.jpg")
    await PopularityRepository(db_session).absorb(r_id, w.id, "ingest_tier_a", batch_id)
    assert await pop(db_session, w) == 2

    metrics = await run(db_session, inbox, base, batch_id)

    assert await pop(db_session, w) == 1
    reverses = await _reverses(db_session)
    assert len(reverses) == 1 and reverses[0].reason == "abort"
    assert await db_session.get(Image, r_id) is None
    assert metrics.counters_dict()["popularity_reversed"] == 1
    assert await PopularityRepository(db_session).verify([w.id]) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_a_chain_inside_the_batch_gives_the_survivor_back_its_count(db_session, tmp_path):
    inbox, base = _dirs(tmp_path)
    batch_id = await _make_run(db_session)
    w = await make_image(db_session, "w.jpg")
    b_id = await _make_image(db_session, "pending", batch_id, "b.jpg")
    c_id = await _make_image(db_session, "pending", batch_id, "c.jpg")
    repo = PopularityRepository(db_session)
    await repo.absorb(c_id, b_id, "ingest_tier_a", batch_id)
    await repo.absorb(b_id, w.id, "ingest_tier_a", batch_id)
    assert await pop(db_session, w) == 3

    metrics = await run(db_session, inbox, base, batch_id)

    assert await pop(db_session, w) == 1
    reverses = await _reverses(db_session)
    assert len(reverses) == 1 and reverses[0].loser_image_id == b_id
    assert metrics.counters_dict()["popularity_reversed"] == 1
    assert await repo.verify([w.id]) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_edges_between_aborted_images_are_left_orphaned(db_session, tmp_path):
    inbox, base = _dirs(tmp_path)
    batch_id = await _make_run(db_session)
    b_id = await _make_image(db_session, "pending", batch_id, "b.jpg")
    c_id = await _make_image(db_session, "pending", batch_id, "c.jpg")
    await PopularityRepository(db_session).absorb(c_id, b_id, "ingest_tier_a", batch_id)

    metrics = await run(db_session, inbox, base, batch_id)

    assert await _reverses(db_session) == []
    assert "popularity_reversed" not in metrics.counters_dict()
    assert "popularity_skipped" not in metrics.counters_dict()


@pytest.mark.asyncio(loop_scope="session")
async def test_hash_copies_recorded_for_the_batch_stay_counted(db_session, tmp_path):
    inbox, base = _dirs(tmp_path)
    batch_id = await _make_run(db_session)
    w = await make_image(db_session, "w.jpg")
    repo = PopularityRepository(db_session)
    await repo.record_dropped_copies(w.id, [DroppedCopy("x.jpg", "hash-x", "inbox")], batch_id)
    assert await pop(db_session, w) == 2
    await _make_image(db_session, "pending", batch_id, "p.jpg")

    await run(db_session, inbox, base, batch_id)

    assert await pop(db_session, w) == 2
    assert await _reverses(db_session) == []
    assert await repo.verify([w.id]) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_a_popularity_error_does_not_stop_the_abort(db_session, tmp_path):
    inbox, base = _dirs(tmp_path)
    batch_id = await _make_run(db_session)
    w = await make_image(db_session, "w.jpg")
    r_id = await _make_image(db_session, "rejected", batch_id, "r.jpg")
    await PopularityRepository(db_session).absorb(r_id, w.id, "ingest_tier_a", batch_id)
    w_id = w.id  # the savepoint rollback expires ORM instances
    original = PopularityRepository.reverse

    async def write_then_fail(self, absorb_id, reason):
        await original(self, absorb_id, reason)  # writes the reverse row and moves the counts
        raise RuntimeError("boom")

    with patch.object(PopularityRepository, "reverse", write_then_fail):
        metrics = await run(db_session, inbox, base, batch_id)

    assert await db_session.get(Image, r_id) is None
    assert metrics.counters_dict()["popularity_skipped"] >= 1
    assert metrics.counters_dict()["unregistered"] == 1
    # the savepoint discarded the partial write: no reverse row, the winner still counts the copy
    assert await _reverses(db_session) == []
    assert (await db_session.execute(select(Image.popularity).where(Image.id == w_id))).scalar_one() == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_abort_with_nothing_open_changes_nothing(db_session, tmp_path):
    inbox, base = _dirs(tmp_path)
    batch_id = await _make_run(db_session)
    w = await make_image(db_session, "w.jpg")
    await _make_image(db_session, "pending", batch_id, "p.jpg")

    metrics = await run(db_session, inbox, base, batch_id)

    assert await pop(db_session, w) == 1
    assert await _reverses(db_session) == []
    assert "popularity_reversed" not in metrics.counters_dict()
    assert await PopularityRepository(db_session).verify([w.id]) == []
