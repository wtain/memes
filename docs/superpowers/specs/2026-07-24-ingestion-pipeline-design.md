# Ingestion Pipeline Design (Draft)

**Date:** 2026-07-24 (revised 2026-07-25 — brainstorming rounds 1 and 2)
**Status:** Draft — architecture decisions below are confirmed with the user; a handful of
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
| [`2026-07-25-duplicate-clustering-incremental-design.md`](2026-07-25-duplicate-clustering-incremental-design.md) | Replaces `rebuild_duplicates.py`'s O(n²) whole-corpus cross join with an incremental, threshold-bounded, HNSW-assisted KNN query, parameterized by probe/corpus scope *and* a distance cutoff. Ingestion calls it with different cutoffs for its two dedup tiers — see below. |

Implementation order isn't strictly linear (see Next Steps), but conceptually: batch-run-tracking
and image-visibility are foundational (nothing ingestion-specific can be built without them);
duplicate-clustering is needed once tier-A work starts.

## Goal

Give each environment (metal / general / it) a repeatable way to bring a batch of new images from
an external drop location into its active library, filtering out exact and near-duplicates —
against both the incoming batch itself and the existing corpus — before the expensive per-image
enrichment pipeline (tags, LLM descriptions) runs on them, without recomputing CLIP embeddings
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
| **Ingestion run** | One `batch_runs` row, `kind = 'ingestion'`; `stage` tracks progress (`hash_dedup` → `tier_a_review` → `ocr_prepass` → `tier_b_review` → `promoted`). |
| **Tier A** | Pre-OCR near-duplicate check: hash + *tight*-threshold embedding similarity, decisive on thumbnails alone. |
| **Tier B** | Post-OCR-prepass near-duplicate check: *loose*-threshold embedding similarity, where OCR text is needed to tell a true repost from a same-template meme variant. |

---

## Decisions confirmed 2026-07-25

1. **No separate pending-file storage location.** Images move from `PATH_INGESTION_SOURCE` into
   `BASE_PATH` immediately after stage 1 (hash dedup), exactly like the pipeline moves files today
   — they just aren't visible yet because `status = 'pending'`. File-serving code needs no changes.
   Promotion (final stage) is therefore a pure status flip, not a file move.
2. **Cross-corpus hash check added to stage 1.** New images are hash-checked against the existing
   corpus's `content_hash` values too, not just against each other. See Stage 1 below for the
   dependency this creates.
3. **Duplicate clustering is incremental and threshold-bounded**, per the dedicated prerequisite
   spec — active-library incremental rebuilds and in-progress ingestion review can safely coexist,
   since the new design never drops `tmp_duplicates` and inserts are idempotent.
4. **Review is purely human-driven, via UI — not the existing agent skill.** The existing
   `review-duplicates` skill / `tools/agent_duplicates.py` pattern has produced false positives in
   practice that permanently lost images. Rejections are recoverable by design (`status =
   'rejected'`, not a delete) specifically because of that history.
5. **Near-duplicate detection is two-tiered by threshold, with a lightweight OCR pass in between**
   (resolves what was open question 1 in the previous draft). Tight-threshold matches (Tier A) are
   visually decisive without OCR and are reviewed pre-OCR. Loose-threshold matches (Tier B) need
   OCR text to distinguish a real repost from a meme variant (matching the existing
   `review-duplicates` skill's own signal priority: OCR text first, embedding distance second) — so
   ingestion runs a **cheap, OCR-only pass** (not the rest of enrichment) on Tier-A survivors before
   Tier B review, and only then promotes. This keeps the "nothing skips review before becoming
   visible" guarantee intact, at the cost of one more stage and an OCR pass earlier than the
   original single-pass design assumed.

---

## Stage-by-stage design

### Stage 0 — Intake

`PATH_INGESTION_SOURCE` is a new per-environment secret in `.env.<environment>`, analogous to
`BASE_PATH`. One source directory per environment. A new ingestion run is refused if
`BatchRunRepository.get_active_run(kind="ingestion")` returns a row for this environment's DB.

### Stage 1 — Hash dedup (in-batch and cross-corpus)

Pure filesystem + hash-lookup work, no embeddings involved.

1. **In-batch:** hash every file in `PATH_INGESTION_SOURCE` (reusing
   `batch/utils/file_hash.py`'s `sha256_file`/`files_are_identical`), union-find on identical
   hashes, keep one per cluster, move the rest to `PATH_INGESTION_SOURCE/duplicates/`.
2. **Cross-corpus:** look up each survivor's hash against `images.content_hash` for
   `status = 'active'` rows; a match moves it to `PATH_INGESTION_SOURCE/duplicates/` too (same
   tier — both are byte-identical decisions).
   - **Dependency:** only a cheap indexed lookup if the existing corpus's `content_hash` is
     actually populated, which today only happens lazily via `detect_file_duplicates.py`. Each
     environment needs a one-time backfill run before its *first* ingestion batch (operational
     prerequisite, not a code change); going forward, `content_hash` should be computed and stored
     at registration time (stage 2 below) so coverage never drifts again.

Survivors register as `Image` rows (`status = 'pending'`, `ingestion_batch_id` = the current
`batch_runs.run_id`) and move into `BASE_PATH` here, per Decision #1. Filename preserved verbatim —
`extract_text_from_memes` depends on it as the lookup key later.

### Stage 2 — Tier A: strong-similarity embedding dedup (pre-OCR)

1. **Compute embeddings** — `build_image_embeddings.py --status pending --incremental`.
2. **Find candidate pairs**, tight threshold — reuse `clusterize.py`'s existing `0.05`
   (`PROXIMITY_THRESHOLD`, already the codebase's "confirmed duplicate" cutoff for the active
   library) as Tier A's cutoff, rather than inventing a new number:
   - *In-batch:* probe = corpus = `ingestion_batch_id = :batch_id AND status = 'pending'`.
   - *Cross-corpus:* probe = `ingestion_batch_id = :batch_id AND status = 'pending'`, corpus =
     `status = 'active'`.
3. **Review — human, via UI**, thumbnails + distance only (no OCR yet — at this tightness that's
   sufficient; this is exactly what makes Tier A safe to review pre-OCR, unlike Tier B). In-batch
   matches: pick a keeper among near-identical siblings. Cross-corpus matches: the existing active
   image wins by default; the new one is the candidate for rejection.
4. **Apply decisions.** Confirmed duplicates: `status = 'rejected'`; file moved from `BASE_PATH`
   into a rejected-images location (naming TBD — open question 5). Row stays for undo.

### Stage 3 — OCR pre-pass + Tier B: loose-similarity embedding dedup

1. **OCR pre-pass**, Tier-A survivors only: `extract_text_from_memes.py --status pending`
   (new flag, mirrors `build_image_embeddings.py`'s). OCR only — not tags, not descriptions — kept
   cheap deliberately, since some of these images will still be rejected in this same stage.
2. **Find candidate pairs**, loose band (proposed `0.05`–`0.3`, upper bound matching the
   duplicate-clustering prereq's general candidate cutoff):
   - *In-batch:* same scoping as Tier A step 2, different threshold band.
   - *Cross-corpus:* same scoping as Tier A step 2, different threshold band.
3. **Review — human, via UI**, now with OCR text available — same signal priority the existing
   `review-duplicates` skill already established (OCR text first, to catch "same template,
   different joke" and correctly *not* reject those; embedding distance as a secondary check).
   Cross-corpus matches are asymmetric (see Open Questions — exact UX still undecided): reviewer
   needs the *active* candidate's existing tags/description/OCR alongside the *pending* candidate's
   now-available OCR. Allowed outcomes: reject the new image, or confirm it's not a duplicate
   (proceeds to promotion). "Replace the existing active image" is out of scope.
4. **Apply decisions** — same mechanics as stage 2 step 4.

### Stage 4 — Promotion

For each surviving `pending` image in the run: `UPDATE images SET status = 'active'`. No file move
— the file has been in `BASE_PATH` since stage 1. Mark `batch_runs.stage = 'promoted'`. Continue
the rest of the standard pipeline order from CLAUDE.md — `extract_text_from_memes` is effectively a
no-op for these images now (already OCR'd in stage 3; its own incremental/should-process logic
skips already-processed images the normal way), so the pipeline picks up at
`build_tags_from_ocr` / `build_ocr_lemmas` / `build_image_descriptions` / etc., unmodified.

---

## Batch / tooling changes summary

| Component | Change |
|---|---|
| `batch/ingest_hash_dedup.py` | **New.** Stage 1: in-batch + cross-corpus hash check, registration, move into `BASE_PATH`. |
| `batch/build_image_embeddings.py` | **Extend** (per visibility prereq) with `--status`. |
| `batch/extract_text_from_memes.py` | **Extend** (per visibility prereq) with `--status`, used for the stage 3 OCR pre-pass. |
| `batch/rebuild_duplicates.py` | **Extend** (per duplicate-clustering prereq) with probe/corpus/threshold scoping — ingestion calls the same query builder with two different threshold bands, not this script's CLI directly. |
| `batch/ingest_promote.py` | **New**, trivial — a status flip + `batch_runs.stage` update, no file I/O. |
| `tools/agent_duplicates.py` / `review-duplicates` skill | **Not used** for ingestion — see Decision #4. |
| Rest of pipeline (tags, lemmas, descriptions, concepts) | **No change.** |

## Backend / API changes

- List pending images for a run (`status=pending&ingestion_batch_id=...`).
- Batch-scoped duplicate clusters, parameterized by tier (threshold band) — extends the existing
  duplicates endpoint with batch + threshold-band filters, backed by the scoped duplicate-clustering
  query.
- Confirm-reject / undo endpoints (status transitions `pending ↔ rejected`).
- Ingestion run status (`GET /api/ingestion/runs/{id}` — thin wrapper over the `batch_runs` row,
  including current `stage`).
- `backend_api.md` updated per CLAUDE.md's API contract requirement.
- Every endpoint touched by the visibility-audit prerequisite needs its query updated (that spec
  owns the exhaustive list); no new *contract* changes to existing endpoints, just row filtering.

## Frontend changes

- New route reusing `MemesList` with `status`/`batch` filter props, following the existing
  `ExploreDuplicatesPage` pattern of a thin page wrapping a shared list component.
- Two review views (or one view with a tier toggle) — Tier A (thumbnails + distance only) and Tier B
  (thumbnails + distance + OCR text, and for cross-corpus matches, the active candidate's existing
  tags/description too).
- Explicit reject/undo action per cluster/pair.
- Batch progress/status view, reading the `batch_runs` row via the new endpoint above.

---

## Open questions

1. **Stage 3 (Tier B) cross-corpus asymmetric review UX** — exact layout/interaction for "here's a
   new image with fresh OCR text, here's the existing corpus image it resembles, with its full
   tags/description/OCR" needs a concrete design, not just the allowed-outcomes list above.
2. **Six review queues is a lot of surface area** (in-batch × cross-corpus × Tier A × Tier B, though
   Tier A's two are thumbnail-only and could plausibly be merged into one combined-source queue
   with a "matched within batch" vs "matched in library" badge, rather than two separate views).
   Worth deciding whether to merge in-batch/cross-corpus within a tier before building the UI, since
   it changes the review endpoint shape.
3. **Resumability within a stage.** `batch_runs.stage` + `images.status` describe *which stage* a
   run is in and survive across process restarts. Still open: if a human review session is
   interrupted mid-cluster-list within a stage, is there a per-cluster "already reviewed" marker, or
   does the reviewer just re-see already-decided clusters (harmless — re-confirming a `rejected`
   image is a no-op)? Leaning toward the latter (simpler, idempotent) unless review volume per batch
   turns out large enough for re-scanning to be annoying in practice.
4. **`k` / threshold tuning** for both tiers — Tier A reuses `clusterize.py`'s existing `0.05` as a
   starting point (a value already validated for the active library, though not specifically for
   "small new batch vs. large existing corpus"); Tier B's `0.05`–`0.3` band and both tiers' `k` are
   unvalidated guesses pending real data.
5. **Rejected-images directory naming and location** — Decision #1 means Tier A/B rejects move
   within `BASE_PATH`, not back into `PATH_INGESTION_SOURCE` (the original proposal's
   `duplicates2`/`duplicates3` naming assumed the latter). Needs a concrete directory structure
   under `BASE_PATH` — e.g. `BASE_PATH/rejected/tier_a/` and `BASE_PATH/rejected/tier_b/` — before
   stage 2/3 apply-decision code is written.

## Risks

- **Visibility leak** — owned by the visibility prerequisite's audit + contract test; ingestion is
  the first real-world exercise of that audit being complete.
- **`k`/threshold mistuned for the new-batch-vs-large-corpus shape**, either tier — could miss
  genuine duplicates or overwhelm reviewers with borderline candidates; needs empirical tuning once
  real data is available.
- **OCR pre-pass cost creep** — if Tier A's threshold is too tight (rejecting too little), the OCR
  pre-pass ends up running on most of a batch, eroding the compute-avoidance rationale for
  splitting into two tiers in the first place. Worth monitoring in practice, not just assuming the
  split pays for itself.

## Out of scope

- Automated (non-human-reviewed) duplicate resolution.
- Multi-environment or cross-environment ingestion routing.
- UI/tooling for undoing a promotion once the full enrichment pipeline has run on an image (undo
  during stages 2/3, before promotion, is in scope; undo after promotion is not).

## Next steps

1. Implement the three prerequisites (image-visibility and batch-run-tracking are both needed
   before ingestion-specific work starts; duplicate-clustering can follow once Tier A work begins).
2. Resolve open questions 1/2 (review UX/queue shape) and 5 (directory layout) — all three block
   writing stage 2/3 apply-decision code.
3. Phased build: Phase A — stage 1 (hash dedup, in-batch + cross-corpus, lowest risk, no schema
   dependency beyond `content_hash` backfill). Phase B — Tier A (registration, embeddings, tight
   scoped clustering, thumbnail-only review UI). Phase C — Tier B (OCR pre-pass, loose scoped
   clustering, OCR-aware review UI, asymmetric cross-corpus UX). Phase D — promotion + pipeline
   hookup + run-status UI polish.
