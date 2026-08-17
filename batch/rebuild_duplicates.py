import argparse
import asyncio
import uuid

from sqlalchemy import text

from batch import clusterize
from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import load_env, settings
from repository.batch_runs import BatchAlreadyRunningError
from Storage.db import AsyncSessionLocal

# Scoping fragments for the active-library rebuild -- see
# docs/superpowers/specs/2026-07-25-duplicate-clustering-incremental-design.md.
# Ingestion's Tier A/Tier B review will call the same find_duplicates() shape with
# different probe/corpus fragments and threshold, not this script's CLI.
_ACTIVE_PROBE_INCREMENTAL = """
    SELECT i.id, e.embedding
    FROM images i
    JOIN embeddings e ON e.image_id = i.id
    WHERE i.status = 'active'
      AND NOT EXISTS (
          SELECT 1 FROM tmp_duplicates d
          WHERE d.image_id1 = i.id OR d.image_id2 = i.id
      )
"""

_ACTIVE_PROBE_FULL = """
    SELECT i.id, e.embedding
    FROM images i
    JOIN embeddings e ON e.image_id = i.id
    WHERE i.status = 'active'
"""

_ACTIVE_CORPUS_FILTER = "i2.status = 'active'"


async def find_duplicates(session, probe_sql: str, corpus_filter_sql: str, k: int, threshold: float,
                           extra_params: dict | None = None) -> int:
    """Insert candidate duplicate pairs found by probing `probe_sql` images (must select
    exactly (id, embedding)) against `corpus_filter_sql`-scoped neighbors, via an
    HNSW-assisted per-image KNN search rather than a full cross join. Idempotent --
    re-running with no new probe rows inserts zero rows, and a pair already present (from
    either probe direction) is skipped via ON CONFLICT DO NOTHING. Returns the number of
    rows actually inserted.

    `probe_sql`/`corpus_filter_sql` may reference named bind params (e.g. `:batch_id`) --
    pass their values via `extra_params` rather than string-interpolating them into the
    fragment, even though callers so far only ever pass internally-generated values (never
    raw user input)."""
    stmt = text(f"""
        INSERT INTO tmp_duplicates (image_id1, image_id2, distance, match_source)
        SELECT
            LEAST(probe.id, nn.image_id)    AS image_id1,
            GREATEST(probe.id, nn.image_id) AS image_id2,
            nn.distance,
            nn.match_source
        FROM ({probe_sql}) AS probe(id, embedding)
        CROSS JOIN LATERAL (
            SELECT
                e2.image_id,
                probe.embedding <=> e2.embedding AS distance,
                CASE WHEN i2.status = 'active' THEN 'cross_corpus' ELSE 'in_batch' END AS match_source
            FROM embeddings e2
            JOIN images i2 ON i2.id = e2.image_id
            WHERE e2.image_id != probe.id
              AND ({corpus_filter_sql})
            ORDER BY probe.embedding <=> e2.embedding
            LIMIT :k
        ) nn
        WHERE nn.distance < :threshold
        ON CONFLICT (image_id1, image_id2) DO NOTHING
    """)
    params = {"k": k, "threshold": threshold, **(extra_params or {})}
    result = await session.execute(stmt, params)
    return result.rowcount


async def rebuild_active_library(session, k: int, threshold: float, full: bool = False) -> int:
    if full:
        print("Full rebuild: clearing existing active-library candidate pairs...")
        # Scoped to pairs where both sides are active, so a future in-flight ingestion
        # review (pending-involving rows) is never touched by a routine active-library
        # rebuild.
        await session.execute(text("""
            DELETE FROM tmp_duplicates
            WHERE image_id1 IN (SELECT id FROM images WHERE status = 'active')
              AND image_id2 IN (SELECT id FROM images WHERE status = 'active')
        """))
        probe_sql = _ACTIVE_PROBE_FULL
    else:
        probe_sql = _ACTIVE_PROBE_INCREMENTAL

    return await find_duplicates(session, probe_sql, _ACTIVE_CORPUS_FILTER, k, threshold)


async def _process(k: int, threshold: float, full: bool) -> None:
    async with AsyncSessionLocal() as session:
        inserted = await rebuild_active_library(session, k=k, threshold=threshold, full=full)
        await session.commit()
        print(f"Inserted {inserted} candidate duplicate pair(s) (k={k}, threshold={threshold}).")


async def main(
    trigger: str = "manual",
    run_id: uuid.UUID | None = None,
    k: int | None = None,
    threshold: float | None = None,
    full: bool = False,
    chain: bool = True,
) -> None:
    resolved_k = k if k is not None else settings.DUPLICATES.K
    resolved_threshold = threshold if threshold is not None else settings.DUPLICATES.THRESHOLD

    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process(resolved_k, resolved_threshold, full)
    else:
        async with tracked_run(kind="rebuild_duplicates", trigger=trigger):
            await _process(resolved_k, resolved_threshold, full)

    if chain:
        try:
            await clusterize.main(trigger=trigger)
        except BatchAlreadyRunningError as e:
            print(f"Skipping chained clusterize: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--full", action="store_true",
                        help="Clear existing active-library candidate pairs and re-probe every "
                             "active image (default: incremental -- only images with no existing "
                             "tmp_duplicates row are probed)")
    parser.add_argument("--k", type=int, default=None,
                        help="Neighbors considered per probe image (default: settings.DUPLICATES.K)")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Candidate distance cutoff (default: settings.DUPLICATES.THRESHOLD)")
    parser.add_argument("--no-chain", action="store_true",
                        help="Skip the automatic clusterize run after rebuilding duplicates.")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(k=args.k, threshold=args.threshold, full=args.full, chain=not args.no_chain))
    # trigger defaults to "manual" -- unchanged direct-CLI behavior
