"""Hash dedup records dropped copies as popularity (epic meme-popularity, P2)."""
import datetime
import hashlib
import os

import pytest
from sqlalchemy import func, select

from batch.ingest_hash_dedup import plan_dropped_copies, run
from repository.batch_runs import BatchRunRepository
from repository.popularity import PopularityRepository
from Storage.models import Image, PopularityAbsorption

CONTENT = b"popularity-content"
CONTENT_HASH = hashlib.sha256(CONTENT).hexdigest()


def _write(path, name, content=CONTENT):
    with open(os.path.join(path, name), "wb") as f:
        f.write(content)


async def _setup(tmp_path, db_session):
    source, base = tmp_path / "source", tmp_path / "base"
    source.mkdir()
    base.mkdir()
    batch_id = await BatchRunRepository(db_session).create_run(
        kind="ingestion", trigger="manual", stage="hash_dedup")
    return source, base, batch_id


async def _active(db_session, name="active.jpg", **kw):
    image = Image(filename=name, content_hash=CONTENT_HASH, status="active", **kw)
    db_session.add(image)
    await db_session.flush()
    return image


async def _pop(db_session, image):
    await db_session.refresh(image)
    return image.popularity


@pytest.mark.asyncio(loop_scope="session")
async def test_three_identical_files_leave_the_keeper_at_three(tmp_path, db_session):
    source, base, batch_id = await _setup(tmp_path, db_session)
    for n in ("a.jpg", "b.jpg", "c.jpg"):
        _write(source, n)
    stats = await run(db_session, str(source), str(base), batch_id)
    keeper = (await db_session.execute(select(Image).where(Image.filename == "a.jpg"))).scalar_one()
    assert await _pop(db_session, keeper) == 3
    assert stats["popularity_copies_recorded"] == 2
    assert stats["popularity_skipped"] == 0
    assert await PopularityRepository(db_session).verify([keeper.id]) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_a_file_identical_to_an_active_image_raises_it_by_one(tmp_path, db_session):
    source, base, batch_id = await _setup(tmp_path, db_session)
    active = await _active(db_session)
    _write(source, "a.jpg")
    stats = await run(db_session, str(source), str(base), batch_id)
    assert await _pop(db_session, active) == 2
    assert stats["registered"] == 0
    assert stats["popularity_copies_recorded"] == 1
    pending = (await db_session.execute(
        select(func.count()).select_from(Image).where(Image.status == "pending"))).scalar_one()
    assert pending == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_in_batch_extras_of_an_active_match_all_count_for_the_active_image(tmp_path, db_session):
    source, base, batch_id = await _setup(tmp_path, db_session)
    active = await _active(db_session)
    _write(source, "a.jpg")
    _write(source, "b.jpg")
    await run(db_session, str(source), str(base), batch_id)
    assert await _pop(db_session, active) == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_two_actives_with_one_hash_pick_the_more_popular(tmp_path, db_session):
    source, base, batch_id = await _setup(tmp_path, db_session)
    low = await _active(db_session, "low.jpg")
    high = await _active(db_session, "high.jpg")
    high.popularity = 5
    await db_session.flush()
    _write(source, "a.jpg")
    await run(db_session, str(source), str(base), batch_id)
    assert await _pop(db_session, high) == 6
    assert await _pop(db_session, low) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_ties_go_to_the_earlier_created_at(tmp_path, db_session):
    source, base, batch_id = await _setup(tmp_path, db_session)
    now = datetime.datetime(2026, 1, 1)
    later = await _active(db_session, "later.jpg", created_at=now + datetime.timedelta(days=1))
    earlier = await _active(db_session, "earlier.jpg", created_at=now)
    _write(source, "a.jpg")
    await run(db_session, str(source), str(base), batch_id)
    assert await _pop(db_session, earlier) == 2
    assert await _pop(db_session, later) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_the_same_name_dropped_in_a_later_invocation_counts_again(tmp_path, db_session):
    source, base, batch_id = await _setup(tmp_path, db_session)
    active = await _active(db_session)
    _write(source, "a.jpg")
    await run(db_session, str(source), str(base), batch_id)
    assert await _pop(db_session, active) == 2
    os.remove(source / "duplicates" / "a.jpg")
    _write(source, "a.jpg")
    await run(db_session, str(source), str(base), batch_id)
    assert await _pop(db_session, active) == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_recording_twice_in_one_invocation_adds_nothing(tmp_path, db_session):
    source, base, batch_id = await _setup(tmp_path, db_session)
    image = await _active(db_session)
    plan = plan_dropped_copies({CONTENT_HASH: ["a.jpg", "b.jpg", "c.jpg"]}, "inv1")
    keeper, copies = plan[CONTENT_HASH]
    assert keeper == "a.jpg"
    assert [c.destination for c in copies] == ["inv1:b.jpg", "inv1:c.jpg"]
    repo = PopularityRepository(db_session)
    assert await repo.record_dropped_copies(image.id, copies, batch_id) == 2
    assert await repo.record_dropped_copies(image.id, copies, batch_id) == 0
    assert await _pop(db_session, image) == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_no_duplicates_means_no_popularity_calls(tmp_path, db_session):
    source, base, batch_id = await _setup(tmp_path, db_session)
    _write(source, "a.jpg", b"one")
    _write(source, "b.jpg", b"two")
    stats = await run(db_session, str(source), str(base), batch_id)
    assert stats["popularity_copies_recorded"] == 0
    assert stats["popularity_skipped"] == 0
    count = (await db_session.execute(select(func.count()).select_from(PopularityAbsorption))).scalar_one()
    assert count == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_a_popularity_error_does_not_fail_the_run(tmp_path, db_session, monkeypatch):
    async def boom(self, *a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(PopularityRepository, "record_dropped_copies", boom)
    source, base, batch_id = await _setup(tmp_path, db_session)
    for n in ("a.jpg", "b.jpg", "c.jpg"):
        _write(source, n)
    _write(source, "u.jpg", b"unique")
    stats = await run(db_session, str(source), str(base), batch_id)
    assert stats["registered"] == 2
    assert stats["popularity_copies_recorded"] == 0
    assert stats["popularity_skipped"] == 2
    assert set(os.listdir(base)) == {"a.jpg", "u.jpg"}
    assert (await db_session.execute(select(func.count()).select_from(Image))).scalar_one() == 2


async def _multi_survivor_run(tmp_path, db_session):
    source, base, batch_id = await _setup(tmp_path, db_session)
    active = await _active(db_session)  # CONTENT_HASH, matched by repost.jpg
    _write(base, "u1.jpg", b"already-in-base")  # forces u1.jpg to be renamed on registration
    _write(source, "u1.jpg", b"unique-one")
    _write(source, "dup_a.jpg", b"in-batch-dup")
    _write(source, "dup_b.jpg", b"in-batch-dup")
    _write(source, "dup_c.jpg", b"in-batch-dup")
    _write(source, "repost.jpg", CONTENT)
    _write(source, "u2.jpg", b"unique-two")
    stats = await run(db_session, str(source), str(base), batch_id)
    return active, stats


@pytest.mark.asyncio(loop_scope="session")
async def test_each_keeper_gets_its_own_extras_among_several_survivors(tmp_path, db_session):
    active, stats = await _multi_survivor_run(tmp_path, db_session)
    by_hash = {}
    for image in (await db_session.execute(select(Image).where(Image.status == "pending"))).scalars():
        by_hash[image.content_hash] = image
    assert len(by_hash) == 3
    keeper = by_hash[hashlib.sha256(b"in-batch-dup").hexdigest()]
    u1 = by_hash[hashlib.sha256(b"unique-one").hexdigest()]
    u2 = by_hash[hashlib.sha256(b"unique-two").hexdigest()]
    assert keeper.filename == "dup_a.jpg"
    assert u1.filename == "u1_1.jpg"  # renamed by the collision
    assert await _pop(db_session, keeper) == 3
    assert await _pop(db_session, u1) == 1
    assert await _pop(db_session, u2) == 1
    assert await _pop(db_session, active) == 2
    assert stats["popularity_copies_recorded"] == 3
    assert stats["popularity_skipped"] == 0
