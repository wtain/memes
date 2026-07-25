"""
Ingestion Stage 2 (Tier A) / Stage 3 (Tier B): find near-duplicate candidate pairs for a
batch's pending images, against both the batch itself and the active corpus, via the same
incremental HNSW-assisted KNN primitive (find_duplicates) rebuild_duplicates.py uses for
the active library. See docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md and
docs/superpowers/specs/2026-07-25-duplicate-clustering-incremental-design.md.

Populates tmp_duplicates only -- listing clusters for review and resolving decisions is a
Backend API concern (Backend/app/repositories/ingestion_repository.py), not this script.

Tier A reuses clusterize.py's PROXIMITY_THRESHOLD (0.05) rather than a new constant, per
the design's "don't invent a second number for the same concept" reasoning. Tier B uses
settings.DUPLICATES.THRESHOLD (0.3) as its outer bound; the 0.05 lower bound that
distinguishes Tier B's *review queue* from Tier A's is enforced by the review query, not
here -- this script's job is just to populate candidates, generously, once per tier.
"""
import argparse
import asyncio

from batch.clusterize import PROXIMITY_THRESHOLD as TIER_A_THRESHOLD
from batch.rebuild_duplicates import find_duplicates
from config.settings import load_env, settings
from repository.batch_runs import BatchRunRepository
from Storage.db import AsyncSessionLocal

TIER_STAGE = {"tier_a": "tier_a_review", "tier_b": "tier_b_review"}

# Probe = this batch's pending images. Corpus = the active library plus this image's own
# batch siblings -- a single filter covering both "cross-corpus" and "in-batch" matches in
# one KNN pass, tagged via match_source. See the duplicate-clustering prereq's scoping table.
_BATCH_PROBE_SQL = """
    SELECT i.id, e.embedding
    FROM images i
    JOIN embeddings e ON e.image_id = i.id
    WHERE i.status = 'pending' AND i.ingestion_batch_id = :batch_id
"""

_BATCH_CORPUS_FILTER_SQL = """
    i2.status = 'active'
    OR (i2.status = 'pending' AND i2.ingestion_batch_id = :batch_id)
"""


async def find_batch_duplicates(session, batch_id, k: int, threshold: float) -> int:
    """Populate tmp_duplicates with candidate pairs for `batch_id`'s pending images, at the
    given threshold. Safe to call once per tier (Tier A tight, Tier B loose) -- a pair
    already found by an earlier, tighter call is a no-op via ON CONFLICT DO NOTHING."""
    return await find_duplicates(
        session, _BATCH_PROBE_SQL, _BATCH_CORPUS_FILTER_SQL, k, threshold,
        extra_params={"batch_id": batch_id},
    )


async def main(env: str | None, tier: str, k: int | None) -> None:
    load_env(env)
    resolved_k = k if k is not None else settings.DUPLICATES.K
    threshold = TIER_A_THRESHOLD if tier == "tier_a" else settings.DUPLICATES.THRESHOLD

    async with AsyncSessionLocal() as session:
        runs_repo = BatchRunRepository(session)
        active_run = await runs_repo.get_active_run(kind="ingestion")
        if active_run is None:
            raise RuntimeError("No ingestion run is currently in progress -- run ingest_hash_dedup.py first.")

        inserted = await find_batch_duplicates(session, active_run.run_id, k=resolved_k, threshold=threshold)
        await runs_repo.set_stage(active_run.run_id, TIER_STAGE[tier])
        await session.commit()

    print(f"Ingestion run {active_run.run_id} [{tier}]: {inserted} candidate pair(s) "
          f"(k={resolved_k}, threshold={threshold}).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--tier", choices=["tier_a", "tier_b"], default="tier_a")
    parser.add_argument("--k", type=int, default=None,
                         help="Neighbors considered per probe image (default: settings.DUPLICATES.K)")
    args = parser.parse_args()
    asyncio.run(main(args.env, args.tier, args.k))
