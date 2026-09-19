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

Run `extract_text_from_memes.py --status pending` *before* this script's `--tier tier_a`
call, not between Tier A and Tier B as earlier drafts of the design assumed -- empirical
validation (2026-07-25) found Tier A's "thumbnails alone are decisive" premise doesn't hold
for all content (e.g. visually-similar-format-but-different-text meme cards), so both tiers
need OCR text available for review, not just Tier B. This needs no code change here or in
the review API/UI -- both already fetch/display OCR text unconditionally per member; it's
purely an operational ordering fix. See Decision #10 in
docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md.
"""
import argparse
import asyncio

from batch.clusterize import PROXIMITY_THRESHOLD as TIER_A_THRESHOLD
from batch.rebuild_duplicates import (
    find_duplicates, _EXCLUDE_TEXT_HEAVY_PAIR, _TEXT_HEAVY_PAIR_ONLY,
    CLIP_SAFETY_NET_THRESHOLD, TEXT_EMBEDDING_LOOSE_THRESHOLD,
)
from config.settings import load_env, settings
from repository.batch_runs import BatchRunRepository
from Storage.db import AsyncSessionLocal

TIER_STAGE = {"tier_a": "tier_a_review", "tier_b": "tier_b_review"}

_STAGE_ORDER = ["hash_dedup", "format_validation", "tier_a_review", "tier_b_review"]


def should_advance_stage(current_stage: str | None, target_stage: str) -> bool:
    """The stage must only ever advance, never rewind -- mirrors
    ingest_validate_formats.should_advance_stage's reasoning, generalized to two arbitrary
    stages instead of one fixed pair. A re-run of an earlier tier must not stomp a later
    stage back, which would make the frontend's tierForStage() drop the review queue until
    the later tier's find-duplicates call re-runs."""
    if current_stage not in _STAGE_ORDER:
        return False
    return _STAGE_ORDER.index(target_stage) > _STAGE_ORDER.index(current_stage)


# Probe = this batch's pending images. Corpus = the active library plus this image's own
# batch siblings -- a single filter covering both "cross-corpus" and "in-batch" matches in
# one KNN pass, tagged via match_source. See the duplicate-clustering prereq's scoping table.
_BATCH_PROBE_SQL = """
    SELECT i.id, e.embedding
    FROM images i
    JOIN embeddings e ON e.image_id = i.id
    WHERE i.status = 'pending' AND i.ingestion_batch_id = :batch_id
"""

_BATCH_PROBE_SQL_OCR_TEXT = """
    SELECT i.id, oe.embedding
    FROM images i
    JOIN ocr_text_embeddings oe ON oe.image_id = i.id
    WHERE i.status = 'pending' AND i.ingestion_batch_id = :batch_id
"""

_BATCH_CORPUS_FILTER_SQL_CLIP = f"""
    (i2.status = 'active' OR (i2.status = 'pending' AND i2.ingestion_batch_id = :batch_id))
    AND ({_EXCLUDE_TEXT_HEAVY_PAIR})
"""

_BATCH_CORPUS_FILTER_SQL_CLIP_SAFETY_NET = f"""
    (i2.status = 'active' OR (i2.status = 'pending' AND i2.ingestion_batch_id = :batch_id))
    AND ({_TEXT_HEAVY_PAIR_ONLY})
"""

_BATCH_CORPUS_FILTER_SQL_OCR_TEXT = """
    i2.status = 'active' OR (i2.status = 'pending' AND i2.ingestion_batch_id = :batch_id)
"""


async def find_batch_duplicates(session, batch_id, k: int, threshold: float) -> int:
    """Populate tmp_duplicates with candidate pairs for `batch_id`'s pending images, at the
    given threshold, across all three signals (general CLIP excluding text-heavy pairs, CLIP
    safety net for text-heavy pairs, OCR-text for text-heavy pairs). Safe to call once per tier
    (Tier A tight, Tier B loose) -- a pair already found by an earlier, tighter call, or by a
    different signal, is a no-op via ON CONFLICT DO NOTHING.

    The OCR-text probe always runs at TEXT_EMBEDDING_LOOSE_THRESHOLD regardless of `threshold` or
    which tier is calling -- deliberately, not by coincidence. This mirrors this file's own
    already-established convention for the CLIP probe itself: the tier_b CLIP call already
    inserts generously all the way down to distance 0, relying entirely on the review query's own
    band filtering (get_tier_candidate_rows/list_tier_b_review_page's `distance >= low AND
    distance < high`) to decide which UI tier a stored row surfaces in -- not on which probe call
    inserted it. See docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md's §4
    for why deriving a tighter value via e.g. min(threshold, TEXT_EMBEDDING_LOOSE_THRESHOLD) would
    be a real bug, not a simplification.

    The safety-net probe reuses _BATCH_PROBE_SQL unchanged (all pending images in the batch, not
    scoped to text_heavy on the probe side) -- unlike rebuild_duplicates.py's own safety-net
    probe, this file's probes have no incremental skip condition to begin with (this function
    probes every pending image in the batch every time, relying entirely on ON CONFLICT DO
    NOTHING), so there is no analogous incremental-staleness risk here to design around. The
    corpus filter alone (_BATCH_CORPUS_FILTER_SQL_CLIP_SAFETY_NET) correctly finds zero candidates
    for a non-text-heavy probe image regardless."""
    inserted = await find_duplicates(
        session, _BATCH_PROBE_SQL, _BATCH_CORPUS_FILTER_SQL_CLIP, k, threshold,
        distance_source="clip", extra_params={"batch_id": batch_id},
    )
    inserted += await find_duplicates(
        session, _BATCH_PROBE_SQL, _BATCH_CORPUS_FILTER_SQL_CLIP_SAFETY_NET, k, CLIP_SAFETY_NET_THRESHOLD,
        distance_source="clip", extra_params={"batch_id": batch_id},
    )
    inserted += await find_duplicates(
        session, _BATCH_PROBE_SQL_OCR_TEXT, _BATCH_CORPUS_FILTER_SQL_OCR_TEXT, k,
        TEXT_EMBEDDING_LOOSE_THRESHOLD,
        distance_source="ocr_text", embedding_table="ocr_text_embeddings",
        extra_params={"batch_id": batch_id},
    )
    return inserted


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
        target_stage = TIER_STAGE[tier]
        if should_advance_stage(active_run.stage, target_stage):
            await runs_repo.set_stage(active_run.run_id, target_stage)
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
