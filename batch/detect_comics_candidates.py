"""Suggested comics (epic multi-image-comics, C9; docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-08-c9-candidate-queue-design.md).
Finds groups of images saved seconds apart (filename timestamps) that look alike (CLIP distance) and stores each as an
open row in comics_candidates for the owner to accept or dismiss. Distances come from `embeddings` with pgvector only
for pairs inside the time window, never for all pairs. Changes no table but the two candidate tables, and never flags
or moves an image. Incremental by construction (the unique group key); a group sharing two or more images with a
dismissed or accepted candidate is skipped. Safe to re-run."""
import argparse
import asyncio
import uuid

from sqlalchemy import and_, exists, select, text

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.comics_candidates import group_candidates, parse_timestamp, window_pairs
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.batch_runs import BatchRunRepository
from repository.comics_candidates import ComicsCandidatesRepository
from Storage.db import AsyncSessionLocal
from Storage.models import ComicsPanel, Embedding, Image, ImageExtras, ImageStatus

KIND = "detect_comics_candidates"
DETECTOR_VERSION = 1
PAIR_BATCH = 2000  # pairs per distance query

_DISTANCES = text(
    "SELECT p.a AS a, p.b AS b, MIN(ea.embedding <=> eb.embedding) AS distance "
    "FROM unnest(CAST(:a AS uuid[]), CAST(:b AS uuid[])) AS p(a, b) "
    "JOIN embeddings ea ON ea.image_id = p.a JOIN embeddings eb ON eb.image_id = p.b "
    "GROUP BY p.a, p.b")


async def eligible_items(session) -> list[tuple]:
    """(image_id, filename, shape, ts) of active, unflagged images in no comics that have an embedding and a
    parsable file name timestamp."""
    rows = await session.execute(
        select(Image.id, Image.filename)
        .where(Image.status == ImageStatus.ACTIVE,
               ~exists().where(and_(ImageExtras.image_id == Image.id, ImageExtras.flagged.is_(True))),
               ~exists().where(ComicsPanel.image_id == Image.id),
               exists().where(Embedding.image_id == Image.id)))
    items = []
    for image_id, filename in rows:
        parsed = parse_timestamp(filename)
        if parsed is not None:
            items.append((image_id, filename, parsed[0], parsed[1]))
    return items


async def window_distances(session, items, gap_seconds: float) -> dict:
    """CLIP cosine distance of each pair of images inside the time window, keyed by both id orders."""
    pairs = [(a[0], b[0]) for a, b in window_pairs(items, gap_seconds)]
    distances: dict = {}
    for start in range(0, len(pairs), PAIR_BATCH):
        chunk = pairs[start:start + PAIR_BATCH]
        rows = await session.execute(_DISTANCES, {"a": [a for a, _ in chunk], "b": [b for _, b in chunk]})
        for a, b, distance in rows:
            distances[(a, b)] = distances[(b, a)] = float(distance)
    return distances


async def run(session, gap_seconds: float, min_distance: float, max_distance: float, max_group: int,
              max_candidates: int) -> SimpleMetricsListener:
    items = await eligible_items(session)
    distances = await window_distances(session, items, gap_seconds)
    result = group_candidates(items, lambda a, b: distances.get((a, b)), gap_seconds, min_distance, max_distance,
                              max_group)
    print(f"Images considered: {len(items)}, groups found: {len(result.groups)}")

    metrics = SimpleMetricsListener()
    metrics.add("images_considered", len(items))
    metrics.add("pairs_linked", result.pairs_linked)
    metrics.add("groups_found", len(result.groups))
    metrics.add("groups_dropped_oversized", result.dropped_oversized)
    for name in ("groups_inserted", "groups_skipped_existing", "groups_skipped_decided"):
        metrics.add(name, 0)

    candidates = ComicsCandidatesRepository(session)
    for group in result.groups:  # strongest first, so the per-run cap keeps the best
        if metrics.counters_dict()["groups_inserted"] >= max_candidates:
            break
        if await candidates.overlaps_decided(group.image_ids):
            metrics.increment("groups_skipped_decided")
        elif await candidates.insert_group(group.image_ids, group.max_gap, group.max_distance,
                                           str(DETECTOR_VERSION)) is None:
            metrics.increment("groups_skipped_existing")
        else:
            metrics.increment("groups_inserted")
    return metrics


async def _execute(run_id: uuid.UUID) -> SimpleMetricsListener:
    c = settings.COMICS_CANDIDATES
    async with AsyncSessionLocal() as session:
        metrics = await run(session, c.GAP_SECONDS, c.MIN_DISTANCE, c.MAX_DISTANCE, c.MAX_GROUP,
                            c.MAX_CANDIDATES_PER_RUN)
        await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
        await session.commit()
    return metrics


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            metrics = await _execute(run_id)
    else:
        async with tracked_run(kind=KIND, trigger=trigger) as new_run_id:
            metrics = await _execute(new_run_id)
    print("Comics candidate detection results:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
