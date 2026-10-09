"""Real two-session tests for the profile row lock (P5c final fix wave). They commit data, so every test
removes the images it created in a `finally`."""
import asyncio
import time
import uuid

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from repository.meme_profiles import MemeProfileRepository, ProfileData, ProfileMeta
from Storage.models import Image, MemeProfile, MemeProfileValue

TAXONOMY = 2


def _meta(human=False) -> ProfileMeta:
    return ProfileMeta(TAXONOMY, "human" if human else "qwen2", "p", "f" * 64, human)


def _data(situation, key, source) -> ProfileData:
    return ProfileData(situation, None, None, {"emotion": [{"key": key, "confidence": 1.0, "source": source}]})


async def _wait_until_blocked(engine, pid: int) -> None:
    """Poll pg_locks (bounded) until the backend `pid` waits for a lock another transaction holds."""
    deadline = time.monotonic() + 10
    async with engine.connect() as conn:
        while time.monotonic() < deadline:
            waiting = (await conn.execute(
                text("SELECT count(*) FROM pg_locks WHERE pid = :pid AND NOT granted"), {"pid": pid})).scalar_one()
            if waiting:
                return
            await asyncio.sleep(0.02)
    raise AssertionError("the second writer never blocked on the first one's lock")


async def _final_state(engine, image_id):
    async with AsyncSession(engine) as session:
        row = (await session.execute(select(MemeProfile).where(MemeProfile.image_id == image_id))).scalar_one()
        values = (await session.execute(
            select(MemeProfileValue.value_key, MemeProfileValue.source).where(MemeProfileValue.image_id == image_id)
        )).all()
        return row.human_verified, row.situation_text, sorted(values)


async def _run_pair(db_engine, first_writer, second_writer):
    """first_writer runs in an open transaction A, second_writer in B starts and must block; then A commits.
    Returns both results and the final committed state; the image is always removed afterwards."""
    async with AsyncSession(db_engine, expire_on_commit=False) as setup:
        image = Image(filename=f"{uuid.uuid4()}.jpg", status="active")
        setup.add(image)
        await setup.commit()
        image_id = image.id
    try:
        async with AsyncSession(db_engine) as a, AsyncSession(db_engine) as b:
            result_a = await first_writer(MemeProfileRepository(a), image_id)
            await a.flush()
            b_pid = (await b.execute(select(func.pg_backend_pid()))).scalar_one()

            async def second():
                result = await second_writer(MemeProfileRepository(b), image_id)
                await b.commit()
                return result

            task = asyncio.create_task(second())
            try:
                await _wait_until_blocked(db_engine, b_pid)
                assert not task.done()
                await a.commit()
                result_b = await asyncio.wait_for(task, timeout=10)
            finally:
                if not task.done():
                    await a.rollback()
                    await asyncio.gather(task, return_exceptions=True)
        return result_a, result_b, await _final_state(db_engine, image_id)
    finally:
        async with AsyncSession(db_engine) as cleanup:
            await cleanup.execute(delete(Image).where(Image.id == image_id))
            await cleanup.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_curator_save_waits_for_an_uncommitted_batch_write_and_then_wins(db_engine):
    async def batch(repo, image_id):
        return await repo.save_profile(image_id, _data("model situation", "pride", "model"), _meta())

    async def curator(repo, image_id):
        await repo.save_human_profile(image_id, _data("human situation", "joy", "human"), _meta(human=True))

    saved_by_batch, _, state = await _run_pair(db_engine, batch, curator)

    assert saved_by_batch is True
    assert state == (True, "human situation", [("joy", "human")])


@pytest.mark.asyncio(loop_scope="session")
async def test_a_batch_write_waits_for_an_uncommitted_human_save_and_changes_nothing(db_engine):
    async def curator(repo, image_id):
        await repo.save_human_profile(image_id, _data("human situation", "joy", "human"), _meta(human=True))

    async def batch(repo, image_id):
        return await repo.save_profile(image_id, _data("model situation", "pride", "model"), _meta())

    _, saved_by_batch, state = await _run_pair(db_engine, curator, batch)

    assert saved_by_batch is False
    assert state == (True, "human situation", [("joy", "human")])


def test_the_profile_row_is_locked_for_update():
    import inspect
    from repository import meme_profiles
    source = inspect.getsource(meme_profiles.MemeProfileRepository._lock_profile)
    assert "with_for_update()" in source and "populate_existing" in source and "on_conflict_do_nothing" in source
