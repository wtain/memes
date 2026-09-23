# Duplicate-Matching Pipeline Coverage-Gap Diagnostics

status: planned
Plan: docs/superpowers/plans/2026-09-23-duplicate-matching-coverage-gap-diagnostics.md
Originates from: `docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md`'s final
whole-branch review, Important finding (1): "nothing structurally prevents `ocr_lemmas` coverage
from silently lagging `rebuild_duplicates.py` runs going forward — a coverage gap causes an image
to be rejected for missing lemmas rather than low overlap, indistinguishably from the outside,
since `build_ocr_lemmas`/`build_ocr_text_embeddings`/`rebuild_duplicates` are all deliberately
unscheduled/manual-trigger-only; worth an operator-facing warning count ... in a future change."
Accepted as a non-blocking follow-up rather than fixed in that branch.

## Problem

The text-heavy duplicate-matching signal depends on a three-stage manual-trigger-only pipeline,
each stage feeding the next (see CLAUDE.md's batch pipeline section):

```
OCR text (extract_text_from_memes)
  → classify_text_heavy   (text_heavy / not_text_heavy)
      → build_ocr_text_embeddings   (text_heavy images only)
          → build_ocr_lemmas        (needed by the OCR-text duplicate probe's lexical-overlap
                                       corroboration gate — see
                                       docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md)
```

None of these three stages run on a schedule (CLAUDE.md: "manual-trigger only, not scheduled" for
each), so an operator can run `build_ocr_text_embeddings` without having run `classify_text_heavy`
first on newly-OCR'd images, or run `rebuild_duplicates`/`ingest_find_duplicates` without having
re-run `build_ocr_lemmas` after a batch of new `ocr_text_embeddings` rows landed. When that
happens, the corroboration gate (`MIN_LEMMA_OVERLAP_COEFFICIENT`/`MIN_LEMMA_COUNT_FLOOR` in
`rebuild_duplicates.py`/`ingest_find_duplicates.py`) rejects an image's OCR-text candidate pairs
for having zero lemma overlap — but "zero lemma overlap" and "no lemma data at all" produce the
exact same outcome from the outside. An operator watching the duplicate-matching queue has no way
to tell "this image's text genuinely doesn't overlap with its candidate" from "this image was
never lemma-indexed, so of course it doesn't overlap."

Today there is no visibility into this at all — an operator only discovers the gap by noticing an
image that obviously should have matched didn't, then manually checking which pipeline stage it's
missing.

## Goal

Surface three read-only counts on the existing `/api/diagnostics/statistics` endpoint (rendered on
the Statistics page's existing "Pipeline coverage" section), one per stage boundary in the chain
above, so an operator can see at a glance whether the pipeline has drifted and which stage to
re-run:

1. **`ocr_missing_text_heavy_classification`** — active images with OCR text but no
   `classify_text_heavy` verdict yet.
2. **`text_heavy_missing_embeddings`** — active images classified `text_heavy` but with no
   `ocr_text_embeddings` row yet.
3. **`embeddings_missing_lemmas`** — active images with an `ocr_text_embeddings` row but no
   `ocr_lemmas` rows yet. This is the exact blind spot the originating finding named: these are
   the images the corroboration gate can silently misjudge as "no overlap" today.

All three counts are diagnostic only — pure reads, no new writes, no change to any batch script's
behavior or to the corroboration gate itself. A non-zero count tells an operator "re-run stage X,"
nothing more.

## Non-goals

- **Fixing the underlying drift.** This spec adds visibility, not automation — it does not make
  any stage run automatically, does not add a scheduled job, and does not change
  `ingest_auto_prep`'s chain (which already runs all three stages in order for ingestion; this gap
  is specifically about the *separately* invokable maintenance path: `rebuild_duplicates`,
  `classify_text_heavy`, `build_ocr_text_embeddings`, and `build_ocr_lemmas` run independently
  against the active corpus per CLAUDE.md's Maintenance section).
- **A new endpoint or UI page.** Reuses `/api/diagnostics/statistics` and the Statistics page's
  existing "Pipeline coverage" section — no new route, no new nav entry.
- **Alerting, thresholds, or "unhealthy" styling.** These are plain counts, expected to be
  transiently non-zero any time a batch run is pending — not a pass/fail signal. No severity
  levels, no push notification, no `/api/diagnostics/health` change.
- **Per-image drill-down (listing *which* images are gapped).** The count alone is enough to tell
  an operator to re-run a stage; if a future need arises to identify specific images (e.g. to
  audit why one image still shows a gap after a re-run), that's a separate follow-up — out of
  scope here to keep this change small.
- **Any Android or ingestion-review-UI surface.** Ingestion Tier A/B review already shows OCR text
  per member directly; this is purely the corpus-wide operator diagnostics page.

## Design

### §1. Shared schema

`shared/schemas/statisticsmemestats.schema.json` gains three properties, following the file's
existing style exactly (flat, `integer`, one-line `description`, added to `required`):

```json
"ocr_missing_text_heavy_classification": { "type": "integer", "description": "Active images with OCR text but no classify_text_heavy verdict yet" },
"text_heavy_missing_embeddings":         { "type": "integer", "description": "Active images classified text_heavy with no ocr_text_embeddings row yet" },
"embeddings_missing_lemmas":             { "type": "integer", "description": "Active images with an ocr_text_embeddings row but no ocr_lemmas rows yet -- the corroboration gate's blind spot, see docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md" }
```

Added to `MemeStats` rather than a new schema file — these are per-image inventory/coverage counts
of exactly the same shape as `with_ocr`/`with_embeddings`/`with_tags` already in that file, not a
distinct concern warranting `ContentStats` or a new top-level section.

Regenerate all three trees per CLAUDE.md's Type generation section
(`Frontend/generate-types.sh`, `AndroidClient/scripts/generate_dtos.py`, the Backend Pydantic
generation command from `documents/generation.md`) and confirm `git diff` on each generated
output — the frontend step is CI-gated (`backend-tests.yml`/`integration-tests.yml`-adjacent CI
checks a clean diff on `Frontend/memes-frontend/src/types/generated/`), so a stale `all.d.ts`
fails the build.

### §2. Repository

`Backend/app/repositories/diagnostics_repository.py`'s `get_statistics()` gains three more
scalar-subquery columns, following the file's existing `without_tags` anti-join pattern exactly
(same `status == "active"` scoping as every other per-image count in this method):

```python
select(func.count()).select_from(Image)
    .where(
        Image.status == "active",
        exists(select(OCRText.image_id).where(OCRText.image_id == Image.id)),
        ~exists(
            select(ImageClassification.image_id)
            .where(
                ImageClassification.image_id == Image.id,
                ImageClassification.classifier == CLASSIFIER_NAME,
            )
        ),
    )
    .scalar_subquery().label("ocr_missing_text_heavy_classification"),
select(func.count()).select_from(Image)
    .where(
        Image.status == "active",
        exists(
            select(ImageClassification.image_id)
            .where(
                ImageClassification.image_id == Image.id,
                ImageClassification.classifier == CLASSIFIER_NAME,
                ImageClassification.result == TEXT_HEAVY,
            )
        ),
        ~exists(select(OCRTextEmbedding.image_id).where(OCRTextEmbedding.image_id == Image.id)),
    )
    .scalar_subquery().label("text_heavy_missing_embeddings"),
select(func.count()).select_from(Image)
    .where(
        Image.status == "active",
        exists(select(OCRTextEmbedding.image_id).where(OCRTextEmbedding.image_id == Image.id)),
        ~exists(select(OCRLemma.image_id).where(OCRLemma.image_id == Image.id)),
    )
    .scalar_subquery().label("embeddings_missing_lemmas"),
```

`CLASSIFIER_NAME`/`TEXT_HEAVY` import from `rules/text_heavy_result.py` (the canonical source —
`batch/utils/text_heavy_classifier.py` re-exports the same constants; either import path resolves
identically, this file follows `batch/build_ocr_text_embeddings.py`'s existing choice of the
`rules/` path). `ImageClassification`, `OCRTextEmbedding`, `OCRLemma` added to this file's
`Storage.models` import line alongside the existing `Image`, `OCRText`, etc.

No new index needed: `image_classifications.image_id`, `ocr_text_embeddings.image_id`, and
`ocr_lemmas.image_id` are all primary-key (or leading-primary-key, for `OCRLemma`'s composite key)
columns already, so each `EXISTS`/`NOT EXISTS` is an indexed lookup — same cost profile as the
existing `without_tags` anti-join, no measurable difference to the endpoint's current latency
expected at current corpus sizes (largest environment: `general`, ~25K active images).

### §3. Router — hand-written response model, not schema-generated

**This is the exact landmine CLAUDE.md's own gotcha section documents**: `HealthResponse`,
`MemeStats`, `ContentStats`, `TrendsStats`, and `StatisticsResponse` in
`Backend/app/api/diagnostics.py` are hand-written Pydantic `BaseModel` classes, not imports from
`Backend/app/types/generated/` — confirmed by inspection, `response_model=StatisticsResponse` in
that file resolves to the hand-written class. Regenerating the Python tree from the updated schema
(§1) is **not sufficient by itself** — per the gotcha, Pydantic silently strips fields the
`response_model` class doesn't declare, no error. `MemeStats` in `diagnostics.py` must be edited
directly to add the three new fields (plain `int` attributes, same as its existing fields), and
`statistics()`'s `MemeStats(...)` construction call updated to pass `row.ocr_missing_text_heavy_classification`,
`row.text_heavy_missing_embeddings`, `row.embeddings_missing_lemmas` through from the repository
row (§2's labels).

### §4. Frontend

`StatisticsPage.tsx`'s existing "Pipeline coverage" `<section>` gains three more `StatGrid` cells,
following the section's existing style (raw count via `n(...)`, no percentage — these are gap
counts against no fixed denominator, not coverage-of-total like the section's other cells):

```tsx
{ label: "OCR without text-heavy classification", value: n(memes.ocr_missing_text_heavy_classification) },
{ label: "Text-heavy without OCR-text embeddings", value: n(memes.text_heavy_missing_embeddings) },
{ label: "Embeddings without OCR lemmas", value: n(memes.embeddings_missing_lemmas) },
```

No new component, no new page, no new route — `StatisticsResponse`'s TypeScript type already
picks up the three new fields once §1's regeneration completes.

## Testing

- **Repository** (`tests/integration/test_backend_diagnostics_repository.py`, real DB — the
  existing file already covers `DiagnosticsRepository.get_statistics()` this way, e.g.
  `test_get_statistics_counts_seeded_rows`): extend with fixtures for each of the three new counts
  — one image in the gapped state and one image one stage further along (not gapped) per count,
  asserting the count reflects only the gapped image, following the file's existing
  before/after-delta assertion style. Also: a `status="pending"` image in a gapped state must not
  be counted (scope is active-only, per the Goal — same assertion shape as the existing
  `test_get_statistics_counts_pending_and_rejected_separately_from_total`), and a fully-covered
  image (OCR → classified `text_heavy` → embedded → lemma-indexed) must not appear in any of the
  three counts.
- **Backend API** (`Backend/tests/test_diagnostics_endpoints.py`, mocked repository via
  `AsyncMock`/`_fake_stats_row` — the existing file's established pattern): add the three new
  fields to `_fake_stats_row`'s defaults and a `TestStatistics` case asserting
  `data["memes"]["ocr_missing_text_heavy_classification"]` (and the other two) round-trip through
  the real response — this is the regression test for §3's gotcha specifically (confirms the
  hand-written `MemeStats` class was actually updated to declare the fields, not just the schema;
  a class left undeclaring them would make this assertion fail with a `KeyError`, not silently
  pass).
- **Frontend** (`vitest run`, if `StatisticsPage.tsx` has existing tests — else skip, matching the
  page's current test coverage level): the three new cells render given a mock
  `StatisticsResponse` including the new fields.
- **Type generation gate**: `bash Frontend/generate-types.sh` followed by
  `git diff Frontend/memes-frontend/src/types/generated/` must show the change and then be clean
  after committing — per CLAUDE.md's frontend pre-commit checklist.

## Rollout

1. Ship the schema change, migration-free (no DB schema change — this is a read-only query
   addition, no new table/column). Regenerate all three type trees, confirm `git diff` is clean
   post-commit.
2. Update `Backend/app/api/diagnostics.py`'s hand-written `MemeStats` (§3) — verify via a live
   `GET /api/diagnostics/statistics` call (per CLAUDE.md's "Before committing backend changes")
   that the three new fields actually appear in the response body, not just in the generated
   Python type — this is the specific check that would have caught the `text_heavy` field-drop
   incident CLAUDE.md documents if it had been run at the time.
3. No live-database write of any kind — safe to verify directly against each environment's
   existing data via `DATABASE_URL_READONLY` (controller-executed, per this repo's live-DB-access
   rule) to sanity-check the three counts against each environment's known pipeline state before
   calling this done: `metal`/`it` are fully caught up per the OCR-text-embeddings rollout
   (§"Rollout outcome" in `docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md`), so all
   three counts should read at or near zero there; `general` had a real gap during the
   corroboration-gate rollout (`ocr_lemmas` coverage was the originating finding's whole premise),
   so a non-zero `embeddings_missing_lemmas` on `general` at rollout time is expected and confirms
   the count is measuring the right thing.
4. Mark this spec `done` with a short Rollout Outcome section (the three counts observed per
   environment at rollout time).

## Rollout outcome (2026-09-23)

Implemented via subagent-driven-development in an isolated worktree (all 5 plan tasks complete,
each independently reviewed clean — see the plan's own ledger). **Branch not yet merged to `main`
as of this writing** — see the note below on what that means for these numbers.

**Read-only verification** (`DATABASE_URL_READONLY`, controller-executed directly, per this repo's
live-DB-access rule) against all three environments' real data, using the same anti-join logic the
implemented `DiagnosticsRepository.get_statistics()` query uses:

| Environment | `ocr_missing_text_heavy_classification` | `text_heavy_missing_embeddings` | `embeddings_missing_lemmas` |
|---|---|---|---|
| metal   | 484 | 0 | 0 |
| general | 197 | 1 | 5 |
| it      | 12  | 0 | 0 |

Matches expectations: `metal`/`it` read at/near zero on `embeddings_missing_lemmas`, consistent
with both being fully caught up per `docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md`'s
own rollout. `general`'s non-zero `embeddings_missing_lemmas` (5) is expected — `general`'s
`ocr_lemmas` coverage gap was the originating finding's whole premise
(`docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md`'s final-review finding).
`metal`'s numbers were independently cross-checked two ways: Task 3's live-server smoke test
(`GET /api/diagnostics/statistics` on a temporary port against the real `metal` database) returned
the identical 484/0/0, and this controller-run raw-SQL query reproduced it exactly — strong
corroborating evidence the implemented query is correct, not an artifact of either verification
method.

**Live-endpoint deployment note:** the three currently-running dev servers (`metal`/`general`/`it`,
ports 8081-8083) are still serving pre-merge code as of this rollout note — confirmed directly
(`GET /api/diagnostics/statistics` on `metal` does not yet include the three new fields). This is
expected: this branch has not merged into `main` yet. These servers run with `--reload`/
`WATCHFILES_FORCE_POLLING` against the main checkout, so merging this branch into `main` is
expected to make them pick up the change automatically, with no separate deploy step. The table
above is independent of that — it queries the database directly, not through the (not-yet-updated)
live endpoint.

Status intentionally left `planned` rather than `done` here — per this repo's lifecycle, `done`
means "implemented and merged," and the merge hasn't happened yet. The status line will be updated
once the branch is actually merged.
