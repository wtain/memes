# Text-Embedding Duplicate Matching

status: approved
Originates from: the 2026-09-18 conversation continuing the Tier B review-noise thread (see
`docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md`'s Non-goals, which explicitly deferred
"wiring this into `tmp_duplicates`, `ingest_find_duplicates.py`, `rebuild_duplicates.py`,
`clusterize.py`, `ingestion_service.py`, or any review-UI sort order" to this follow-up, once real
distance data existed to calibrate against). Also follows directly from the same-day CLIP
threshold tightening (`duplicates.threshold` 0.3 → 0.12, commit `7c800b0`), which fixed the bulk
of Tier B's volume problem but left a residual false-positive class this spec targets.

## Problem

The CLIP-threshold tightening (same day, `general` environment) cut Tier B's unreviewed backlog
by ~85% (154,732 → ~24K raw pairs) by eliminating the dominant "same meme template genre, unrelated
content" false-positive class — nature-photo-plus-caption memes, cat-reaction two-panel memes,
etc. But it did **not** fix a structurally different failure mode, confirmed by direct visual
inspection during that same investigation: for **text-heavy images specifically** (chat/tweet
screenshots), CLIP produces false positives at *every* distance in the band, including the
tightest possible Tier B distance (0.050 — two completely different chat screenshots, different
senders, different jokes, sharing only "dark background + white text" styling). CLIP is a visual
embedding; it cannot see that the *words* differ, only that the *layout* is similar.

This is exactly the gap `docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md` was built to
close: that spec computed and stored a sentence embedding of each text-heavy image's OCR text,
explicitly to serve as a future duplicate-matching signal, but deliberately stopped short of
wiring it into anything. This spec does the wiring, using real threshold-calibration data
gathered today against `general`'s 4,083 stored OCR-text embeddings:

- Distance 0.025, 0.051: confirmed genuine near-duplicates (same repost, different crop/resolution)
- Distance 0.065–0.067: confirmed false positive — a *different* failure mode than CLIP's: a
  recurring boilerplate joke phrase ("А спонсор этого вечера...") shared verbatim across
  otherwise-unrelated images pulls their text embeddings artificially close (a "hub" effect from
  short/formulaic repeated text, not genuine content similarity)

## Goal

For text-heavy-vs-text-heavy image pairs specifically, replace CLIP with OCR-text-embedding
distance as the primary duplicate-matching signal, across both the ingestion Tier A/B pipeline and
the corpus-wide `rebuild_duplicates`/`clusterize` pipeline — while keeping a tight CLIP safety net
for near-pixel-identical reposts that OCR noise might otherwise push just outside the text-embedding
threshold. All other pairs (text-heavy vs. non-text-heavy, or neither text-heavy) are completely
unaffected — this spec touches zero matching behavior for them.

## Non-goals

- **Changing CLIP-based matching for any pair that isn't text-heavy-vs-text-heavy.** The
  same-day threshold tightening (0.3 → 0.12) already addressed the dominant false-positive class;
  this spec is scoped narrowly to the specific residual gap it didn't close.
- **A fourth signal, or closing every possible false-positive class.** The "hub" pathology from
  short/formulaic repeated OCR phrases (confirmed at distance 0.065–0.067) is a known, accepted
  imprecision of this signal — the loose threshold (0.10) is calibrated to sit below where it was
  observed to start, not to eliminate it entirely. A future spec may revisit this if it proves
  material at scale; not attempted here.
- **Retuning thresholds beyond today's calibration.** 0.05 tight / 0.10 loose / 0.02 safety-net
  are explicitly starting points from a single environment's data (`general`), the same way every
  other threshold in this pipeline (CLIP's own 0.05/0.12, the text-heavy classifier's three
  signal thresholds) started as an empirically-grounded first pass, not a final calibration.
- **Admin Duplicates page UI changes.** `Backend/app/repositories/image_repository.py`'s
  `get_duplicates_clustered` (the corpus-wide admin review page) never surfaces raw `distance` or
  `match_source` to begin with — only cluster membership. `clusterize.py`'s union-find gaining
  `distance_source`-aware edges changes *which* pairs get clustered, but nothing about how that
  page displays them. No changes needed there.
- **A distance_source-aware rewrite of `ClusterRow.tsx`'s multi-edge `edgeSummaryFor` aggregation.**
  A Tier A cluster member can now legitimately have edges from both sources (e.g. a safety-net CLIP
  edge to one text-heavy neighbor and an OCR-text edge to another). The existing `Math.min(...dists)`
  summary stays a plain informational string, not a value anything downstream compares or sorts by —
  correctness of matching/clustering never depends on this display string. Only `TierBReviewCard.tsx`
  (whose candidates are shown and read individually, not aggregated) gets a source label per Design
  §6.

## Design

### §1. Schema: `distance_source` column

`Storage/models.py`'s `TmpDuplicates` gains one column, directly after `match_source`:

```python
    # 'clip' | 'ocr_text' -- which embedding signal produced this row's distance. Orthogonal to
    # match_source (in_batch/cross_corpus): a pair can be any combination of the two. Existing
    # rows (all CLIP-sourced, from before this column existed) default to 'clip'. See
    # docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md.
    distance_source = Column(String(20), nullable=False, server_default="clip")
```

Migration: plain `alembic revision --autogenerate`, same shape as prior additive-column
migrations this session (e.g. `2026-09-11`'s `match_source` addition, if it exists as a separate
migration — otherwise mirror the most recent single-column-addition migration in
`Storage/alembic/versions/`). `server_default="clip"` means no backfill step is needed; existing
rows are correctly classified retroactively since every row in the table today came from the CLIP
probe.

### §2. The probe-set staleness problem (why this needs care, not just three more probe calls)

The natural first instinct — "call `find_duplicates()` three times per environment run: general
CLIP (now excluding text-heavy-vs-text-heavy), OCR-text, safety-net CLIP" — has a real bug.
`rebuild_duplicates.py`'s `_ACTIVE_PROBE_INCREMENTAL` selects images with **no existing
`tmp_duplicates` row at all** as the probe set:

```sql
WHERE i.status = 'active'
  AND NOT EXISTS (SELECT 1 FROM tmp_duplicates d WHERE d.image_id1 = i.id OR d.image_id2 = i.id)
```

If three probes run in sequence within one script invocation, the **second** probe's `NOT EXISTS`
check re-evaluates *after* the first probe's `INSERT` has already added rows for that image
(visible within the same transaction) — so the second and third probes would silently skip every
image the first probe just touched, even though they're looking for a completely different
signal's edges.

The fix: parameterize the incremental probe-set fragment by `distance_source`, so each probe
maintains its own independent "have I already been probed by *this* signal" bookkeeping:

```sql
WHERE i.status = 'active'
  AND NOT EXISTS (
      SELECT 1 FROM tmp_duplicates d
      WHERE (d.image_id1 = i.id OR d.image_id2 = i.id) AND d.distance_source = :probe_distance_source
  )
```

This is a real behavior change to `_ACTIVE_PROBE_INCREMENTAL`, not just an addition — it affects
today's existing CLIP-only incremental probing too (harmlessly: today, every row is `distance_source
= 'clip'`, so `AND d.distance_source = 'clip'` is a no-op restriction on the current data).

### §3. `batch/rebuild_duplicates.py` — generalizing `find_duplicates()`

`find_duplicates()` gains an `embedding_table: str` parameter (`"embeddings"` or
`"ocr_text_embeddings"` — both tables use the identical column name `embedding`, so only the table
name varies) and a `distance_source: str` parameter, and the probe-set fragment gets the
`:probe_distance_source` bind param from §2:

```python
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
```

`embedding_table` is an internally-fixed enum of two literal strings passed by call sites in this
codebase, never user input — f-string interpolation here matches this function's own existing,
already-documented convention for `probe_sql`/`corpus_filter_sql`.

New module-level constants and probe fragments:

```python
# Text-heavy-vs-text-heavy pairs are excluded from the general CLIP probe -- they're matched via
# OCR-text embeddings instead (see §4 below), plus a tight CLIP safety net. Appended to every
# existing CLIP corpus filter as an extra AND clause.
_EXCLUDE_TEXT_HEAVY_PAIR = """
    NOT (
        EXISTS (SELECT 1 FROM image_classifications tc1 WHERE tc1.image_id = probe.id
                AND tc1.classifier = 'text_heavy_v1' AND tc1.result = 'text_heavy')
        AND EXISTS (SELECT 1 FROM image_classifications tc2 WHERE tc2.image_id = i2.id
                    AND tc2.classifier = 'text_heavy_v1' AND tc2.result = 'text_heavy')
    )
"""

# Scoped to text-heavy-vs-text-heavy pairs only -- the inverse of the exclusion above, used by
# both the OCR-text probe (via the ocr_text_embeddings join itself, which only has rows for
# text_heavy images -- no explicit filter needed there) and the CLIP safety-net probe (which
# still queries the general `embeddings` table, so needs this filter explicitly).
_TEXT_HEAVY_PAIR_ONLY = """
    EXISTS (SELECT 1 FROM image_classifications tc1 WHERE tc1.image_id = probe.id
            AND tc1.classifier = 'text_heavy_v1' AND tc1.result = 'text_heavy')
    AND EXISTS (SELECT 1 FROM image_classifications tc2 WHERE tc2.image_id = i2.id
                AND tc2.classifier = 'text_heavy_v1' AND tc2.result = 'text_heavy')
"""

TEXT_EMBEDDING_TIGHT_THRESHOLD = 0.05   # auto-cluster-worthy OCR-text match
TEXT_EMBEDDING_LOOSE_THRESHOLD = 0.10   # review-worthy OCR-text match (Tier B upper bound)
CLIP_SAFETY_NET_THRESHOLD = 0.02        # near-pixel-identical repost, text-heavy pairs only
```

`_ACTIVE_CORPUS_FILTER` becomes source-specific:

```python
_ACTIVE_CORPUS_FILTER_CLIP = f"i2.status = 'active' AND ({_EXCLUDE_TEXT_HEAVY_PAIR})"
_ACTIVE_CORPUS_FILTER_CLIP_SAFETY_NET = f"i2.status = 'active' AND ({_TEXT_HEAVY_PAIR_ONLY})"
_ACTIVE_CORPUS_FILTER_OCR_TEXT = "i2.status = 'active'"  # ocr_text_embeddings join is already
                                                           # text_heavy-scoped by construction
```

`_ACTIVE_PROBE_INCREMENTAL` gains the `:probe_distance_source` filter from §2. `_ACTIVE_PROBE_FULL`
is unaffected (it has no incremental skip condition to begin with). A third probe variant is
needed for the OCR-text signal, since it must join `ocr_text_embeddings` instead of `embeddings`
for the probe side too:

```python
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

# Safety-net probe: deliberately NOT the same _ACTIVE_PROBE_INCREMENTAL/_FULL fragments the
# general CLIP call uses, and deliberately has no incremental skip condition at all -- see the
# explanation after rebuild_active_library() below for why sharing an incremental marker with
# the general probe is actually broken, not just imprecise. Scoped to active text_heavy images
# only (the corpus filter already requires this on the candidate side; scoping the probe side
# too avoids wastefully KNN-searching non-text-heavy images that could never match it anyway).
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
```

`rebuild_active_library()` now issues three `find_duplicates()` calls instead of one:

```python
async def rebuild_active_library(session, k: int, threshold: float, full: bool = False) -> int:
    if full:
        print("Full rebuild: clearing existing active-library candidate pairs...")
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
    # Deliberately NOT clip_probe_sql, and deliberately NO incremental skip condition -- see
    # below for why sharing the general probe's own incremental marker is genuinely broken here,
    # not just imprecise.
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
```

**Why the safety-net probe has no incremental skip condition at all, and doesn't reuse
`clip_probe_sql`:** an earlier draft of this section had it reuse `clip_probe_sql` with the same
`probe_distance_source="clip"` marker as the general call, reasoning that "an image already
touched by the general probe this run would still have `NOT EXISTS` return `TRUE` if the general
probe found zero matches for it." That reasoning only holds for an image the general probe found
*nothing* for. It breaks for the much more common case: a text-heavy image that also happens to
have some unrelated non-text-heavy CLIP-similar neighbor. The general probe (which does not
exclude that pairing — `_EXCLUDE_TEXT_HEAVY_PAIR` only excludes a pairing where *both* sides are
text-heavy) would correctly find and insert that unrelated match, giving the text-heavy image a
`distance_source='clip'` row. If the safety-net probe shared that same incremental marker, its own
`NOT EXISTS` check would now see that row and skip the image entirely — permanently, since nothing
ever re-triggers an incremental probe once *any* row of that `distance_source` exists — starving
the safety net of exactly the images it exists to protect, with no error or warning that it
happened. Scoping the safety-net probe to run unconditionally over every active text-heavy image,
every invocation (relying purely on `ON CONFLICT DO NOTHING` for idempotency, the same convention
`ingest_find_duplicates.py`'s own batch probes already use for a different reason), sidesteps this
entirely: it doesn't matter what the general probe did or didn't insert, because the safety net
never consults that bookkeeping at all. The text-heavy population is a small fraction of the
active corpus, so re-scanning it in full on every routine rebuild is cheap.

*(Self-review note: this section was rewritten twice during self-review. The first rewrite fixed a
narrower issue — two calls potentially finding the exact same pair. Writing the implementation
plan surfaced this broader, more serious issue: the shared marker doesn't just risk redundant
work, it risks silently and permanently disabling the safety net for any text-heavy image that
happens to also have an unrelated CLIP match. The fix — the safety-net probe never checks
incremental state at all — resolves both. This is the single most subtle piece of mechanics in
the whole spec; Task 2's own tests must verify it empirically (see the Testing section's new case
below), not just trust this prose.)*

`_process()` and `main()` are unaffected beyond `rebuild_active_library`'s internals — the public
CLI surface (`--k`, `--threshold`, `--full`) doesn't change; `--threshold` continues to mean "the
general CLIP probe's threshold" exactly as today, since the new OCR-text and safety-net thresholds
are fixed module constants, not yet exposed as CLI flags (no call site needs to override them
today — YAGNI per this spec's own Non-goals on threshold retuning).

### §4. `batch/ingest_find_duplicates.py` — mirroring the same split at both tiers

`_BATCH_CORPUS_FILTER_SQL` becomes three variants, mirroring §3:

```python
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
```

(`_EXCLUDE_TEXT_HEAVY_PAIR`/`_TEXT_HEAVY_PAIR_ONLY` imported from `batch.rebuild_duplicates` — not
redefined here, per this module's own existing convention of importing `find_duplicates` from
there rather than duplicating it.)

`_BATCH_PROBE_SQL` gains an OCR-text variant:

```python
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
```

`find_batch_duplicates()` becomes three calls per tier invocation:

```python
async def find_batch_duplicates(session, batch_id, k: int, threshold: float) -> int:
    """Populate tmp_duplicates with candidate pairs for `batch_id`'s pending images, at the
    given threshold, across all three signals (general CLIP excluding text-heavy pairs, CLIP
    safety net for text-heavy pairs, OCR-text for text-heavy pairs). Safe to call once per tier
    (Tier A tight, Tier B loose) -- a pair already found by an earlier, tighter call, or by a
    different signal, is a no-op via ON CONFLICT DO NOTHING.

    The OCR-text probe always runs at TEXT_EMBEDDING_LOOSE_THRESHOLD regardless of `threshold` or
    which tier is calling -- deliberately, not by coincidence. This mirrors this file's own
    already-established convention for the CLIP probe itself: the tier_b CLIP call already
    inserts generously all the way down to distance 0 (there is no separate tight variant of
    `threshold` passed for tier_b), relying entirely on the review query's own band filtering
    (`get_tier_candidate_rows`/`list_tier_b_review_page`'s `distance >= low AND distance < high`)
    to decide which UI tier a stored row actually surfaces in -- not on which probe call inserted
    it. Deriving a separate "tight" OCR-text threshold for tier_a via e.g. `min(threshold,
    TEXT_EMBEDDING_LOOSE_THRESHOLD)` would only produce the right number today because
    TIER_A_THRESHOLD and TEXT_EMBEDDING_TIGHT_THRESHOLD happen to both be 0.05 -- exactly the kind
    of cross-scale coincidence this spec's own §6 warns against relying on elsewhere. Always using
    the loose bound here avoids that trap entirely; the review query's own bands are what actually
    enforce tier_a vs. tier_b, identically to how CLIP already works in this file today."""
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
```

Import additions: `from batch.rebuild_duplicates import (find_duplicates,
_EXCLUDE_TEXT_HEAVY_PAIR, _TEXT_HEAVY_PAIR_ONLY, CLIP_SAFETY_NET_THRESHOLD,
TEXT_EMBEDDING_LOOSE_THRESHOLD)`.

No incremental probe-set staleness concern here (unlike §3) — `_BATCH_PROBE_SQL` has no `NOT
EXISTS` skip condition at all; it probes every pending image in the batch every time this script
runs, relying entirely on `ON CONFLICT DO NOTHING` for idempotency (matches this file's own
existing docstring: "a pair already found by an earlier, tighter call is a no-op"). Adding two
more calls per tier doesn't change that shape.

### §5. `batch/clusterize.py` — `distance_source`-aware threshold comparison

Two module constants replace the flat `PROXIMITY_THRESHOLD` for the *union-find edge-acceptance*
decision (splitting logic in `resolve_cluster` stays source-agnostic per this spec's own Non-goals
— see below):

```python
PROXIMITY_THRESHOLD = 0.05  # CLIP -- unchanged, still the general "confirmed duplicate" cutoff
PROXIMITY_THRESHOLD_OCR_TEXT = 0.05  # matches TEXT_EMBEDDING_TIGHT_THRESHOLD in rebuild_duplicates.py
```

(Two separately-named constants, not one shared import, even though they're numerically equal
today — matches this spec's own principle of never assuming the two scales stay coupled; a future
change to either doesn't silently move the other.)

`get_duplicate_pairs()`'s query changes from a flat `distance < threshold` to a
`distance_source`-aware OR:

```python
async def get_duplicate_pairs(session, mapping, clip_threshold, ocr_text_threshold) -> list[tuple[int, int, float]]:
    decided_pair_exists = (
        select(DuplicateDecision.id)
        .where(
            DuplicateDecision.image_id1 == TmpDuplicates.image_id1,
            DuplicateDecision.image_id2 == TmpDuplicates.image_id2,
        )
        .exists()
    )
    query = (
        select(
            TmpDuplicates.image_id1,
            TmpDuplicates.image_id2,
            TmpDuplicates.distance,
        ).where(
            or_(
                and_(TmpDuplicates.distance_source == "clip", TmpDuplicates.distance < clip_threshold),
                and_(TmpDuplicates.distance_source == "ocr_text", TmpDuplicates.distance < ocr_text_threshold),
            ),
            TmpDuplicates.image_id1 != TmpDuplicates.image_id2,
            ~decided_pair_exists,
        )
    )
    duplicates = await session.execute(query)
    return [
        (mapping[id1], mapping[id2], distance)
        for id1, id2, distance in duplicates
        if id1 in mapping and id2 in mapping
    ]
```

`cluster_active_library()`'s call site updates: `pairs = await get_duplicate_pairs(session,
img_id_to_int_id, PROXIMITY_THRESHOLD, PROXIMITY_THRESHOLD_OCR_TEXT)`.

Import additions: `from sqlalchemy import and_, or_, select, delete` (was `select, delete`).

**`resolve_cluster`'s recursive splitting stays source-agnostic** (a deliberate scoping decision,
not an oversight): once two members are already unioned into one connected component — because
*either* signal found them close enough to pass the check above — progressively tightening the
threshold to split an oversized cluster is a purely quantitative "keep shrinking toward the
tightest sub-groups" operation. Mixing sources there doesn't reintroduce the original problem
(a wrong signal admitting an edge that shouldn't exist at all); it only affects which of several
*already-admitted* edges survive a further tightening pass. Revisiting this is explicitly
out of scope (see Non-goals).

### §6. Backend: repository, service, API schema

`Backend/app/repositories/ingestion_repository.py`'s `get_tier_candidate_rows` SELECTs
`TmpDuplicates.distance_source` alongside the existing `match_source`; `list_tier_b_review_page`'s
two SQL blocks (`pair_cte`, the `ranked`/final `cand_sql`) both add `td.distance_source` /
`r.distance_source` to their SELECT lists (no WHERE/ORDER BY changes needed — see the tier-banding
note below).

**Why no tier-banding SQL changes are needed:** `_tier_band("tier_a")` returns `(0.0,
PROXIMITY_THRESHOLD)` = `(0.0, 0.05)`; the CLIP safety-net probe's threshold (0.02) is *tighter*
than 0.05, so its rows land inside Tier A's existing band automatically. `_tier_band("tier_b")`
returns `(0.05, settings.DUPLICATES.THRESHOLD)` = `(0.05, 0.12)`; the OCR-text probe's loose
threshold (0.10) is *inside* that range, so its rows land inside Tier B's existing band
automatically too. Both new signals fit inside the numeric bands that already exist — the
`get_tier_candidate_rows`/`list_tier_b_review_page` WHERE clauses filtering on `distance >= low
AND distance < high` need zero changes; they were already source-agnostic range filters, and both
signals' calibrated ranges happen to nest inside the existing CLIP-defined bands. (This is a
direct consequence of today's specific calibration choices — tight=0.05, loose=0.10 were chosen
*because* they fit inside CLIP's own tight=0.05/loose=0.12 bands, not a coincidence to rely on
if either threshold changes independently later.)

`Backend/app/services/ingestion_service.py`: `list_clusters`'s `edges.append({...})` and
`list_tier_b_review`'s candidate-building both add `"distance_source": row.distance_source` /
`c.distance_source` to their dicts, mirroring exactly how `match_source` is already threaded
through both methods today.

`Backend/app/api/ingestion.py`: `ClusterEdge` and `TierBCandidate` both gain
`distance_source: Optional[str]`, directly below their existing `match_source: Optional[str]`
field — the same hand-written-response-model pattern CLAUDE.md's own gotcha documents (these two
classes are NOT backed by `shared/schemas/`-generated types; they're endpoint-specific inline
models per this router's existing convention, confirmed by reading the file directly rather than
assuming).

`shared/schemas/ingestionclusteredge.schema.json` and
`shared/schemas/ingestiontierbcandidate.schema.json` (if these exist as separate generated-type
schema files distinct from the hand-written Pydantic models above — confirm at implementation
time; if `ClusterEdge`/`TierBCandidate` are purely hand-written with no corresponding schema file,
this bullet is a no-op) gain the matching field, followed by a full three-tree regeneration
(TypeScript, Kotlin, Python) per `documents/generation.md`.

### §7. Frontend

`Frontend/memes-frontend/src/components/ingestion/TierBReviewCard.tsx`'s candidate `edgeSummary`
string construction gains a source label:

```tsx
edgeSummary={`${c.distance.toFixed(3)} · ${c.distance_source === "ocr_text" ? "text" : "visual"} · ${c.match_source ?? "?"}`}
```

`ClusterRow.tsx` is explicitly unchanged (see Non-goals).

## Testing

- **`batch/tests/test_rebuild_duplicates_*.py`**: the existing `find_duplicates()` tests need
  their INSERT-statement assertions updated for the new `distance_source` column and parameter
  (mechanical). New tests: the text-heavy-pair exclusion filter genuinely excludes a
  text-heavy-vs-text-heavy pair from the general CLIP probe; the safety-net probe genuinely finds
  a tight text-heavy-vs-text-heavy CLIP match the general probe excluded; the OCR-text probe
  finds a pair via `ocr_text_embeddings`; the §2 incremental-probe-staleness fix — call
  `find_duplicates()` twice in sequence within one test (CLIP then OCR-text) against the same
  probe image and confirm the second call is NOT silently skipped (this is a regression test for
  the first, narrower incremental-staleness bug this spec's design caught); **the broader
  safety-net-starvation regression** — a text-heavy image A with both a non-text-heavy CLIP
  neighbor C (found by the general probe, inserting a `distance_source='clip'` row for A) and a
  genuine tight text-heavy near-duplicate B — call `rebuild_active_library()` once and confirm
  *both* the (A,C) and (A,B) pairs get inserted; this must fail if the safety-net probe is
  changed back to share the general probe's own incremental marker, and is the regression test
  for the second, more serious bug this spec's design caught during plan-writing.
- **`batch/tests/test_ingest_find_duplicates_*.py`**: mirror the above for
  `find_batch_duplicates()` — three calls happen per invocation, each with correct scoping.
- **`batch/tests/test_clusterize*.py`**: `get_duplicate_pairs` returns a CLIP-sourced pair under
  `clip_threshold` but not `ocr_text_threshold` (and vice versa) — the two thresholds must be
  independently enforced per-row, not OR'd loosely (a test using two *different* threshold values
  for the two params is the only way to actually prove this, not just pass both at 0.05).
- **`tests/integration/`**: real-DB tests for the full pipeline — `rebuild_duplicates.py`'s three
  probes against real `images`/`embeddings`/`ocr_text_embeddings`/`image_classifications` rows;
  `ingest_find_duplicates.py`'s three probes against a real pending batch;
  `list_tier_b_review_page`/`get_tier_candidate_rows` returning the new column correctly.
- **`Backend/tests/`**: `ClusterEdge`/`TierBCandidate` serialize `distance_source`; a live
  `docker run` import-chain check is NOT needed here (this spec touches no new module-level
  imports in anything Backend-reachable — `image_classifications` and `ocr_text_embeddings` are
  both already-existing tables/models, not new dependencies).
- **Frontend**: `TierBReviewCard.test.tsx` gains a case asserting the label text for an
  `ocr_text`-sourced candidate.

## Rollout

1. Ship the migration + code to all three files (`rebuild_duplicates.py`,
   `ingest_find_duplicates.py`, `clusterize.py`) plus the Backend/frontend plumbing, on a branch,
   through the same SDD process as every prior piece of this multi-week effort.
2. Apply the migration to `metal`/`general`/`it` (additive column, `server_default`, no backfill,
   no existing-data risk).
3. Run `rebuild_duplicates.py --env <environment>` (incremental, chains to `clusterize.py`
   automatically per its existing `--no-chain` default) against each environment to populate
   OCR-text and safety-net CLIP edges for the existing active-library text-heavy population, and
   re-cluster. **Live-database write against a continuously-running dev environment — explicit
   go-ahead required before this step, per this session's established pattern.**
4. Verify via `DATABASE_URL_READONLY`: count of new `distance_source='ocr_text'` rows per
   environment; spot-check a handful of newly-surfaced pairs by opening the actual image files
   (same manual-verification approach used throughout this whole effort).
5. For `general` specifically (the environment with an in-flight ingestion batch): re-run
   `ingest_find_duplicates --tier tier_a` and `--tier tier_b` against the current pending batch,
   so it benefits from the new signal too, not just future batches.
6. Spot-check the live Tier B review UI (`/ingestion`) to confirm the `distance_source` label
   renders correctly and the queue's remaining false-positive rate for text-heavy pairs visibly
   drops.
7. Mark this spec `done` with a Rollout Outcome section (counts per environment, spot-check
   findings), matching every prior spec's rollout-outcome convention this session.

## Known limitation: OCR-text auto-clustering disabled pending recalibration

Found during Step 4's live-rollout spot-check on 2026-09-19, against real `general` data (all
three environments already migrated and rebuilt by that point).

**What was found:** the initial spot-check (a handful of tight `ocr_text`-sourced pairs) found 2
false positives out of 6 checked, both well inside `clusterize.py`'s auto-cluster threshold
(0.0454 and 0.0488, both < `PROXIMITY_THRESHOLD_OCR_TEXT` = 0.05) — meaning they were being
auto-confirmed as duplicates in `general`'s live Explore → Duplicates page with zero human
review. Pulling the full distribution of `general`'s 191 `ocr_text`-sourced pairs (not just the
tightest few) showed this was systemic, not two edge cases:

- **Hub effect**: dozens of images each appeared in 11-20 different "duplicate" pairs. Genuine
  reposting produces small pairs/groups; a 20-way hub is the signature of spurious clustering
  (directly analogous to the CLIP "same template, not actual duplicate" hub pathology this whole
  feature was built to fix — recurring in the text signal instead).
- **Root cause, confirmed by direct visual/OCR-text inspection**: informal, profanity-heavy,
  exclamatory meme captions (crude "rage comic"-style content, and separately, badly-OCR'd
  handwriting/stylized fonts that EasyOCR confidently misreads as different-but-still-plausible
  Cyrillic text) cluster together in `paraphrase-multilingual-MiniLM-L12-v2` embedding space by
  **register/style**, not actual joke content. Two entirely unrelated images sharing "crude, short,
  ALL-CAPS Cyrillic exclamations" as their dominant textual register can land well inside the
  tight auto-cluster threshold despite having nothing in common.
- **A CLIP-distance corroboration gate (require some baseline visual similarity too) was
  considered and rejected as insufficient on its own**: `general`'s 191 `ocr_text` pairs' CLIP
  distances were pulled and checked against 3 more confirmed examples. 78/191 (41%) had CLIP
  distance >= 0.35 (almost certainly visually unrelated — a corroboration gate would cleanly
  reject these). But the ambiguous middle band (0.25-0.35 CLIP distance, 66/191 pairs, over a
  third of the total) contained BOTH a confirmed genuine repost (0.32, same joke posted to two
  different platforms with different visual chrome) AND a confirmed false positive (0.27,
  unrelated hand-drawn map vs. an unrelated cartoon) — CLIP distance alone does not cleanly
  separate the two in that band. A real fix needs something more targeted than a single corroborating
  distance threshold (candidate directions for a follow-up: per-pair OCR-text substance/length
  gating, since the confirmed true positives all had long, distinctive, well-recognized text while
  the confirmed false positives had either very short/fragmented text or heavily garbled OCR;
  possibly a different or fine-tuned embedding model better suited to short informal Cyrillic
  meme captions specifically).

**Decision:** rather than block the whole feature on solving this open calibration question,
`batch/clusterize.py`'s `get_duplicate_pairs()` was scoped back to CLIP-only for active-library
auto-clustering (`ocr_text`-sourced pairs are now unconditionally excluded from auto-confirmed
clusters, regardless of distance). This removes the acute live harm (false duplicates silently
shown as confirmed on a public-facing page) with a small, surgical, easily-reversible change.
**Ingestion's Tier A/B review is unaffected** — `ingest_find_duplicates.py` still inserts
`ocr_text`-sourced candidate rows into `tmp_duplicates` exactly as designed, and the Backend's
review API (`get_tier_candidate_rows`/`list_tier_b_review_page`) still surfaces them, because that
path reads `tmp_duplicates` directly and was never routed through `get_duplicate_pairs()` — a
human reviewer, not an unsupervised auto-cluster, makes the final call there, which is an
acceptable-risk surface for the same noisy signal in a way active-library auto-clustering is not.

**Follow-up required before re-enabling active-library auto-clustering for `ocr_text` pairs:** a
properly recalibrated design addressing the false-positive rate found above — this is future work,
not scheduled as part of this spec's own rollout. `PROXIMITY_THRESHOLD_OCR_TEXT` in
`batch/clusterize.py` is kept defined (unused) rather than deleted, to save re-deriving it later.

## Self-Review

Performed inline per the brainstorming skill's Architectural-path requirement (fresh eyes against
the spec, not a subagent dispatch):

**Caught and fixed across two self-review passes — the initial pass while writing this document,
and a second pass while writing the implementation plan, which is exactly what the plan's own
"argue from the spec" role is for (three issues total, all fixed in place, none left for the
implementer):**

1. §3's first draft claimed the safety-net CLIP probe and the general CLIP probe "can't both run
   for the same image in one invocation" without explaining why in a way that survived a second
   read. The first rewrite (during this document's own initial self-review) explained the shared-
   marker mechanism but only checked the narrow case of both calls finding the *same* pair. Writing
   the implementation plan surfaced the real, broader bug this was hiding: if the general probe
   finds and inserts a row for a text-heavy image from an *unrelated* non-text-heavy match (a
   common case, not an edge case — `_EXCLUDE_TEXT_HEAVY_PAIR` only excludes pairings where *both*
   sides are text-heavy), a safety-net probe sharing that same incremental marker would then be
   silently and *permanently* skipped for that image, defeating the safety net's entire purpose
   for exactly the images it exists to protect. Fixed by giving the safety-net probe no
   incremental skip condition at all — see §3's own explanation, rewritten a second time, for the
   final design and why it's correct. This was the single most subtle piece of mechanics in the
   whole spec, and it took two full passes (writing the spec, then writing the plan) to get right —
   a real demonstration of why the plan's own self-review step matters, not a formality.
2. **A genuine correctness bug**, not just a clarity issue: §4's first draft derived Tier A's
   OCR-text probe threshold via `min(threshold, TEXT_EMBEDDING_LOOSE_THRESHOLD)`, which only
   produces the intended tight value (0.05) because `TIER_A_THRESHOLD` and
   `TEXT_EMBEDDING_TIGHT_THRESHOLD` happen to be numerically equal today — the exact "coincidence
   to rely on" fragility §6 explicitly warns against, just recreated in a different section of the
   same document. Fixed by always using `TEXT_EMBEDDING_LOOSE_THRESHOLD` at both tiers instead,
   matching the existing file's own established convention (the review query's bands, not the
   probe's threshold, already determine which UI tier a row surfaces in — confirmed by re-reading
   `ingest_find_duplicates.py`'s own docstring, which already documents this exact philosophy for
   the CLIP probe: "Tier B... the 0.05 lower bound... is enforced by the review query, not here").
   This would have shipped a real, silent bug the first time either threshold was recalibrated
   independently — caught only by deliberately re-checking every threshold-deriving expression
   against the spec's own stated fragility principle, not by reading the code in isolation.
3. `rebuild_active_library()`'s first draft had an unnecessary `extra_params=... if not full else
   None` ternary plus a pointless `extra = {}` dict that was spread into another dict and did
   nothing. Removed — SQLAlchemy's `text()` execution ignores unused params dict keys, so the
   conditional added complexity without changing behavior.

**Placeholder scan:** none found — every code block is complete, real code, not a sketch.

**Spec coverage vs. the originating Non-goals list:** `distance_source` column (✓ §1),
text-embedding probe scoped to text-heavy-vs-text-heavy pairs with its own tight/loose thresholds
mirroring `PROXIMITY_THRESHOLD`/`DUPLICATES.THRESHOLD`'s shape (✓ §3/§4, values 0.05/0.10), tight
CLIP safety-net probe (✓ §3/§4, 0.02), `clusterize.py` union-find accepting `ocr_text`-sourced
edges at their own threshold (✓ §5), `distance_source`-aware sorting/display in ingestion review
(✓ §6/§7 — "grouped before raw distance" from the original design is satisfied structurally,
since both signals' calibrated ranges nest inside the existing tier bands rather than needing an
explicit sort-key change; the *display* label is what actually prevents a reviewer from comparing
the two scales directly, which was the original design's real concern). Corpus-wide admin
Duplicates page: confirmed via direct file read that it doesn't surface raw distance, so
correctly scoped as a Non-goal rather than missed.

**Type/name consistency check:** `distance_source` spelled identically everywhere (model column,
SQL params, Pydantic fields, TS field access) — no `distanceSource`/`distance_src` drift.
`embedding_table` parameter name doesn't collide with any existing `find_duplicates()` parameter.
`CLIP_SAFETY_NET_THRESHOLD`/`TEXT_EMBEDDING_TIGHT_THRESHOLD`/`TEXT_EMBEDDING_LOOSE_THRESHOLD`
each used consistently at their one definition site and every import site.
