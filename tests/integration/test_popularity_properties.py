import asyncio
import random
import uuid

import pytest
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repository.popularity import (AlreadyDiscardedError, CycleError, DroppedCopy,
                                   PopularityRepository)
from Storage.models import Image, PopularityAbsorption

from tests.integration.test_popularity_repository import make_image, make_run, pop

SOURCES = ["cluster_review", "ingest_tier_a", "agent_review", "recut"]
REASONS = ["correction"]
STEPS = 60
IMAGES = 8


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("seed", range(25))
async def test_random_absorb_and_reverse_sequences_never_drift(db_session, seed):
    rng = random.Random(seed)
    repo = PopularityRepository(db_session)
    ids = [(await make_image(db_session, f"r{seed}-{n}.jpg")).id for n in range(IMAGES)]
    run = await make_run(db_session)
    log = []
    counter = 0

    for step in range(STEPS):
        action = rng.choice(["absorb", "absorb", "dropped", "reverse", "delete"])
        short = lambda i: str(i)[:8]
        try:
            if action == "absorb":
                loser, winner, source = rng.choice(ids), rng.choice(ids), rng.choice(SOURCES)
                log.append(f"{step}: absorb loser={short(loser)} winner={short(winner)} source={source}")
                try:
                    await repo.absorb(loser, winner, source)
                except (CycleError, AlreadyDiscardedError):
                    pass
                except ValueError:  # loser row deleted earlier
                    pass
            elif action == "dropped":
                winner = rng.choice(ids)
                counter += 1
                n = rng.randint(1, 3)
                dropped = [DroppedCopy(f"d{counter}-{k}", f"h{seed}-{counter}-{k}", f"/dest/{counter}")
                           for k in range(n)]
                log.append(f"{step}: dropped winner={short(winner)} count={n}")
                await repo.record_dropped_copies(winner, dropped, run)
            elif action == "reverse":
                open_rows = (await db_session.execute(
                    select(PopularityAbsorption.id).where(
                        PopularityAbsorption.kind == "absorb",
                        PopularityAbsorption.reversed_by_id.is_(None),
                    ))).scalars().all()
                if not open_rows:
                    log.append(f"{step}: reverse (no open rows)")
                    continue
                target = rng.choice(sorted(open_rows))
                log.append(f"{step}: reverse {short(target)}")
                try:
                    await repo.reverse(target, rng.choice(REASONS))
                except ValueError:
                    pass
            else:
                victim = rng.choice(ids)
                log.append(f"{step}: delete image {short(victim)}")
                await db_session.execute(delete(Image).where(Image.id == victim))
        except Exception:
            print("\n".join(log))
            raise

        drift = await repo.verify()
        assert drift == [], f"seed {seed} drift after step {step}: {drift}\n" + "\n".join(log)
        for image_id, popularity in (await db_session.execute(
                select(Image.id, Image.popularity).where(Image.id.in_(ids)))).all():
            if await repo._open_edge(image_id) is None:
                assert popularity >= 1, (
                    f"seed {seed} step {step}: {image_id} is not discarded but has popularity {popularity}\n"
                    + "\n".join(log))


@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_absorbs_in_one_tree_lose_no_update(db_engine):
    """Commits for real on ocrdb_test (no db_session rollback); cleans up everything it created."""
    tag = uuid.uuid4().hex[:8]
    ids = []
    try:
        async with AsyncSession(db_engine, expire_on_commit=False) as s:
            imgs = [await make_image(s, f"conc-{tag}-{n}.jpg") for n in "ABCD"]
            ids = [i.id for i in imgs]
            await s.commit()
        a, b, c, d = ids

        async def run(loser, winner):
            async with AsyncSession(db_engine, expire_on_commit=False) as s:
                await PopularityRepository(s).absorb(loser, winner, "cluster_review")
                await s.commit()

        await asyncio.gather(run(b, a), run(c, a), run(a, d))

        async with AsyncSession(db_engine, expire_on_commit=False) as s:
            repo = PopularityRepository(s)
            assert await repo.verify(ids) == []
            assert await pop(s, imgs[3]) == 4
            assert [await pop(s, i) for i in imgs[:3]] == [0, 0, 0]
    finally:
        async with AsyncSession(db_engine) as s:
            if ids:
                await s.execute(delete(PopularityAbsorption).where(or_(
                    PopularityAbsorption.loser_image_id.in_(ids),
                    PopularityAbsorption.winner_image_id.in_(ids))))
                await s.execute(delete(Image).where(Image.id.in_(ids)))
            await s.commit()
