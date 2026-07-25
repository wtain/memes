# Ingestion Pipeline Design (Draft)

**Date:** 2026-07-24 (revised 2026-07-25 — brainstorming rounds 1–3)
**Status:** Draft — architecture and UX decisions below are confirmed with the user; only
empirical tuning (Open Questions) remains. Builds on `2026-07-24-ingestion-pipeline-pre-spec.md`.

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
6. **Rejected images move to a single `BASE_PATH/rejected/` directory**, not split by tier — kept
   deliberately separate from `PATH_INGESTION_SOURCE/duplicates/` (stage 1's hash-dedup rejects) so
   the two directories read unambiguously: `duplicates/` is exact-hash matches caught before a file
   ever became a DB row or entered `BASE_PATH`; `rejected/` is anything that made it further (had an
   embedding, went through human review) and was rejected there. Since the directory no longer
   encodes *which tier* rejected an image, per-tier audit relies on the DB — the `status = 'rejected'`
   row plus the `tmp_duplicates` pair that triggered it (queryable, not lost) rather than the
   filename path.
7. **Review-queue resumability is per-pair, tier-scoped, and explicit — not "just re-show
   everything, it's harmless."** See the dedicated section below; the short version is that
   "confirmed not a duplicate" is currently a no-op with no state change, so without an explicit
   marker the same cluster would resurface every time the queue reopens, indefinitely, not just
   occasionally.
8. **Cluster review is one uniform per-pending-image `reject`/`keep` decision**, not two separate
   interaction modes for in-batch vs. cross-corpus matches. See "Cluster review UX" below — the
   two-mode framing broke down once a single cluster could contain both kinds of match at once.

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
   library) as Tier A's cutoff. **One merged query**, not two: probe =
   `ingestion_batch_id = :batch_id AND status = 'pending'`, corpus = `status = 'active' OR
   (status = 'pending' AND ingestion_batch_id = :batch_id)` — the active library and this image's
   own batch siblings in a single scan, per the duplicate-clustering prereq's `match_source`
   addition. Pairs are grouped into review clusters via a batch-scoped union-find (not the global
   `clusterize.py`) so a cluster can naturally mix new and existing images.
3. **Review — human, via UI, one queue.** Thumbnails + distance only (no OCR yet — at this
   tightness that's sufficient; this is exactly what makes Tier A safe to review pre-OCR, unlike
   Tier B). Per-pending-image `reject`/`keep` decisions, uniform regardless of `match_source` —
   see "Cluster review UX" below for why this replaced an earlier "pick a keeper" framing.
4. **Apply decisions.** Confirmed duplicates: `status = 'rejected'`; file moved from `BASE_PATH`
   into `BASE_PATH/rejected/` (Decision #6). Row stays for undo.

### Stage 3 — OCR pre-pass + Tier B: loose-similarity embedding dedup

1. **OCR pre-pass**, Tier-A survivors only: `extract_text_from_memes.py --status pending`
   (new flag, mirrors `build_image_embeddings.py`'s). OCR only — not tags, not descriptions — kept
   cheap deliberately, since some of these images will still be rejected in this same stage.
2. **Find candidate pairs**, loose band (proposed `0.05`–`0.3`, upper bound matching the
   duplicate-clustering prereq's general candidate cutoff) — same merged query and same
   batch-scoped clustering as Tier A step 2, different threshold band.
3. **Review — human, via UI, one queue**, now with OCR text available — same signal priority the
   existing `review-duplicates` skill already established (OCR text first, to catch "same
   template, different joke" and correctly *not* reject those; embedding distance as a secondary
   check). Same per-pending-image `reject`/`keep` decision as Tier A, now informed by OCR text and,
   for `cross_corpus` edges, the active candidate's full existing context. See "Cluster review UX"
   below.
4. **Apply decisions** — same mechanics as stage 2 step 4.

### Stage 4 — Promotion

For each surviving `pending` image in the run: `UPDATE images SET status = 'active'`. No file move
— the file has been in `BASE_PATH` since stage 1. Mark `batch_runs.stage = 'promoted'`. Continue
the rest of the standard pipeline order from CLAUDE.md — `extract_text_from_memes` is effectively a
no-op for these images now (already OCR'd in stage 3; its own incremental/should-process logic
skips already-processed images the normal way), so the pipeline picks up at
`build_tags_from_ocr` / `build_ocr_lemmas` / `build_image_descriptions` / etc., unmodified.

---

## Cluster review UX

Resolves what was open question 1 (Tier B cross-corpus asymmetric review). The earlier draft
framed this as two different interaction modes — "pick a keeper" for `in_batch` matches vs.
"reject-or-confirm" for `cross_corpus` matches — which doesn't hold up once a single cluster can
contain both kinds of member at once (e.g. two new near-identical uploads that *also* both match an
existing library image — a realistic case once in-batch and cross-corpus review were merged into
one query). Trying to run two different decision paradigms on the same screen for a mixed cluster
is more complex than the problem needs.

**Resolution: one uniform decision per pending image, not per pair, not per match type.** Every
`pending`-status member of a displayed cluster gets an independent `reject` / `keep` call from the
reviewer — that's the entire decision surface, for both tiers, regardless of whether the member's
edges are `in_batch`, `cross_corpus`, or both. "Pick a keeper among siblings" was never really a
distinct operation — it's just "reject all but one," which is already expressible as N independent
reject/keep calls. Active-library images are never a decision target — they're always shown, never
touched (out of scope, per the Stage 3 description, to ever replace/modify an existing active
image) — so there's no "who wins" framing needed for cross-corpus matches either: the active image
implicitly "wins" by simply not being an option to reject.

**What the reviewer sees, per cluster:**

- **New images in this batch** (one card per `pending` member): thumbnail, OCR text (Tier B only —
  absent in Tier A by design), the `reject`/`keep` toggle, and the distance to each thing it
  matched, labelled by `match_source`.
- **Matches found in the library** (one card per `active` member connected to the cluster, if any):
  thumbnail, full existing context — OCR text, description, tags — shown read-only for comparison.
  No action available on these; they exist purely to inform the pending-image decisions above them.

**Partial resolution is allowed.** A reviewer doesn't have to decide every member of a cluster in
one sitting — submitting decisions for a subset is fine; undecided members simply remain in the
queue (per Review-queue resumability below), so "I'm confident about two of these four but not the
other two" doesn't force a guess on the rest.

---

## Review-queue resumability

**Problem.** Within a stage, a review session can span multiple sittings (a human isn't going to
clear a whole batch in one pass). Rejections are self-cleaning — a rejected image's `status`
changes, so it drops out of the duplicate-clustering query's probe/corpus filters and can never
resurface. But "confirmed not a duplicate" leaves the image `pending` with no other state change,
so without an explicit marker, reopening the queue mid-review would re-show every
already-cleared cluster, forever — not a one-time nuisance, since nothing ever removes it.

**Design.** Two nullable `TIMESTAMP` columns on `tmp_duplicates` (extending the schema from
`2026-07-25-duplicate-clustering-incremental-design.md`, ingestion-specific, not part of that
spec's own scope): `tier_a_reviewed_at`, `tier_b_reviewed_at`. Set only by an explicit "keep both,
not a duplicate" decision on that specific pair, in that specific tier's review UI.

- **Per-pair, not per-image.** An image cleared in Tier A says nothing about Tier B — Tier B is a
  deliberately independent second look, with OCR text Tier A didn't have (Decision #5). A
  per-image marker shared across tiers would let Tier B silently inherit Tier A's blind-to-OCR
  verdict, defeating the reason Tier B exists. Per-pair, tier-scoped columns mean: if Tier B's
  looser threshold rediscovers the *exact same* pair Tier A already cleared, it's suppressed
  (no new signal changes a verdict already made on this specific pair); if Tier B surfaces a *new*
  pair for that same image (a looser-band neighbor Tier A's tighter search never considered), it's
  correctly treated as unreviewed.
- **Queue query per tier:** pending-side pairs in that tier's distance band where the tier's
  `reviewed_at` column is `NULL` and the pending-side image is still `status = 'pending'`
  (rejected images are already excluded by the underlying duplicate-clustering query's own status
  filter, so no separate check is needed here).
- **Resolving a cluster, not a pair.** The review UI shows a whole connected cluster (via the
  batch-scoped union-find) in one screen; the "resolve" action takes per-member decisions
  (`reject` or `keep`) and applies them atomically — `reject` flips that image's `status`;
  `keep` sets the current tier's `reviewed_at` on every `tmp_duplicates` row touching that image
  within the displayed cluster, not just one pair, so the whole cluster's relevant edges clear
  together rather than needing to be resolved one pair at a time.

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
- Resolve-cluster endpoint (`POST .../clusters/{cluster_key}/resolve`, per-member `reject`/`keep`
  decisions applied atomically — see Review-queue resumability above) plus a separate undo endpoint
  for reverting a `rejected` image back to `pending`.
- Ingestion run status (`GET /api/ingestion/runs/{id}` — thin wrapper over the `batch_runs` row,
  including current `stage`).
- `backend_api.md` updated per CLAUDE.md's API contract requirement.
- Every endpoint touched by the visibility-audit prerequisite needs its query updated (that spec
  owns the exhaustive list); no new *contract* changes to existing endpoints, just row filtering.

## Frontend changes

- New route reusing `MemesList` with `status`/`batch` filter props, following the existing
  `ExploreDuplicatesPage` pattern of a thin page wrapping a shared list component.
- Two review queues — one per tier, each merging in-batch and cross-corpus matches per the
  duplicate-clustering prereq's `match_source`. Per "Cluster review UX" above: one card per
  `pending` member (thumbnail, OCR text in Tier B only, `reject`/`keep` toggle, per-edge distance
  labelled by `match_source`) plus, where relevant, read-only cards for connected `active` members
  showing their existing OCR/description/tags.
- Resolve action submits whatever subset of member decisions the reviewer has made (partial
  resolution allowed); undecided members stay in the queue.
- Batch progress/status view, reading the `batch_runs` row via the new endpoint above.

---

## Open questions

1. **`k` / threshold tuning** for both tiers — Tier A reuses `clusterize.py`'s existing `0.05` as a
   starting point (a value already validated for the active library, though not specifically for
   "small new batch vs. large existing corpus"); Tier B's `0.05`–`0.3` band and both tiers' `k` are
   unvalidated guesses pending real data.

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
2. Resolve open question 1 (`k`/threshold tuning) empirically once real batch data is available —
   not a blocker for building the pipeline, since both tiers ship with reasoned starting defaults.
3. Phased build: Phase A — stage 1 (hash dedup, in-batch + cross-corpus, lowest risk, no schema
   dependency beyond `content_hash` backfill). Phase B — Tier A (registration, embeddings, merged
   tight-threshold review queue). Phase C — Tier B (OCR pre-pass, merged loose-threshold review
   queue). Phase D — promotion + pipeline hookup + run-status UI polish.
