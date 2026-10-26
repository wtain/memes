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
MIN_SUCCESSES = 50


async def run_sequence(session, seed, tag=""):
    """Run one seeded random sequence; returns (step log, counts of successful operations by kind).
    Everything that influences a choice is run-independent (no uuid ordering), so a seed replays exactly."""
    rng = random.Random(seed)
    repo = PopularityRepository(session)
    names = [f"{tag}r{seed}-{n}.jpg" for n in range(IMAGES)]
    ids = [(await make_image(session, name)).id for name in names]
    index = {i: n for n, i in enumerate(ids)}
    deleted = set()
    run = await make_run(session)
    log = []
    counts = {"absorb": 0, "dropped": 0, "reverse": 0, "delete": 0}
    counter = 0

    for step in range(STEPS):
        action = rng.choice(["absorb", "absorb", "absorb", "dropped", "reverse", "reverse", "delete"])
        if action == "absorb":
            loser, winner, source = rng.choice(ids), rng.choice(ids), rng.choice(SOURCES)
            log.append(f"{step}: absorb loser=#{index[loser]} winner=#{index[winner]} source={source}")
            try:
                await repo.absorb(loser, winner, source)
                counts["absorb"] += 1
            except (CycleError, AlreadyDiscardedError):
                pass
            except ValueError:
                if loser not in deleted:
                    raise
        elif action == "dropped":
            winner = rng.choice(ids)
            counter += 1
            n = rng.randint(1, 3)
            dropped = [DroppedCopy(f"{tag}d{seed}-{counter}-{k}", f"h{seed}-{counter}-{k}", f"/dest/{counter}")
                       for k in range(n)]
            log.append(f"{step}: dropped winner=#{index[winner]} count={n}")
            counts["dropped"] += await repo.record_dropped_copies(winner, dropped, run) and 1
        elif action == "reverse":
            open_rows = (await session.execute(
                select(PopularityAbsorption.id, PopularityAbsorption.loser_filename).where(
                    PopularityAbsorption.kind == "absorb",
                    PopularityAbsorption.reversed_by_id.is_(None),
                    PopularityAbsorption.loser_filename.like(f"{tag}r{seed}-%")
                    | PopularityAbsorption.loser_filename.like(f"{tag}d{seed}-%"),
                ))).all()
            # unique among open rows: one open edge per loser image, dropped names carry a counter
            open_rows = sorted(open_rows, key=lambda r: r[1])
            if not open_rows:
                log.append(f"{step}: reverse (no open rows)")
                continue
            target_id, target_name = rng.choice(open_rows)
            log.append(f"{step}: reverse row of {target_name.removeprefix(tag)}")
            await repo.reverse(target_id, rng.choice(REASONS))
            counts["reverse"] += 1
        else:
            alive = [i for i in ids if i not in deleted]
            if not alive:
                log.append(f"{step}: delete (none left)")
                continue
            victim = rng.choice(alive)
            log.append(f"{step}: delete image #{index[victim]}")
            await session.execute(delete(Image).where(Image.id == victim))
            deleted.add(victim)
            counts["delete"] += 1

        drift = await repo.verify()
        assert drift == [], f"seed {seed} drift after step {step}: {drift}\n" + "\n".join(log)
        for image_id, popularity in (await session.execute(
                select(Image.id, Image.popularity).where(Image.id.in_(ids)))).all():
            if await repo._open_edge(image_id) is None:
                assert popularity >= 1, (
                    f"seed {seed} step {step}: #{index[image_id]} is not discarded but has "
                    f"popularity {popularity}\n" + "\n".join(log))
    return log, counts


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("seed", range(25))
async def test_random_absorb_and_reverse_sequences_never_drift(db_session, seed):
    await run_sequence(db_session, seed)


@pytest.mark.asyncio(loop_scope="session")
async def test_random_sequences_are_replayable_and_not_vacuous(db_session):
    totals = {"absorb": 0, "dropped": 0, "reverse": 0, "delete": 0}
    for seed in range(25):
        log, counts = await run_sequence(db_session, seed, tag="first-")
        again, _ = await run_sequence(db_session, seed, tag="second-")
        assert log == again, f"seed {seed} did not replay identically"
        for kind, n in counts.items():
            totals[kind] += n
    for kind, n in totals.items():
        assert n >= MIN_SUCCESSES, f"only {n} successful {kind} operations across all seeds: {totals}"


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

        results = await asyncio.gather(run(b, a), run(c, a), run(a, d), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result

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
