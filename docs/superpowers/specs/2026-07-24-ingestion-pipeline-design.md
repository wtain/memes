# Ingestion Pipeline Design (Draft)

Status: done
Originates from: docs/superpowers/specs/2026-07-24-ingestion-pipeline-pre-spec.md
Follow-ups: docs/superpowers/specs/2026-07-25-batch-run-tracking-design.md, docs/superpowers/specs/2026-07-25-image-visibility-status-design.md, docs/superpowers/specs/2026-07-25-duplicate-clustering-incremental-design.md

**Date:** 2026-07-24 (revised 2026-07-25 — brainstorming rounds 1–3)

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
9. **`k` raised from `20` to `50`**, empirically validated 2026-07-25 against real `tmp_duplicates`
   data: `metal`'s worst-case real cluster degree was 8 (comfortable headroom even at 20), but
   `general`'s was 37 — `k=20` was silently truncating real near-duplicate clusters there (neighbors
   beyond the 20th-nearest are never fetched by the KNN query at all, not filtered out afterward —
   see `2026-07-25-duplicate-clustering-incremental-design.md`'s "What `k` actually does" for why).
   Kept as one global value rather than a per-environment override: raising `k` costs sparse corpora
   nothing, since `LIMIT k` candidates still get filtered by `:threshold` afterward — a sparse
   image's stored results are identical whether `k` is 20 or 50, only the traversal cost differs
   marginally. `it` wasn't sampled in this validation pass; 50 gives it slack too.
10. **OCR now runs before Tier A review, not between Tier A and Tier B.** The original design's
    premise — Tier A's tight threshold is visually decisive enough that thumbnails alone are safe,
    OCR only needed for Tier B's looser matches — was empirically tested 2026-07-25 and found not to
    hold universally: on `general`, the single highest-degree tight-band (`<0.05`) cluster turned out
    to be three *different* plain-white-background/black-text quote memes with entirely different
    text, clustered because CLIP embeds visual layout/format, not textual content, for this kind of
    image. A human reviewing Tier A on thumbnails alone could plausibly reject two unrelated memes
    for looking like "the same template" — precisely the failure class that motivated moving
    duplicate review off the agent-driven skill and onto human-in-the-loop UI in the first place
    (Decision #4). Fix: run `extract_text_from_memes.py --status pending` right after embeddings,
    before *either* tier's review, not just before Tier B's. Needed **no functional code change** —
    `IngestionService.list_clusters()` already fetches OCR text unconditionally per tier, and the
    frontend already renders it whenever present — this was purely an operational-ordering fix (see
    Stage 2/3 below) plus updated comments/docs. Considered and rejected: a separate, cheaper
    "lightweight" OCR pass just for Tier A — rejected because the failure mode specifically requires
    *reliable* text transcription to disambiguate near-identical-looking memes, which is exactly
    where a lower-quality OCR pass would be weakest, not a place to cut corners. The tier split
    itself still earns its keep for review ergonomics (confidence/volume batching), just no longer
    implies "Tier A doesn't need OCR."

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

### Stage 2 — Embeddings + OCR, then Tier A: strong-similarity embedding dedup

1. **Compute embeddings** — `build_image_embeddings.py --status pending --incremental`.
2. **OCR pre-pass** — `extract_text_from_memes.py --status pending`. Runs here, before *either*
   tier's review, not between Tier A and Tier B as originally designed — see Decision #10: empirical
   validation found Tier A's original "thumbnails alone are decisive" premise doesn't hold for all
   content, so both tiers need OCR text available, not just Tier B.
3. **Find candidate pairs**, tight threshold — reuse `clusterize.py`'s existing `0.05`
   (`PROXIMITY_THRESHOLD`, already the codebase's "confirmed duplicate" cutoff for the active
   library) as Tier A's cutoff. **One merged query**, not two: probe =
   `ingestion_batch_id = :batch_id AND status = 'pending'`, corpus = `status = 'active' OR
   (status = 'pending' AND ingestion_batch_id = :batch_id)` — the active library and this image's
   own batch siblings in a single scan, per the duplicate-clustering prereq's `match_source`
   addition. Pairs are grouped into review clusters via a batch-scoped union-find (not the global
   `clusterize.py`) so a cluster can naturally mix new and existing images.
4. **Review — human, via UI, one queue.** Thumbnails + distance + OCR text. Per-pending-image
   `reject`/`keep` decisions, uniform regardless of `match_source` — see "Cluster review UX" below
   for why this replaced an earlier "pick a keeper" framing.
5. **Apply decisions.** Confirmed duplicates: `status = 'rejected'`; file moved from `BASE_PATH`
   into `BASE_PATH/rejected/` (Decision #6). Row stays for undo.

### Stage 3 — Tier B: loose-similarity embedding dedup

OCR already ran in Stage 2, so this stage is just the loose-threshold pass — no separate OCR
step needed here (a simplification over the original design, which ran OCR between the tiers).

1. **Find candidate pairs**, loose band (`0.05`–`0.3`, upper bound matching the
   duplicate-clustering prereq's general candidate cutoff) — same merged query and same
   batch-scoped clustering as Tier A step 3, different threshold band.
2. **Review — human, via UI, one queue** — same OCR-text-first signal priority as Tier A (the
   existing `review-duplicates` skill's established priority: OCR text first, to catch "same
   template, different joke" and correctly *not* reject those; embedding distance as a secondary
   check). Same per-pending-image `reject`/`keep` decision as Tier A, informed by OCR text and,
   for `cross_corpus` edges, the active candidate's full existing context. See "Cluster review UX"
   below.
3. **Apply decisions** — same mechanics as stage 2 step 5.

### Stage 4 — Promotion

For each surviving `pending` image in the run: `UPDATE images SET status = 'active'`. No file move
— the file has been in `BASE_PATH` since stage 1. Mark `batch_runs.stage = 'promoted'`. Continue
the rest of the standard pipeline order from CLAUDE.md — `extract_text_from_memes` is effectively a
no-op for these images now (already OCR'd in stage 2; its own incremental/should-process logic
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
  `pending` member (thumbnail, OCR text, `reject`/`keep` toggle, per-edge distance labelled by
  `match_source`) plus, where relevant, read-only cards for connected `active` members showing
  their existing OCR (description/tags not surfaced yet — see Open Questions).
- Resolve action submits whatever subset of member decisions the reviewer has made (partial
  resolution allowed); undecided members stay in the queue.
- Batch progress/status view, reading the `batch_runs` row via the new endpoint above.

---

## Open questions

`k`/threshold tuning (formerly open question 1) was empirically resolved 2026-07-26 against real
`tmp_duplicates` data on `metal` and `general` — see Decision #9/#10 above and
`2026-07-25-duplicate-clustering-incremental-design.md`'s tuning section for the full findings.
Remaining smaller items:

1. **`it` (the smallest environment) wasn't sampled** in the empirical validation pass — its
   corpus-density characteristics are unconfirmed; `k=50` should be adequate (it's well above both
   sampled environments' worst cases) but hasn't been checked against `it` specifically.
2. **Stage 3 (Tier B) cross-corpus asymmetric review still only shows OCR text for the active
   candidate, not its tags/description** — the original design called for "the active candidate's
   full existing context"; only OCR shipped so far (see Frontend changes above).
3. **Resumability within a stage** (a human review session interrupted mid-cluster-list) still
   relies on "re-showing an already-decided cluster is harmless" rather than a dedicated per-cluster
   progress marker — untested at any real review volume large enough to know if that's actually
   annoying in practice.

## Risks

- **Visibility leak** — owned by the visibility prerequisite's audit + contract test; exercised for
  real during this feature's own development with no leaks found.
- **Tier A's "thumbnails are decisive" premise turned out to be content-type-dependent, not
  universal** (Decision #10) — mitigated by moving OCR earlier rather than assumed away; worth
  re-checking if a *new* failure mode surfaces once more real ingestion batches run, since the
  empirical validation so far only examined `metal` and `general`'s existing active-library data,
  not actual ingestion batches at volume.
- **`general`'s duplicate-detection density is real and non-trivial** (max observed tight-band
  cluster degree 37) — large batches on `general` specifically may produce big, review-heavy
  clusters; not yet observed at ingestion scale (only exercised via `metal`'s small real batch so
  far).

## Out of scope

- Automated (non-human-reviewed) duplicate resolution.
- Multi-environment or cross-environment ingestion routing.
- UI/tooling for undoing a promotion once the full enrichment pipeline has run on an image (undo
  during stages 2/3, before promotion, is in scope; undo after promotion is not).

## Status

All four stages (hash dedup, Tier A, Tier B, promotion) are implemented and have each been run
end to end against real data on `metal`: 3 images intake → 1 rejected (confirmed duplicate via
Tier A cross-corpus match) → 2 promoted to `active`, run marked `completed`. `general` and `it`
haven't had a real ingestion batch run yet — only the active-library duplicate data used for
threshold validation.
