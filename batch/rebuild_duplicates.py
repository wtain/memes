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
# docs/superpowers/specs/2026-07-25-duplicate-clustering-incremental-design.md and
# docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md.
# Ingestion's Tier A/Tier B review will call the same find_duplicates() shape with
# different probe/corpus fragments and threshold, not this script's CLI.
_ACTIVE_PROBE_INCREMENTAL = """
    SELECT i.id, e.embedding
    FROM images i
    JOIN embeddings e ON e.image_id = i.id
    WHERE i.status = 'active'
      AND NOT EXISTS (
          SELECT 1 FROM tmp_duplicates d
          WHERE (d.image_id1 = i.id OR d.image_id2 = i.id) AND d.distance_source = :probe_distance_source
      )
"""

_ACTIVE_PROBE_FULL = """
    SELECT i.id, e.embedding
    FROM images i
    JOIN embeddings e ON e.image_id = i.id
    WHERE i.status = 'active'
"""

_ACTIVE_PROBE_OCR_TEXT_INCREMENTAL = """
    SELECT i.id, oe.embedding
    FROM images i
    JOIN ocr_text_embeddings oe ON oe.image_id = i.id
    WHERE i.status = 'active'
      AND NOT EXISTS (
          SELECT 1 FROM tmp_duplicates d
          WHERE (d.image_id1 = i.id OR d.image_id2 = i.id) AND d.distance_source = 'ocr_text'
      )
"""

_ACTIVE_PROBE_OCR_TEXT_FULL = """
    SELECT i.id, oe.embedding
    FROM images i
    JOIN ocr_text_embeddings oe ON oe.image_id = i.id
    WHERE i.status = 'active'
"""

# Safety-net probe: deliberately NOT _ACTIVE_PROBE_INCREMENTAL/_FULL, and deliberately has no
# incremental skip condition at all -- see
# docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md's §3 for why sharing an
# incremental marker with the general CLIP probe is genuinely
# broken (not just imprecise): it would silently and permanently disable the safety net for any
# text-heavy image that also has an unrelated non-text-heavy CLIP match. Scoped to active
# text_heavy images only, on the probe side too, so it never wastes a KNN search on an image that
# could never match its own corpus filter anyway.
_ACTIVE_PROBE_SAFETY_NET = """
    SELECT i.id, e.embedding
    FROM images i
    JOIN embeddings e ON e.image_id = i.id
    WHERE i.status = 'active'
      AND EXISTS (
          SELECT 1 FROM image_classifications c WHERE c.image_id = i.id
          AND c.classifier = 'text_heavy_v1' AND c.result = 'text_heavy'
      )
"""

# Text-heavy-vs-text-heavy pairs are excluded from the general CLIP probe -- they're matched via
# OCR-text embeddings instead, plus the tight CLIP safety net above. Appended to every existing
# CLIP corpus filter as an extra AND clause. References probe.id/i2.id, both in scope wherever
# this is interpolated into find_duplicates()'s LATERAL subquery.
#
# Keyed on ocr_text_embeddings existence, NOT on image_classifications' text_heavy result --
# deliberately, not an oversight (found and fixed during final whole-branch review). An image can
# be classified text_heavy but still have no ocr_text_embeddings row: build_ocr_text_embeddings.py
# applies its own quality filter (OCR.CONFIDENCE_MIN / OCR.LANG_SCORE_MIN), so text too short or
# low-confidence to pass that filter never gets embedded even though the classifier marked the
# image text_heavy. If this exclusion were keyed on classification instead, such an image would be
# excluded from the general CLIP probe (because it's classified text_heavy) with zero replacement
# coverage from the OCR-text probe (because it has no embedding to probe with) -- silently losing
# all mid-band duplicate coverage down to the 0.02 CLIP safety net. Keying on embedding existence
# instead makes this exclusion the exact complement of what the OCR-text probe can actually cover,
# so an image in that gap correctly falls back to normal general-CLIP matching. See
# docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md.
_EXCLUDE_TEXT_HEAVY_PAIR = """
    NOT (
        EXISTS (SELECT 1 FROM ocr_text_embeddings oe1 WHERE oe1.image_id = probe.id)
        AND EXISTS (SELECT 1 FROM ocr_text_embeddings oe2 WHERE oe2.image_id = i2.id)
    )
"""

# Scoped to text-heavy-vs-text-heavy pairs only -- the inverse of the exclusion above.
_TEXT_HEAVY_PAIR_ONLY = """
    EXISTS (SELECT 1 FROM image_classifications tc1 WHERE tc1.image_id = probe.id
            AND tc1.classifier = 'text_heavy_v1' AND tc1.result = 'text_heavy')
    AND EXISTS (SELECT 1 FROM image_classifications tc2 WHERE tc2.image_id = i2.id
                AND tc2.classifier = 'text_heavy_v1' AND tc2.result = 'text_heavy')
"""

TEXT_EMBEDDING_TIGHT_THRESHOLD = 0.05   # auto-cluster-worthy OCR-text match
TEXT_EMBEDDING_LOOSE_THRESHOLD = 0.10   # review-worthy OCR-text match (Tier B upper bound)
CLIP_SAFETY_NET_THRESHOLD = 0.02        # near-pixel-identical repost, text-heavy pairs only

_ACTIVE_CORPUS_FILTER_CLIP = f"i2.status = 'active' AND ({_EXCLUDE_TEXT_HEAVY_PAIR})"
_ACTIVE_CORPUS_FILTER_CLIP_SAFETY_NET = f"i2.status = 'active' AND ({_TEXT_HEAVY_PAIR_ONLY})"
_ACTIVE_CORPUS_FILTER_OCR_TEXT = "i2.status = 'active'"  # ocr_text_embeddings join is already
                                                           # text_heavy-scoped by construction


async def find_duplicates(session, probe_sql: str, corpus_filter_sql: str, k: int, threshold: float,
                           distance_source: str, embedding_table: str = "embeddings",
                           extra_params: dict | None = None) -> int:
    """Insert candidate duplicate pairs found by probing `probe_sql` images (must select
    exactly (id, embedding)) against `corpus_filter_sql`-scoped neighbors in `embedding_table`
    ("embeddings" for CLIP, "ocr_text_embeddings" for OCR-text -- both use the column name
    `embedding`), via an HNSW-assisted per-image KNN search rather than a full cross join.
    Idempotent -- re-running with no new probe rows inserts zero rows, and a pair already present
    (from either probe direction, or from a different distance_source's earlier probe call within
    the same run) is skipped via ON CONFLICT DO NOTHING. Returns the number of rows actually
    inserted.

    distance_source is stamped on every inserted row -- 'clip' or 'ocr_text' -- see
    docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md.

    `probe_sql`/`corpus_filter_sql` may reference named bind params (e.g. `:batch_id`) -- pass
    their values via `extra_params` rather than string-interpolating them into the fragment, even
    though callers so far only ever pass internally-generated values (never raw user input)."""
    stmt = text(f"""
        INSERT INTO tmp_duplicates (image_id1, image_id2, distance, match_source, distance_source)
        SELECT
            LEAST(probe.id, nn.image_id)    AS image_id1,
            GREATEST(probe.id, nn.image_id) AS image_id2,
            nn.distance,
            nn.match_source,
            :distance_source
        FROM ({probe_sql}) AS probe(id, embedding)
        CROSS JOIN LATERAL (
            SELECT
                e2.image_id,
                probe.embedding <=> e2.embedding AS distance,
                CASE WHEN i2.status = 'active' THEN 'cross_corpus' ELSE 'in_batch' END AS match_source
            FROM {embedding_table} e2
            JOIN images i2 ON i2.id = e2.image_id
            WHERE e2.image_id != probe.id
              AND ({corpus_filter_sql})
            ORDER BY probe.embedding <=> e2.embedding
            LIMIT :k
        ) nn
        WHERE nn.distance < :threshold
        ON CONFLICT (image_id1, image_id2) DO NOTHING
    """)
    params = {"k": k, "threshold": threshold, "distance_source": distance_source, **(extra_params or {})}
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
        clip_probe_sql = _ACTIVE_PROBE_FULL
        ocr_probe_sql = _ACTIVE_PROBE_OCR_TEXT_FULL
    else:
        clip_probe_sql = _ACTIVE_PROBE_INCREMENTAL
        ocr_probe_sql = _ACTIVE_PROBE_OCR_TEXT_INCREMENTAL

    # probe_distance_source is only referenced by the _INCREMENTAL probe variants' own
    # NOT EXISTS clause -- harmless to always pass it even on a --full run, where the _FULL
    # variants simply don't reference it; SQLAlchemy's text() execute ignores unused dict keys.
    inserted = await find_duplicates(
        session, clip_probe_sql, _ACTIVE_CORPUS_FILTER_CLIP, k, threshold,
        distance_source="clip", extra_params={"probe_distance_source": "clip"},
    )
    # Deliberately NOT clip_probe_sql, and deliberately no incremental skip condition -- see
    # docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md's §3 for why.
    inserted += await find_duplicates(
        session, _ACTIVE_PROBE_SAFETY_NET, _ACTIVE_CORPUS_FILTER_CLIP_SAFETY_NET, k,
        CLIP_SAFETY_NET_THRESHOLD, distance_source="clip",
    )
    inserted += await find_duplicates(
        session, ocr_probe_sql, _ACTIVE_CORPUS_FILTER_OCR_TEXT, k, TEXT_EMBEDDING_LOOSE_THRESHOLD,
        distance_source="ocr_text", embedding_table="ocr_text_embeddings",
        extra_params={"probe_distance_source": "ocr_text"},
    )
    return inserted


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
