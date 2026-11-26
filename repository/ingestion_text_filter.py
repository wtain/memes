"""Set-based SQL for the ingestion Tier B text filter (batch/ingest_text_filter.py).

Classifies every open Tier B candidate pair of the active ingestion run by what the OCR text says,
and settles ("auto-keeps") the clearly different ones per pair. Never commits: the caller owns the
transaction. See docs/superpowers/specs/2026-11-26-ingestion-tier-b-text-filter-design.md.
"""
import uuid
from dataclasses import dataclass

from sqlalchemy import text

ELIGIBLE = "eligible"
FAR_WEAK_TEXT = "far_weak_text"
BUCKETS = ("eligible", FAR_WEAK_TEXT, "vetoed", "no_text", "overlap_high", "ocr_text_source", "stale_text")
# Buckets whose pairs are settled by --apply.
SETTLED_BUCKETS = (ELIGIBLE, FAR_WEAK_TEXT)


@dataclass(frozen=True)
class FilterParams:
    min_lemmas: int
    max_overlap: float
    veto_distance: float
    # Far band (optional; both None = off): a pair at or beyond far_min_distance is also settled
    # when its text does not strongly corroborate it (overlap below far_max_overlap, or too little
    # text), unless the embedding veto or the OCR-text probe's own lemma check says otherwise.
    far_min_distance: float | None = None
    far_max_overlap: float | None = None


@dataclass(frozen=True)
class PlannedPair:
    pair_id: uuid.UUID
    image_id1: uuid.UUID
    image_id2: uuid.UUID
    distance: float
    overlap: float | None
    text_distance: float | None
    bucket: str


# One row per open Tier B pair (the same set list_tier_b_review_page shows): in the Tier B band,
# not yet reviewed, one side pending in this batch, the other side not rejected. Lemma counts and
# shared-lemma counts are computed per image / per pair in two grouped CTEs, never per pair in a
# correlated subquery. The bucket CASE is evaluated in precedence order: overlap_high comes before
# vetoed so "vetoed" only counts pairs that would otherwise have been auto-kept. far_weak_text is the
# far band: a pair at or beyond :far_min whose text is missing or below :far_max overlap, with no
# embedding veto (NULL :far_min or :far_max makes the far branch fall through).
_PLAN_SQL = text("""
WITH pair AS (
    SELECT td.id AS pair_id, td.image_id1, td.image_id2, td.distance, td.distance_source
    FROM tmp_duplicates td
    JOIN images i1 ON i1.id = td.image_id1
    JOIN images i2 ON i2.id = td.image_id2
    WHERE td.tier_b_reviewed_at IS NULL
      AND td.distance >= :low AND td.distance < :high
      AND ((i1.ingestion_batch_id = :batch_id AND i1.status = 'pending' AND i2.status <> 'rejected')
        OR (i2.ingestion_batch_id = :batch_id AND i2.status = 'pending' AND i1.status <> 'rejected'))
),
involved AS (
    SELECT image_id1 AS image_id FROM pair
    UNION
    SELECT image_id2 FROM pair
),
lc AS (
    SELECT l.image_id, count(*) AS n, bool_or(l.stale) AS has_stale
    FROM ocr_lemmas l JOIN involved v ON v.image_id = l.image_id
    GROUP BY l.image_id
),
shared AS (
    SELECT p.pair_id, count(*) AS sh
    FROM pair p
    JOIN ocr_lemmas x ON x.image_id = p.image_id1
    JOIN ocr_lemmas y ON y.image_id = p.image_id2 AND y.lemma = x.lemma
    GROUP BY p.pair_id
),
scored AS (
    SELECT p.pair_id, p.image_id1, p.image_id2, p.distance, p.distance_source,
           coalesce(la.n, 0) AS na, coalesce(lb.n, 0) AS nb,
           (coalesce(la.has_stale, false) OR coalesce(lb.has_stale, false)) AS stale,
           coalesce(s.sh, 0) AS sh,
           (ea.embedding <=> eb.embedding) AS text_distance
    FROM pair p
    LEFT JOIN lc la ON la.image_id = p.image_id1
    LEFT JOIN lc lb ON lb.image_id = p.image_id2
    LEFT JOIN shared s ON s.pair_id = p.pair_id
    LEFT JOIN ocr_text_embeddings ea ON ea.image_id = p.image_id1
    LEFT JOIN ocr_text_embeddings eb ON eb.image_id = p.image_id2
)
SELECT pair_id, image_id1, image_id2, distance, text_distance,
       CASE WHEN na > 0 AND nb > 0 THEN sh::float / LEAST(na, nb) END AS overlap,
       CASE
           WHEN distance_source = 'ocr_text' THEN 'ocr_text_source'
           WHEN stale THEN 'stale_text'
           WHEN na < :min_lemmas OR nb < :min_lemmas THEN
               CASE WHEN distance >= CAST(:far_min AS double precision)
                         AND NOT (text_distance IS NOT NULL AND text_distance < :veto_distance)
                    THEN 'far_weak_text' ELSE 'no_text' END
           WHEN sh::float / LEAST(na, nb) >= :max_overlap THEN
               CASE WHEN distance >= CAST(:far_min AS double precision)
                         AND sh::float / LEAST(na, nb) < CAST(:far_max AS double precision)
                         AND NOT (text_distance IS NOT NULL AND text_distance < :veto_distance)
                    THEN 'far_weak_text' ELSE 'overlap_high' END
           WHEN text_distance IS NOT NULL AND text_distance < :veto_distance THEN 'vetoed'
           ELSE 'eligible'
       END AS bucket
FROM scored
ORDER BY pair_id
""")


async def plan_pairs(session, batch_id, band: tuple[float, float], params: FilterParams) -> list[PlannedPair]:
    """Every open Tier B pair of `batch_id` with its bucket; only "eligible" is ever applied."""
    # Scoped to this transaction only, same reasoning as IngestionRepository.list_tier_b_review_page.
    await session.execute(text("SET LOCAL work_mem = '256MB'"))
    rows = (await session.execute(_PLAN_SQL, {
        "batch_id": batch_id, "low": band[0], "high": band[1],
        "min_lemmas": params.min_lemmas, "max_overlap": params.max_overlap,
        "veto_distance": params.veto_distance,
        "far_min": params.far_min_distance, "far_max": params.far_max_overlap,
    })).mappings().all()
    return [
        PlannedPair(r["pair_id"], r["image_id1"], r["image_id2"], r["distance"],
                    r["overlap"], r["text_distance"], r["bucket"])
        for r in rows
    ]


async def apply_pairs(session, run_id: uuid.UUID, pair_ids: list[uuid.UUID]) -> int:
    """Settle exactly these pairs (only those still unreviewed) and tag them with `run_id`."""
    if not pair_ids:
        return 0
    result = await session.execute(
        text("""UPDATE tmp_duplicates
                SET tier_b_reviewed_at = now(), tier_b_auto_kept_run = :run_id
                WHERE id = ANY(:ids) AND tier_b_reviewed_at IS NULL"""),
        {"run_id": run_id, "ids": list(pair_ids)},
    )
    return result.rowcount


async def undo_run(session, run_id: uuid.UUID) -> int:
    """Reopen the pairs still tagged with `run_id`; never touches a pair a person settled."""
    result = await session.execute(
        text("""UPDATE tmp_duplicates
                SET tier_b_reviewed_at = NULL, tier_b_auto_kept_run = NULL
                WHERE tier_b_auto_kept_run = :run_id"""),
        {"run_id": run_id},
    )
    return result.rowcount
