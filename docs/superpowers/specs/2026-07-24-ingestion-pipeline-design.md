# Ingestion Pipeline Design (Draft)

**Date:** 2026-07-24 (revised 2026-07-25 after first brainstorming pass)
**Status:** Draft — architecture decisions below are now confirmed with the user; a handful of
UX-level open questions remain. Builds on `2026-07-24-ingestion-pipeline-pre-spec.md`.

---

## Prerequisites

This design depends on three pieces of infrastructure that don't exist yet. None of them are
ingestion-specific — each is a generalization of something already in the codebase — and each has
its own spec:

| Spec | Unblocks |
|---|---|
| [`2026-07-25-batch-run-tracking-design.md`](2026-07-25-batch-run-tracking-design.md) | Generic `batch_runs` table (generalized from `trends_batch`'s `TrendsRun`). Ingestion uses one `batch_runs` row (`kind="ingestion"`) to track which stage a run is in, instead of a bespoke `ingestion_batches` table. |
| [`2026-07-25-image-visibility-status-design.md`](2026-07-25-image-visibility-status-design.md) | `images.status` (`pending`/`active`/`rejected`) plus the full audit of every read path that must filter to `active`. This is what makes "register into the same `images` table but keep it hidden" possible at all. |
| [`2026-07-25-duplicate-clustering-incremental-design.md`](2026-07-25-duplicate-clustering-incremental-design.md) | Replaces `rebuild_duplicates.py`'s O(n²) whole-corpus cross join with an incremental, threshold-bounded, HNSW-assisted KNN query, parameterized by probe/corpus scope. This single primitive covers active-library maintenance, ingestion stage 2, *and* stage 3 — see below. |

Implementation order isn't strictly linear (see Next Steps), but conceptually: batch-run-tracking
and image-visibility are foundational (nothing ingestion-specific can be built without them);
duplicate-clustering is needed once stage 2 work starts.

## Goal

Give each environment (metal / general / it) a repeatable way to bring a batch of new images from
an external drop location into its active library, filtering out exact and near-duplicates —
against both the incoming batch itself and the existing corpus — before the expensive per-image
enrichment pipeline (OCR, tags, descriptions) runs on them, without recomputing CLIP embeddings
already computed during duplicate review, and with **human-driven** review only (see Decisions
below).

## Non-goals

- Cross-environment ingestion — one `PATH_INGESTION_SOURCE` maps to one environment.
- Agent/skill-driven duplicate review for ingestion. The existing `review-duplicates` skill +
  `tools/agent_duplicates.py` pattern is explicitly **not** reused here — see Decisions.
- Re-architecting the existing active-library duplicate review UI/flow — scoped/reused via the
  duplicate-clustering prerequisite, not replaced.

## Terminology

| Term | Meaning |
|---|---|
| **Pending image** | `images.status = 'pending'` — registered, has an embedding, not yet visible in browse/search. |
| **Active image** | `images.status = 'active'` — visible, fully promoted. |
| **Rejected image** | `images.status = 'rejected'` — confirmed duplicate during review; row kept for undo, excluded from listings the same as pending. |
| **Ingestion run** | One `batch_runs` row, `kind = 'ingestion'`; `stage` tracks progress (`hash_dedup` → `in_batch_review` → `cross_corpus_review` → `promoted`). |

---

## Decisions confirmed 2026-07-25

1. **No separate pending-file storage location.** Images move from `PATH_INGESTION_SOURCE` into
   `BASE_PATH` immediately after stage 1 (hash dedup), exactly like the pipeline moves files today
   — they just aren't visible yet because `status = 'pending'`. This eliminates an entire category
   of complexity the first draft carried (a second file-serving path for pending images); file
   serving code needs no changes at all. It also **simplifies stage 4 (promotion) down to a pure
   status flip** — there's no file move left to do at promotion time, since the file has already
   been sitting in `BASE_PATH` since right after stage 1.
2. **Cross-corpus hash check added to stage 1.** In addition to hashing the incoming batch against
   itself, new images are also hash-checked against the *existing* corpus's `content_hash` values —
   a cheap, exact-match win before any embedding work happens. See Stage 1 below for the dependency
   this creates.
3. **Duplicate clustering is incremental and threshold-bounded**, per the dedicated prerequisite
   spec — this resolves what was previously open question #3 (whether batch-scoped and
   active-library dedup could safely share `tmp_duplicates`): since the new design never drops the
   table and inserts are idempotent (`ON CONFLICT DO NOTHING` on a normalized pair), an
   active-library incremental rebuild and an in-progress ingestion review can coexist safely.
4. **Review is purely human-driven, via UI — not the existing agent skill.** The existing
   `review-duplicates` skill / `tools/agent_duplicates.py` pattern has produced false positives in
   practice that permanently lost images. Ingestion review reuses the *visual* pattern (cluster →
   decide → mark) but as a UI flow, and rejections are recoverable by design (`status = 'rejected'`,
   not a delete — see the visibility spec) specifically because of that history.

---

## Stage-by-stage design

### Stage 0 — Intake

`PATH_INGESTION_SOURCE` is a new per-environment secret in `.env.<environment>`, analogous to
`BASE_PATH`. One source directory per environment. A new ingestion run is refused if
`BatchRunRepository.get_active_run(kind="ingestion")` returns a row for this environment's DB —
cheap concurrency guard, provided for free by the batch-run-tracking prerequisite.

### Stage 1 — Coarse filter (hash dedup, in-batch and cross-corpus)

Pure filesystem + hash-lookup work, no embeddings involved yet.

1. **In-batch:** hash every file in `PATH_INGESTION_SOURCE` (reusing
   `batch/utils/file_hash.py`'s `sha256_file`/`files_are_identical` directly), union-find on
   identical hashes, keep one per cluster, move the rest to `PATH_INGESTION_SOURCE/duplicates/`.
2. **Cross-corpus:** look up each surviving file's hash against `images.content_hash` for
   `status = 'active'` rows. A match moves the new file to `PATH_INGESTION_SOURCE/duplicates/` too
   (same tier as in-batch hash matches — both are byte-identical decisions, no need for a separate
   directory just because the comparison side differs).
   - **Dependency:** this is only a cheap indexed lookup if the existing corpus's `content_hash`
     is actually populated. Today it's populated lazily, only when `detect_file_duplicates.py`
     (a "run as needed" maintenance script) happens to run — not guaranteed. Two things follow:
     (a) each environment needs a one-time `detect_file_duplicates.py` run before the *first*
     ingestion batch, as a documented operational prerequisite, not a code change; (b) going
     forward, `content_hash` should be computed at registration time during stage 1 itself (a small
     addition — compute it once per file, already being read for the hash check, and store it
     immediately) so coverage never drifts again and this cross-corpus check never needs to
     re-hash the *existing* corpus, only ever look it up.

### Stage 2 — In-batch near-duplicate review

1. **Register** survivors as `Image` rows: `status = 'pending'`, `ingestion_batch_id` = the current
   `batch_runs.run_id`. Filename must be preserved verbatim (no UUID-renaming) — `extract_text_from_memes`
   later depends on filename as the lookup key.
2. **Compute embeddings** — `build_image_embeddings.py --status pending --incremental`, scoped
   implicitly to whatever's pending (per the visibility prerequisite's proposed `--status` flag).
3. **Find candidate pairs** — the duplicate-clustering primitive with probe set = corpus filter =
   `ingestion_batch_id = :batch_id AND status = 'pending'` (see that spec's scoping table).
4. **Review — human, via UI.** New page reusing the `MemesList`/`ExploreDuplicatesPage` pattern
   with a `status=pending&batch=...` filter, showing clusters from step 3. See Open Questions below
   re: what signal is actually available to the reviewer at this point (OCR hasn't run yet).
5. **Apply decisions.** Confirmed duplicates: `status = 'rejected'`, file moved out of the active
   `BASE_PATH` tree into a rejected-images location under `BASE_PATH` — not back into
   `PATH_INGESTION_SOURCE`, since the file already left there at the end of stage 1 (Decision #1).
   Exact directory naming/structure is open question 5 below. Row stays, status change only,
   enabling undo.

### Stage 3 — Cross-corpus near-duplicate review

1. Duplicate-clustering primitive with probe set = `ingestion_batch_id = :batch_id AND status =
   'pending'`, corpus filter = `status = 'active'`.
2. **Review — same UI surface**, but the decision is asymmetric (candidates aren't interchangeable
   — one is new, one already-published). Reviewer needs to see the *active* candidate's existing
   context (tags, description, OCR text — all of which exist for it, since it's fully enriched) even
   though the *pending* candidate has none of that yet. Allowed outcomes: reject the new image
   (`status = 'rejected'`, same recoverability as stage 2), or confirm it's not actually a duplicate
   (no state change, proceeds to promotion). "Replace the existing active image" is explicitly not
   an outcome here — out of scope, a different and riskier operation.

### Stage 4 — Promotion

Now genuinely simple, per Decision #1: for each surviving `pending` image in the run,
`UPDATE images SET status = 'active'`. No file move — the file has been in `BASE_PATH` since stage
1. Mark `batch_runs.stage = 'promoted'`. Run `extract_text_from_memes` in its normal incremental
mode — since these images are already registered with the right filename, and are no longer
`pending` (so the visibility prerequisite's skip-if-pending check in `io_producer` no longer
applies to them), the script picks them up exactly like any other unprocessed file in `BASE_PATH`.
Continue the rest of the standard pipeline order from CLAUDE.md unmodified.

---

## Batch / tooling changes summary

| Component | Change |
|---|---|
| `batch/ingest_hash_dedup.py` | **New.** Stage 1: in-batch + cross-corpus hash check, per above. |
| `batch/build_image_embeddings.py` | **Extend** (per visibility prereq) with `--status`. |
| `batch/rebuild_duplicates.py` | **Extend** (per duplicate-clustering prereq) with probe/corpus scoping — ingestion calls the same query builder, not this script's CLI directly. |
| `batch/ingest_promote.py` | **New**, but now trivial — a status flip + `batch_runs.stage` update, no file I/O. |
| `extract_text_from_memes.py` | **Small addition** (per visibility prereq): skip files whose registered image is still `pending`. |
| `tools/agent_duplicates.py` / `review-duplicates` skill | **Not used** for ingestion — see Decision #4. |
| Rest of pipeline (tags, lemmas, descriptions, concepts) | **No change.** |

## Backend / API changes

- List pending images for a run (`status=pending&ingestion_batch_id=...`).
- Batch-scoped duplicate clusters — extends the existing duplicates endpoint with a batch filter,
  backed by the scoped duplicate-clustering query.
- Confirm-reject / undo endpoints (status transitions `pending ↔ rejected`).
- Ingestion run status (`GET /api/ingestion/runs/{id}` — thin wrapper over the `batch_runs` row).
- `backend_api.md` updated per CLAUDE.md's API contract requirement.
- Every endpoint touched by the visibility-audit prerequisite needs its query updated (that spec
  owns the exhaustive list); no new *contract* changes to existing endpoints, just row filtering.

## Frontend changes

- New route reusing `MemesList` with `status`/`batch` filter props, following the existing
  `ExploreDuplicatesPage` pattern of a thin page wrapping a shared list component.
- Review UI needs an explicit reject/undo action per cluster/pair, and — for stage 3 — a way to
  show the active candidate's existing tags/description/OCR alongside the pending candidate's bare
  thumbnail (see Open Questions on signal asymmetry).
- Batch progress/status view, reading the `batch_runs` row via the new endpoint above.

---

## Open questions

1. **Review signal availability — OCR hasn't run yet at stage 2/3, but it's the primary signal
   the existing review process relies on.** The existing `review-duplicates` skill's signal
   priority is explicitly OCR text first, embedding distance second, description third — but
   ingestion's stage ordering deliberately runs dedup *before* OCR (to avoid wasting OCR compute
   on images likely to be rejected). That means a human reviewing an ingestion cluster has only
   thumbnails + a distance number, a strictly weaker signal than what the existing review UI/flow
   gives for the active library — which risks *increasing* the false-positive rate this design is
   specifically trying to avoid (per Decision #4's motivation). Options, none chosen yet:
   - (a) Accept the weaker signal — thumbnails + distance is close to what a human would use
     visually anyway for genuine near-duplicates (same template, near-identical crop).
   - (b) Run OCR (not the rest of enrichment — just OCR, which is meaningfully cheaper than the
     LLM description step) as part of stage 2, before review, so reviewers get text to compare.
     "Wasted" OCR on later-rejected images is a smaller cost than wasted LLM descriptions, so this
     may be an acceptable trade even though it reintroduces some pre-review compute.
   - (c) Keep OCR-less review but make the UI lean harder on side-by-side visual comparison
     (larger thumbnails, overlay/diff view) to compensate.
   This needs a real decision before stage 2's UI is built — recommend (b) as a starting point
   given OCR's relatively low cost, but flagging for discussion rather than deciding unilaterally.
2. **Stage 3 asymmetric review UX** — exact layout/interaction for "here's a new image, here's the
   existing one it resembles, with its tags/description" needs a concrete design, not just the
   allowed-outcomes list above.
3. **Resumability within a stage.** `batch_runs.stage` + `images.status` describe *which stage* a
   run is in and survive across process restarts, which resolves cross-run resumability. Still
   open: if a human review session is interrupted mid-cluster-list within a stage, is there a
   per-cluster "already reviewed" marker, or does the reviewer just re-see already-decided clusters
   (harmless — re-confirming a `rejected` image is a no-op) until everything in the batch is
   decided? Leaning toward the latter (simpler, and idempotent) unless review volume per batch
   turns out large enough that re-scanning already-decided clusters is annoying in practice.
4. **`k`/`threshold` tuning** for the duplicate-clustering primitive — delegated to that spec, but
   ingestion is the first caller that will surface whether the chosen defaults are actually right
   for a "small new batch vs. large existing corpus" shape specifically (as opposed to the
   whole-corpus incremental case that spec's design was primarily reasoned about).
5. **`rejected_ingestion/` directory naming and location** — Decision #1 means stage 2/3 rejects
   move within `BASE_PATH`, not back into `PATH_INGESTION_SOURCE` (which the original proposal's
   `duplicates2`/`duplicates3` naming assumed). Needs a concrete decision on directory structure
   under `BASE_PATH` — e.g. `BASE_PATH/rejected/in_batch/` and `BASE_PATH/rejected/cross_corpus/` —
   before stage 2/3 apply-decision code is written.

## Risks

- **OCR-less review quality (open question 1 above)** is now the primary quality risk for this
  design, directly because of the "images get lost" lesson that motivated moving to human-only
  review in the first place — worth resolving before building the stage 2/3 UI, not after.
- **Visibility leak** — owned by the visibility prerequisite's audit + contract test; not
  ingestion-specific risk anymore, but ingestion is the first real-world exercise of that audit
  being complete.
- **`k`/`threshold` mistuned for the new-batch-vs-large-corpus shape** — could either miss genuine
  cross-corpus duplicates (k too low, or threshold too tight) or overwhelm reviewers with
  borderline candidates (threshold too loose) — needs empirical tuning once real data is available.

## Out of scope

- Automated (non-human-reviewed) duplicate resolution.
- Multi-environment or cross-environment ingestion routing.
- UI/tooling for undoing a promotion once the full enrichment pipeline has run on an image (undo
  during stages 2/3, before promotion, is in scope; undo after promotion is not).

## Next steps

1. Implement the three prerequisites (any order, though image-visibility and batch-run-tracking are
   both needed before ingestion-specific work starts; duplicate-clustering can follow once stage 2
   work begins).
2. Resolve open questions 1 (OCR timing) and 5 (directory layout) — both block writing stage 2/3
   apply-decision code.
3. Phased build: Phase A — stage 1 (hash dedup, in-batch + cross-corpus, lowest risk, no schema
   dependency beyond `content_hash` backfill). Phase B — stage 2 (registration, embeddings, scoped
   clustering, review UI). Phase C — stage 3 (cross-corpus review, asymmetric UX). Phase D —
   promotion (now small) + pipeline hookup + run-status UI polish.
