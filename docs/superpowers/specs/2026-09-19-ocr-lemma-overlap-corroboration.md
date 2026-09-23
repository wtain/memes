# OCR-Lemma Overlap Corroboration for Text-Embedding Duplicate Matching

status: done
Plan: `docs/superpowers/plans/2026-09-19-ocr-lemma-overlap-corroboration.md`
Originates from: `docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md`'s
"Known limitation" section (live-rollout finding, 2026-09-19) — that spec shipped OCR-text-embedding
distance as the primary duplicate signal for text-heavy image pairs, but the live rollout found a
genuine false-positive "hub" pattern too severe to auto-confirm without human review, and disabled
active-library auto-clustering for `ocr_text`-sourced pairs pending a properly recalibrated design.
This spec is that recalibration.
Follow-ups: `docs/superpowers/specs/2026-09-20-clusterize-oversized-cluster-data-loss.md` (a
pre-existing, unrelated `clusterize.py` defect discovered during this spec's own Task 7 rollout
verification — `general`'s Explore → Duplicates page showing zero items, confirmed independent of
this spec's changes and spun out as its own follow-up); `docs/superpowers/specs/2026-09-23-duplicate-matching-coverage-gap-diagnostics.md`
(the operator-facing coverage-gap warning this spec's own final-review finding (1) called for).

## Problem

`2026-09-18-text-embedding-duplicate-matching.md` replaced CLIP with OCR-text-embedding distance
(`paraphrase-multilingual-MiniLM-L12-v2`) as the primary duplicate signal for text-heavy-vs-text-heavy
image pairs, on the theory that CLIP conflates "same visual template" with genuine duplication and a
text-content signal would discriminate better. It does, for well-formed content — but the live
rollout against `general`'s real 4,083-image text-heavy population found the embedding distance
alone reproduces the *same class* of failure it was built to fix, just one level down: informal,
short, exclamatory meme captions (crude "rage comic" content; also OCR text EasyOCR confidently
misreads from handwriting/stylized fonts) cluster together in embedding space by **register/style**,
not actual content. Two unrelated jokes sharing "crude, ALL-CAPS, profanity-heavy" tone can land
well inside the tight auto-cluster threshold (0.05) with nothing else in common.

Concrete evidence from the live investigation (full writeup in the originating spec's "Known
limitation" section):

- 2 of the first 6 manually-verified `general` pairs were confirmed false positives, both already
  auto-confirmed as duplicates on the live Explore → Duplicates page before this was caught.
- Pulling the full 191-pair `ocr_text` candidate distribution for `general` found a hub effect
  (dozens of images each in 11–20 different "duplicate" pairs) and 41% of pairs at CLIP distance
  ≥ 0.35 (almost certainly visually unrelated).
- A CLIP-distance "corroboration" gate (require some baseline visual similarity too) was
  considered and rejected: the ambiguous 0.25–0.35 CLIP-distance band contained both a confirmed
  true positive (0.32) and a confirmed false positive (0.27) — CLIP distance doesn't cleanly
  separate the two there.

The interim fix (already shipped) scoped `clusterize.py`'s active-library auto-clustering back to
CLIP-only. `ocr_text`-sourced candidate pairs still get inserted into `tmp_duplicates` and still
reach ingestion's human-reviewed Tier A/B queue — an acceptable-risk surface for the same noisy
signal, but still real reviewer-facing noise, and the auto-clustering gap remains open.

### The signal that actually separates them: lexical overlap, not distance

Investigation for this spec (2026-09-19, same live `general` data) found that **neither** signal
already computed for text-heavy images cleanly separates true from false positives:

- OCR-text embedding distance: false positives span 0.045–0.10, indistinguishable from true
  positives' 0.05–0.10 range — this is the whole problem this spec exists to fix.
- CLIP distance (already rejected in the originating spec, reconfirmed here): overlapping ranges.
- Raw OCR text length / block count: true positives 334–950 chars, false positives 424–5,238
  chars — heavy overlap, no clean boundary.

But **lexical overlap between the two images' `ocr_lemmas` sets** — the per-image lemma index
`build_ocr_lemmas.py` already computes and stores for smart search, entirely independent of this
feature — is dramatically clean. Measured as the **overlap coefficient**
(`shared_lemmas / min(lemma_count_1, lemma_count_2)`) across 12 hand-verified examples (opening the
actual image files) plus the *complete* 191-pair `general` distribution (not a sample):

| Confirmed true positives (4, all environments) | overlap coefficient |
|---|---|
| Same joke poem, two screenshots (`general`) | 0.737 |
| Same tweet, cropped differently (`metal`) | 1.000 |
| Same DDoS-joke text, two platforms (`it`) | 0.727 |
| Same "3 stages of life" template, different comment | 0.417 |
| Same "you give them a word..." tweet, two reposts | 0.889 |
| Same "accordion during sex" chat screenshot, two crops | 0.815 |

| Confirmed false positives (4) | overlap coefficient |
|---|---|
| Handwritten neighbor-noise note vs. unrelated comic | 0.000 |
| "Idiot certificate" joke vs. absurdist fake metro map (biggest hub, 635 OCR blocks) | 0.000 |
| Hand-drawn park map vs. unrelated delivery-service comic | 0.000 |
| Squash-shaped-like-a-swan meme vs. unrelated handwritten note | 0.033 |

**Full 191-pair `general` distribution** (not cherry-picked — every `ocr_text`-sourced candidate
pair that existed at rollout time): 13 pairs at overlap coefficient ≥ 0.417; **zero pairs** between
0.125 and 0.417; the remaining 178 pairs (109 at exactly 0.000, the rest trailing from 0.125 down)
form the noise cluster. This is a genuinely empty gap in real, complete production data, not an
artifact of a small hand-picked sample.

**Why the overlap *coefficient*, not raw shared-lemma count**: raw intersection count is fooled by
large "hub" texts purely through volume. The absurdist fake-metro-map image (635 OCR blocks, 440
distinct lemmas) coincidentally shares 4 lemmas with a completely unrelated doctor's-office
greentext story (both happen to reference "поликлиника"/clinic-adjacent vocabulary) — a raw
threshold of "≥4 shared lemmas" would have wrongly accepted this pair. Normalized by the *smaller*
image's lemma count, that same pair scores 0.042 (4/96) — correctly rejected, comfortably inside
the false-positive cluster. The confirmed true positive with the fewest lemmas ("you give them a
word..." tweet, 9 lemmas on the smaller side) still scores 0.889.

## Goal

Require a pair to also pass a lexical-overlap corroboration check, computed from the two images'
already-stored `ocr_lemmas` sets, before an OCR-text-embedding-sourced candidate pair is trusted —
for **both** active-library auto-clustering and ingestion Tier A/B review, not just the
auto-clustering path the interim fix addressed. Once this ships and is validated, re-enable
`clusterize.py`'s `ocr_text`-sourced auto-clustering (reverting the interim CLIP-only scoping),
since every `ocr_text` row reaching `tmp_duplicates` will already have passed the corroboration
check at insertion time.

## Non-goals

- **Retuning the OCR-text embedding distance thresholds themselves**
  (`TEXT_EMBEDDING_TIGHT_THRESHOLD`/`TEXT_EMBEDDING_LOOSE_THRESHOLD`/`CLIP_SAFETY_NET_THRESHOLD`,
  from the originating spec). The embedding distance remains the KNN candidate-retrieval mechanism
  (cheap, already HNSW-indexed); this spec adds a corroboration gate on top of it, not a
  replacement for it.
- **A new tokenization/lemmatization pipeline.** `ocr_lemmas` is fully reused as-is — same
  `rules/normalize.py` lemmatization, same `confidence_min`/`lang_score_min` quality filter
  (`settings.OCR.*`), same table. No new NLP infrastructure.
- **Touching CLIP-sourced matching in any way.** This spec is scoped entirely to the `ocr_text`
  probe's own insertion criteria.
- **A new schema column, API field, or frontend change.** The corroboration check gates whether a
  row is inserted at all; it doesn't change what a row looks like once inserted. `distance_source`
  stays exactly `'clip'`/`'ocr_text'` as already shipped — no new value, no new field.
- **Closing every possible false-positive class.** The empty 0.125–0.417 gap is real, but is
  calibrated against `general`'s corpus specifically (2026-09-19). `metal`/`it` have far fewer
  `ocr_text` candidate pairs today (1 each) to cross-validate against — see Design §1's caveat.

## Design

### §1. The corroboration check

New module constants in `batch/rebuild_duplicates.py` (mirroring `TEXT_EMBEDDING_TIGHT_THRESHOLD`'s
existing placement and comment style):

```python
# Corroboration gate for the OCR-text probe specifically -- see
# docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md. Computed from
# ocr_lemmas (already populated by build_ocr_lemmas.py for smart search, reused as-is here) rather
# than the OCR-text embedding itself, because embedding distance alone was shown NOT to separate
# true from false positives (both span 0.05-0.10) -- lexical overlap does, cleanly, on real data.
MIN_LEMMA_OVERLAP_COEFFICIENT = 0.2  # shared_lemmas / min(lemma_count_1, lemma_count_2);
                                      # calibrated against general's real 191-pair distribution --
                                      # zero pairs observed between 0.125 and 0.417, so 0.2 sits in
                                      # the middle of a genuinely empty gap, not a guessed round number
MIN_LEMMA_COUNT_FLOOR = 3            # guards the degenerate case where an image has 1-2 total
                                      # lemmas, where a single coincidental shared word would
                                      # otherwise score 50-100% overlap
```

**Caveat carried forward, not resolved here**: this calibration is validated against `general`'s
complete 191-pair distribution (2026-09-19) plus 12 individually-verified examples spanning all
three environments. `metal`/`it` each had only 1 `ocr_text` candidate pair at rollout time — too
few to independently validate the 0.2 threshold in isolation for those environments. The design
relies on the mechanism (lexical overlap directly measuring shared content words) generalizing
across environments/corpora, which is a reasonable expectation given `ocr_lemmas` already covers
Russian, English, and Spanish OCR text corpus-wide, not a `general`-specific artifact — but this is
flagged, not asserted, as the calibration's known limit.

### §2. SQL mechanics — where this plugs into `find_duplicates()`

`find_duplicates()` (`batch/rebuild_duplicates.py`) gains one new optional parameter:

```python
async def find_duplicates(session, probe_sql: str, corpus_filter_sql: str, k: int, threshold: float,
                           distance_source: str, embedding_table: str = "embeddings",
                           extra_params: dict | None = None, extra_where_sql: str | None = None) -> int:
```

`extra_where_sql`, when provided, is interpolated into the **outer** SELECT's WHERE clause —
alongside `WHERE nn.distance < :threshold`, not inside the LATERAL's own WHERE (which holds
`corpus_filter_sql`). This placement is deliberate: the LATERAL's `ORDER BY ... LIMIT :k` already
narrows every probe image's candidates down to at most `k` (50) neighbors *before* the corroboration
check needs to run, so the lemma-overlap computation — two count subqueries plus an intersection
join — only ever executes on that small, already-distance-filtered set, not on every candidate the
HNSW index considers during the approximate search. Putting it inside the LATERAL instead would run
it against a much larger candidate pool per probe image, with no equivalent index to make it cheap.

```python
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
  {extra_where_clause}
ON CONFLICT (image_id1, image_id2) DO NOTHING
```

Where `extra_where_clause` is `f"AND ({extra_where_sql})"` if `extra_where_sql` is provided, else
empty — matching the existing `f"AND ({corpus_filter_sql})"` pattern already used one level up, so
this isn't a new interpolation idiom for the file.

New module constant, the lemma-overlap fragment itself (references `probe.id` and `nn.image_id`,
both in scope at the point this is interpolated — the outer SELECT, after the LATERAL has already
resolved):

```python
# References probe.id and nn.image_id -- only valid interpolated into find_duplicates()'s OUTER
# WHERE clause (after the LATERAL resolves nn), never into corpus_filter_sql (which runs inside
# the LATERAL, before nn exists). See MIN_LEMMA_OVERLAP_COEFFICIENT's own comment for calibration.
_OCR_LEMMA_OVERLAP_CHECK = """
    (SELECT LEAST(
        (SELECT count(*) FROM ocr_lemmas WHERE image_id = probe.id),
        (SELECT count(*) FROM ocr_lemmas WHERE image_id = nn.image_id)
    )) >= :min_lemma_count
    AND (
        (SELECT count(*) FROM ocr_lemmas a JOIN ocr_lemmas b ON a.lemma = b.lemma
         WHERE a.image_id = probe.id AND b.image_id = nn.image_id)::float
        / GREATEST((SELECT LEAST(
            (SELECT count(*) FROM ocr_lemmas WHERE image_id = probe.id),
            (SELECT count(*) FROM ocr_lemmas WHERE image_id = nn.image_id)
        )), 1)
    ) >= :min_overlap_coefficient
"""
```

`rebuild_active_library()`'s OCR-text `find_duplicates()` call site gains
`extra_where_sql=_OCR_LEMMA_OVERLAP_CHECK` and the two new bind params:

```python
    inserted += await find_duplicates(
        session, ocr_probe_sql, _ACTIVE_CORPUS_FILTER_OCR_TEXT, k, TEXT_EMBEDDING_LOOSE_THRESHOLD,
        distance_source="ocr_text", embedding_table="ocr_text_embeddings",
        extra_params={
            "probe_distance_source": "ocr_text",
            "min_lemma_count": MIN_LEMMA_COUNT_FLOOR,
            "min_overlap_coefficient": MIN_LEMMA_OVERLAP_COEFFICIENT,
        },
        extra_where_sql=_OCR_LEMMA_OVERLAP_CHECK,
    )
```

`batch/ingest_find_duplicates.py`'s OCR-text probe call (`find_batch_duplicates()`) gets the
identical treatment — same constants imported from `rebuild_duplicates`, same `extra_where_sql`,
same two extra params merged into its existing `extra_params={"batch_id": batch_id}` dict.

**Not changed**: the general CLIP probe and the CLIP safety-net probe. Neither passes
`extra_where_sql` — the corroboration gate is `ocr_text`-probe-specific, exactly like
`TEXT_EMBEDDING_LOOSE_THRESHOLD` and `_EXCLUDE_TEXT_HEAVY_PAIR`/`_TEXT_HEAVY_PAIR_ONLY` already are.

### §3. Prerequisite: `ocr_lemmas` coverage gap

`ocr_lemmas` is `build_ocr_lemmas.py`'s own output, already maintained corpus-wide for smart search
— but it lags `ocr_text_embeddings` today. Measured on `general`: 742 images have an
`ocr_text_embeddings` row but zero `ocr_lemmas` rows; of those, 737 were simply never processed by
`build_ocr_lemmas.py` yet (an operational lag, not a structural problem — confirmed via
`image_processing_status`), and only 5 were genuinely processed with zero lemmas surviving the
quality filter (a real, expected edge case for very short/low-quality text).

This is the same class of gap Task 7 found and fixed for `build_ocr_text_embeddings.py` not being
wired into `ingest_auto_prep`. The fix here is smaller: `build_ocr_lemmas.py`'s own repository
layer (`ImagesRepository.get_images_and_ocr_texts_without_lemmas_with_language`) already accepts a
`status: str = "active"` parameter — the CLI and `main()`/`run()` just don't expose it yet. Add
`--status` (mirroring `classify_text_heavy.py`'s and `build_ocr_text_embeddings.py`'s existing
`--status {active,pending}` convention exactly) and thread it through:

```python
async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True,
                status: str = "active") -> None:
    ...
    await _process(incremental=incremental, status=status)
```

```python
    parser.add_argument("--status", choices=["active", "pending"], default="active")
    ...
    asyncio.run(main(incremental=args.incremental, status=args.status))
```

`_process()`/`run()` thread `status` down to the repository calls the same way
`build_ocr_text_embeddings.py`'s `run()` already does for its own analogous parameter — no new
pattern, just applying the existing one to this file.

`batch/ingest_auto_prep.py`'s chain gains a `build_ocr_lemmas` step. Unlike
`build_ocr_text_embeddings` (which is `text_heavy`-scoped and must run after `classify_text_heavy`),
`build_ocr_lemmas` processes *every* image with OCR text regardless of `text_heavy` status — it has
no ordering dependency on the classifier. Placed directly after `extract_text_from_memes` (the
earliest point OCR text exists) and before `classify_text_heavy`/`build_ocr_text_embeddings`, so
lemma coverage is available as early in the chain as possible:

```python
    steps = [
        ("ingest_validate_formats", lambda: ingest_validate_formats.main(env=None)),
        ("build_image_embeddings", lambda: build_image_embeddings.main(incremental=True, target_status="pending")),
        ("extract_text_from_memes", lambda: extract_text_from_memes.main(settings.BASE_PATH, target_status="pending")),
        ("build_ocr_lemmas", lambda: build_ocr_lemmas.main(status="pending")),
        ("classify_text_heavy", lambda: classify_text_heavy.main(status="pending")),
        ("build_ocr_text_embeddings", lambda: build_ocr_text_embeddings.main(status="pending")),
        ("ingest_find_duplicates", lambda: ingest_find_duplicates.main(env=None, tier="tier_a", k=None)),
    ]
```

CLAUDE.md's ingestion run-order block, the real per-script entries list, and
`docs/runbooks/ingestion-pipeline.md`'s TL;DR + "Running a batch" walkthrough all need the matching
update (mirroring exactly how Task 7's fix updated them for `build_ocr_text_embeddings` — same
sections, same style).

### §4. Re-enabling `clusterize.py`'s `ocr_text` auto-clustering

`batch/clusterize.py`'s `get_duplicate_pairs()` currently reads:

```python
        ).where(
            TmpDuplicates.distance_source == "clip",
            TmpDuplicates.distance < clip_threshold,
```

Reverts to the pre-interim-fix two-source form (from the originating spec, Task 4), restoring the
`ocr_text_threshold` parameter and the `or_(and_(...), and_(...))` shape:

```python
        ).where(
            or_(
                and_(TmpDuplicates.distance_source == "clip", TmpDuplicates.distance < clip_threshold),
                and_(TmpDuplicates.distance_source == "ocr_text", TmpDuplicates.distance < ocr_text_threshold),
            ),
```

This is safe to revert now specifically *because* §2's corroboration gate runs at **insertion**
time — every `ocr_text`-sourced row that exists in `tmp_duplicates` by the time `clusterize.py` reads
it has already passed the lemma-overlap check. `clusterize.py` itself doesn't need to know about
`ocr_lemmas` at all; it trusts the `distance_source` marker the same way it already trusts `'clip'`.
`PROXIMITY_THRESHOLD_OCR_TEXT` (kept defined but unused since the interim fix) becomes live again,
unchanged at `0.05`.

`cluster_active_library()`'s call site reverts symmetrically:

```python
    pairs = await get_duplicate_pairs(session, img_id_to_int_id, PROXIMITY_THRESHOLD, PROXIMITY_THRESHOLD_OCR_TEXT)
```

### §5. Backend / Frontend

No changes. `distance_source` already ships as `'clip'`/`'ocr_text'` on every API response (Task 5
of the originating spec); this feature changes which rows exist upstream of that, not their shape.
The Tier B review label (Task 6, `"text"` vs `"visual"`) is unaffected — it already renders whatever
`distance_source` a row carries.

## Testing

New integration tests in `tests/integration/test_rebuild_duplicates.py` (mirroring the file's
existing `_insert_ocr_text_embedding`/`_mark_text_heavy` helper conventions):

- `test_ocr_text_probe_excludes_pair_below_overlap_coefficient` — two text-heavy images, tight OCR-
  text embedding distance, zero shared `ocr_lemmas` — must NOT be inserted at all (not just
  excluded from clustering; the corroboration gate blocks insertion).
- `test_ocr_text_probe_includes_pair_above_overlap_coefficient` — same embedding-distance setup,
  but with shared `ocr_lemmas` rows giving overlap coefficient comfortably above 0.2 — must be
  inserted as `distance_source='ocr_text'`.
- `test_ocr_lemma_overlap_check_uses_coefficient_not_raw_count` — the regression test for the
  hub-coincidence bug this design explicitly guards against: a "hub" probe image with a large
  `ocr_lemmas` set and a candidate with a small set share enough *raw* lemmas to clear a naive count
  threshold, but the *coefficient* (normalized by the smaller side) falls below
  `MIN_LEMMA_OVERLAP_COEFFICIENT` — must be excluded. This must fail if `_OCR_LEMMA_OVERLAP_CHECK`
  is ever simplified back to a raw count.
- `test_ocr_lemma_overlap_check_respects_min_lemma_count_floor` — two images with only 1-2 total
  `ocr_lemmas` rows each, all shared (coefficient would compute to 1.0) — must still be excluded,
  since `MIN_LEMMA_COUNT_FLOOR` isn't met.

Mirrored in `tests/integration/test_ingest_find_duplicates.py` for `find_batch_duplicates()`'s own
OCR-text probe call site (same four test shapes, that file's existing fixture conventions).

`tests/integration/test_clusterize.py`: revert the interim fix's test changes — restore
`test_ocr_text_sourced_pair_clusters_under_its_own_threshold`-shaped tests (an `ocr_text`-sourced
pair below `PROXIMITY_THRESHOLD_OCR_TEXT` clusters; above it, doesn't; both sources gate
independently) since `clusterize.py` itself reverts to trusting `distance_source` again. The
interim fix's `test_ocr_text_sourced_pair_never_auto_clusters` (proving `ocr_text` pairs are
*unconditionally* excluded) is specifically what needs to change — its assertion becomes wrong
under this spec's design by intent, not by accident, so it's replaced rather than kept alongside
the reverted behavior.

`batch/tests/test_ingest_auto_prep.py`: update for the new `build_ocr_lemmas` step (8 steps now),
mirroring exactly how Task 7's fix updated this file for `build_ocr_text_embeddings`.

## Rollout

1. Ship the code (this spec's Design §1-§4) plus test coverage, through the same SDD process as
   every prior piece of this effort.
2. **Controller-only, live-database step, explicit go-ahead required before this step**: run
   `build_ocr_lemmas.py --status active --incremental` against `metal`/`general`/`it` to close the
   742-image (`general`; smaller counts expected for `metal`/`it`) coverage gap identified in
   Design §3. `--incremental` here is *not* the same as skipping the gap: `ImagesRepository`'s
   incremental query (`repository/images.py`) filters on processing-status, not on "already has
   lemma rows", so it still picks up every never-processed image in the gap. A non-incremental
   full reprocess is both unnecessary (it would redo already-covered images) and, per Task 1's own
   guard, actively wrong to combine with `--status pending` — safest to default to `--incremental`
   for every `--status active` invocation too. (Corrected post-rollout: this originally read "no
   `--incremental`" — the actual Task 7 execution used `--incremental` throughout; see Rollout
   Outcome below.)
3. Re-run `rebuild_duplicates.py --env <environment>` against all three (incremental; chains to
   `clusterize.py` automatically) — this re-evaluates every existing `ocr_text` candidate against
   the new corroboration gate (rows that already exist in `tmp_duplicates` from the prior rollout
   are untouched by `ON CONFLICT DO NOTHING`; incremental probing only affects images not yet
   probed for this signal, so the *existing* noisy rows from the 2026-09-18 rollout need a
   `--full` OCR-text-specific pass or an explicit cleanup query — see the open question below).
4. Verify via `DATABASE_URL_READONLY`: re-run this spec's own calibration queries (overlap-
   coefficient distribution, hub-image pair counts) against the post-rollout `tmp_duplicates` state
   to confirm the false-positive cluster is gone, not just that new rows look right.
5. Spot-check the live Explore → Duplicates page for `general` (the environment with the most
   `ocr_text` history) to confirm no obviously-unrelated pairs are now auto-confirmed.
6. Mark this spec `done` with a Rollout Outcome section.

**Open question for the implementation plan to resolve, not decided here**: because `ON CONFLICT DO
NOTHING` means already-inserted `ocr_text` rows from the 2026-09-18 rollout won't be re-evaluated by
a normal incremental `rebuild_duplicates.py --env <environment>` run, Step 3 needs either (a) a
one-time cleanup that deletes existing `distance_source='ocr_text'` rows before re-running (so the
incremental probe treats every text-heavy image as unprobed for this signal again, and every
surviving row is guaranteed to have passed the new gate), or (b) a one-off read-only-then-write
script that evaluates the corroboration check against existing rows in place and deletes the ones
that fail it, without a full re-probe. Both are viable; the implementation plan should pick one
explicitly rather than leave it implicit — this is exactly the kind of `tmp_duplicates`-mutating
step Task 7's own rollout treated as controller-only, explicit-go-ahead-required.

## Rollout Outcome

Executed 2026-09-19/20 (controller-only, all live-database steps run directly, per explicit user
go-ahead). Tasks 1-6 (code + docs) were merged first via the standard SDD task loop; this section
covers Task 7's live rollout across `metal`/`general`/`it`.

**Backups**: `pg_dump -Fc` taken for all three databases before any write
(`Storage/backups/ocrdb-2026-09-20-{metal,general,it}.dump`), matching the originating spec's
rollout precedent.

**Step 2 — `build_ocr_lemmas --status active --incremental` coverage-gap backfill**:

| Environment | Images backfilled | Lemmas added |
|---|---|---|
| `metal` | 0 (already fully indexed) | 0 |
| `general` | 43 | 645 |
| `it` | 0 (already fully indexed) | 0 |

The `general` gap had already narrowed substantially from the 742-image estimate in Design §3
(taken at the 2026-09-18 rollout) by the time this plan executed — most of the corpus had already
picked up coverage since then, likely from other activity between the two rollouts.

**Step 3 — deleted existing `distance_source='ocr_text'` rows** (per this spec's own Rollout
ruling, resolved during plan-writing: delete-and-reprobe rather than evaluate-in-place):

| Environment | Rows deleted |
|---|---|
| `metal` | 1 |
| `general` | 236 (grown from the 191 recorded in Design §1's calibration snapshot — more
  candidates accumulated between the two rollouts) |
| `it` | 1 |

**Step 4 — re-ran `rebuild_duplicates.py --env <environment>`** (chains to `clusterize.py`) for all
three. All three backends (`/api/diagnostics/health`) stayed healthy throughout every step.

**Step 5 — verification** (via `DATABASE_URL_READONLY`):

| Environment | `ocr_text` rows before delete | `ocr_text` rows after re-probe |
|---|---|---|
| `metal` | 1 | 1 |
| `general` | 236 | **13** |
| `it` | 1 | 1 |

`general`'s post-rollout count of exactly 13 matches Design §1's own calibration finding almost
exactly ("13 pairs at overlap coefficient ≥ 0.417" in the original complete 191-pair distribution).
Computed the overlap coefficient directly (via `psql`, not trusted from application logic) for all
13 surviving pairs: every one clears 0.417+ (range 0.417-0.938), with the boundary-case pair
scoring exactly 0.417 (10 shared / 24 min) and turning out, on inspection, to be Design §1's own
"3 stages of life template, different comment" calibration example. The known false-positive hub
image (635 OCR blocks, confirmed by querying `ocr_texts` grouped by `image_id` for the matching
block count) is confirmed absent from every surviving `general` pair — zero pairs touch it.

Spot-checked two `general` pairs by opening the actual image files: the 0.417-boundary pair (the
"3 stages of life" template, confirmed genuine) and the highest-coefficient pair (0.938 — two
screenshots of the same comment thread at different scroll depth, confirmed genuine). Also
spot-checked `metal`'s sole surviving pair (distance 0.013, well under
`PROXIMITY_THRESHOLD_OCR_TEXT`) by opening both images — same tweet screenshot, two different crops,
confirmed genuine and confirmed present in `tmp_clusters` (correctly auto-clustered).

**Step 6 — live Explore → Duplicates spot-check**: `metal` (5 clusters, 10 rows in `tmp_clusters`)
and `it` (54 clusters, 112 rows) both show real, correct content via `/api/images/duplicates` — the
`metal` true-positive pair above is present and correctly clustered.

`general` currently shows **zero** items on this page (`tmp_clusters` has 0 rows for `general`).
Investigated this rather than treating it as a silent pass: `general` has three unusually large
CLIP-sourced connected components (288/136/76 images — long-standing repost "hub" clusters, not new
from this rollout) that `clusterize.py`'s pre-existing oversized-cluster splitting logic
(`resolve_cluster()`, untouched by any task in this plan) shatters entirely to zero surviving
groups when it tightens the threshold from 0.05 down to the 0.01 floor — every member ends up
dropped as an implicit singleton rather than landing in a valid 2-12-member group. Confirmed this
is **not caused by this rollout**: reproducing the identical union-find graph using only
`clip`-sourced edges (with `ocr_text` completely excluded — nothing in Tasks 1-7 touches CLIP
thresholds, `resolve_cluster`, or the splitting settings) gives the byte-identical zero-groups
result. None of `general`'s 13 surviving `ocr_text` pairs are affected either way — all 13 sit
above `PROXIMITY_THRESHOLD_OCR_TEXT` (0.05, range is 0.0517-0.0972), so none was ever a candidate
for `clusterize.py`'s tighter auto-cluster threshold regardless of this splitting behavior; they
still reach ingestion's Tier A/B human review via `ingest_find_duplicates.py`, unaffected.

**This is a known, separate, pre-existing issue in `clusterize.py`'s oversized-cluster splitting
algorithm for very large, densely-connected hub clusters — out of this spec's scope (no task here
touches `resolve_cluster` or the splitting settings) and not fixed as part of this rollout.** It
affects only `general`'s largest CLIP-only hub clusters, not the OCR-text corroboration mechanism
this spec ships. Flagged here for whoever picks up a future fix; worth a follow-up spec.

**Backup/restore caveat**: the pre-rollout `pg_dump` backups (`Storage/backups/ocrdb-2026-09-20-
{metal,general,it}.dump`) contain the un-gated `ocr_text` rows Step 3 deleted (238 across all three
environments). Restoring one of these dumps reintroduces rows that never passed the corroboration
check into a `clusterize.py` that trusts `distance_source` unconditionally. If restoring a
pre-rollout dump, re-run `DELETE FROM tmp_duplicates WHERE distance_source = 'ocr_text';` followed
by `rebuild_duplicates.py --env <environment>` afterward.

**Final whole-branch review** (dispatched on the most capable model, per SDD convention for a
branch touching live-database-affecting matching logic): **Ready to merge: Yes**, no Critical
findings. Independently re-verified the `clusterize.py` discovery above (confirmed via `git log -p`
that `resolve_cluster()` and every splitting setting are untouched across the whole branch) and
independently re-ran all three test suites in a separate worktree against `main`, confirming the
5 `tests/integration/` failures are pre-existing and unrelated. Two Important, non-blocking
findings accepted as follow-ups rather than fixed in this branch: (1) nothing structurally prevents
`ocr_lemmas` coverage from silently lagging `rebuild_duplicates.py` runs going forward — a coverage
gap causes an image to be rejected for missing lemmas rather than low overlap, indistinguishably
from the outside, since `build_ocr_lemmas`/`build_ocr_text_embeddings`/`rebuild_duplicates` are all
deliberately unscheduled/manual-trigger-only; worth an operator-facing warning count (e.g. images
with an `ocr_text_embeddings` row but no `ocr_lemmas` rows) in a future change. (2) `general`'s
re-enabled `ocr_text` auto-clustering has near-zero live validation today, since all 13 surviving
pairs sit above `PROXIMITY_THRESHOLD_OCR_TEXT` (0.05) — only `metal`'s single pair (0.013)
currently exercises the live auto-cluster path; worth re-checking `general`'s `tmp_clusters` after
future `rebuild_duplicates` runs once pairs start landing under 0.05. Minor findings (two misleading
test docstrings, this Rollout section's stale `--incremental` guidance, a `CLAUDE.md` consistency
gap) were fixed directly following the review.

**Conclusion**: the OCR-lemma overlap corroboration gate is live and functioning exactly as
calibrated in Design §1 across all three environments — `general`'s `ocr_text` candidate pool
dropped from 236 to 13 with zero false positives surviving and the previously-identified hub image
confirmed excluded, and both `metal`'s and `it`'s single surviving pairs are confirmed genuine.

## Self-Review

**Placeholder scan**: none — every SQL fragment, constant, and call-site change above is complete,
literal code, not a description of what to write.

**Type/name consistency**: `MIN_LEMMA_OVERLAP_COEFFICIENT`/`MIN_LEMMA_COUNT_FLOOR` defined once in
`rebuild_duplicates.py`, imported (never redefined) by `ingest_find_duplicates.py` — matching the
established pattern `TEXT_EMBEDDING_TIGHT_THRESHOLD` etc. already use across those two files.
`find_duplicates()`'s new `extra_where_sql` parameter is optional with a `None` default, so every
existing call site (general CLIP, safety net) needs zero changes — verified by reading all four
current call sites (two in `rebuild_duplicates.py`, two in `ingest_find_duplicates.py`) against the
new signature.

**Caught during this pass**: the first draft of `_OCR_LEMMA_OVERLAP_CHECK` computed
`min_lemma_count`/overlap coefficient using `probe.id`/`e2.image_id` (the LATERAL's own inner
aliases) rather than `probe.id`/`nn.image_id` (the outer, post-LIMIT aliases) — which would have
silently reintroduced the exact "runs on every corpus candidate before the LIMIT narrows it"
performance problem §2 explicitly argues against, since `e2`/`i2` only exist inside the LATERAL.
Fixed by writing out the full interpolation context (§2's code block) and re-deriving which alias
is actually in scope at that point, rather than assuming.

**Spec coverage check**: Problem → both the "why embedding distance alone fails" recap and the new
lexical-overlap evidence. Goal → §1-§4. Non-goals → explicitly cross-checked against §1-§5 (nothing
in Design touches embedding thresholds, tokenization, CLIP, schema, or API surface). Testing →
covers both files' probe call sites, the hub-coincidence regression specifically, the floor
edge case, and `clusterize.py`'s reversion. Rollout → sequenced, with the one real open
implementation question (re-evaluating already-inserted rows) flagged explicitly rather than
glossed over.
