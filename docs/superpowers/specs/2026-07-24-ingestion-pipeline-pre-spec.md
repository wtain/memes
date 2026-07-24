# Ingestion Pipeline — Pre-Spec (Assessment)

**Date:** 2026-07-24
**Status:** Assessment of an initial proposal, written before the full design draft. Not an implementation plan.

---

## The proposal (as given)

New images land in `PATH_INGESTION_SOURCE`. Before they're folded into an environment's active
library (`BASE_PATH` + DB), they go through a funnel:

1. **Coarse filter — file hash dedup.** Compare content hashes within the incoming batch; move
   exact duplicates to `PATH_INGESTION_SOURCE/duplicates`.
2. **In-batch near-dup review.** Compute embeddings for the surviving images, cluster them (as the
   environment already does for its active library), surface clusters to a human (or agent) for
   review in a UI similar to the existing duplicate-review flow, scoped to just the new batch.
   Confirmed duplicates move to `PATH_INGESTION_SOURCE/duplicates2`.
3. **Cross-corpus near-dup review.** For the remaining images, search for near-duplicates against
   the *existing* environment corpus (like the existing similarity search), again with human/agent
   review. Confirmed duplicates move to `PATH_INGESTION_SOURCE/duplicates3`.
4. **Promotion.** Remaining images move into `BASE_PATH`; `extract_text_from_memes` runs in
   incremental mode, followed by the rest of the batch pipeline. Embeddings computed in step 2
   should **not** be recomputed — they should carry forward.

A UI is needed to let a human watch/review pre-ingested images before promotion — possibly via a
separate table or separate database.

---

## Assessment

### What's sound

- The three-tier funnel (exact hash → in-batch near-dup → cross-corpus near-dup) is the right
  shape and mirrors how large-scale dedup pipelines are usually built: cheapest/most-precise filter
  first, most expensive/fuzziest filter last.
- Reusing the existing duplicate-review pattern (cluster → human/agent decision → mark → move) is
  correct — the environment already has this end-to-end via `rebuild_duplicates.py` +
  `clusterize.py` + `tools/agent_duplicates.py` + the `review-duplicates` skill +
  `ExploreDuplicatesPage.tsx`. No need to invent a new review UX from scratch, only to scope the
  existing one.
- Treating stage 3 (new vs. existing corpus) as a *different* operation from stage 2 (new vs. new)
  is the right call, not just a simplification — they have different costs (see below) and reusing
  the *existing similarity search* endpoint pattern for stage 3 is a better fit than reusing the
  cross-join duplicate pipeline.
- Not recomputing embeddings on promotion is the right goal — CLIP embedding is one of the more
  expensive steps in the pipeline, and computing it twice for the same file is pure waste.
- `PATH_INGESTION_SOURCE/duplicates`, `duplicates2`, `duplicates3` as separate move targets per
  stage is a reasonable, auditable trail (each stage's decisions are separately inspectable /
  reversible) rather than one bucket.

### Where the plan needs more thought

**1. Where do pending images and their embeddings live during stages 1–3?**

This is the load-bearing decision for the whole spec — it determines whether "don't recompute
embeddings" is actually achievable and how much of the existing dup-review stack is reusable
as-is vs. needs forking.

- **Same DB as the target environment, with a new visibility/status marker.** Embeddings are FK'd
  to `images.id`; if pending images are registered in the *same* `images`/`embeddings` tables from
  the start, "promotion" becomes flipping a status flag and moving a file on disk — the embedding
  row never moves or gets recomputed. This also means the *existing* similarity-search query
  (cosine distance over the HNSW index) can be reused directly for stage 3, since pending and
  active images are the same table.
- **Separate staging table/DB.** Cleaner isolation guarantee (zero chance of a pending image
  leaking into production search), but stage 3 (new vs. existing corpus) becomes a cross-database
  vector comparison, which Postgres doesn't support natively — would need FDW/dblink, or an ETL
  step to copy vectors somewhere queryable, which is extra work for every ingestion run and
  arguably *is* a form of recompute-avoidance failure (copying isn't recomputing, but it's a sync
  burden that duplicates the "don't do double work" concern the proposal is trying to avoid).

Leaning towards same-DB-with-status-flag, but this needs to be a deliberate decision, not a default,
because of point 2.

**2. The existing `flagged` marker cannot be reused for "pending" — it doesn't hide anything.**

Checked `Backend/app/repositories/image_repository.py`: `image_extras.flagged` is a *visible*
admin-selection marker (flagged images still show up in normal paginated browse/search; there's a
separate endpoint to list only the flagged ones). It was deliberately designed this way — see
`docs/superpowers/specs/2026-06-29-rename-excluded-to-flagged-design.md` — as a marker for pending
*bulk operations*, explicitly not a visibility switch.

If pending/pre-ingestion images share the `images` table, they need a genuinely new field that
*is* filtered out by default everywhere images are listed (browse, search, recommendations, stats,
concept tagging, Android client). That's a wide surface area to audit, and a single missed filter
means un-reviewed, potentially-duplicate, potentially-low-quality content leaks into a real user's
search results. This is the highest-risk part of the whole feature and deserves its own careful
pass and probably its own test coverage (e.g. "assert no pending image ever appears in
`/api/images` or `/api/search`").

**3. The existing duplicate-cluster pipeline is not scoped and won't scale to per-ingestion runs.**

`rebuild_duplicates.py` builds `tmp_duplicates` as a full cross join over *every* row in `images`
(already called out in its own comments as expensive) and `rebuild_duplicates` is documented as
non-idempotent (drops and rebuilds the table). Running it unmodified for every ingestion batch
would mean recomputing pairwise distances for the *entire* corpus (tens of thousands of images)
every time a handful of new memes arrive. Stage 2 needs a scoped variant — restricted to the new
batch's images, or new-batch-vs-new-batch only, not new-batch-vs-everything. Stage 3, by contrast,
is naturally cheap if done as an ANN top-k query per new image (like the existing "similar images"
search) rather than a cross join — worth being explicit in the design so it doesn't get
accidentally implemented as a second full cross join.

**4. No ingestion-run/batch concept exists in the schema today.**

`Image` has no status, no batch id, nothing to scope "these are the N images from this ingestion
run" for stage-2 clustering, review-progress tracking, or the promote UI. Will need something like
an `ingestion_batches` table + `images.ingestion_batch_id`.

**5. Filename stability across the move.**

`ImagesRepository.register_image(file)` stores a bare filename, and `extract_text_from_memes`
looks images up by filename to decide whether to (re)process. If registration happens in stage 2
(before promotion) and the file later moves from a pending location to `BASE_PATH`, the filename
must not change — this must be a pure "move the file to a new directory, keep the same name, flip
a status" operation, not a rename, or the incremental skip logic in `extract_text_from_memes`
silently breaks (treats it as a new file, or worse, can't find the row).

**6. Stage 1 doesn't need the DB at all.**

Hash-based coarse dedup on raw incoming files, before anything is registered, is pure filesystem
work. `batch/utils/file_hash.py` (`sha256_file`, `files_are_identical`) is already DB-agnostic and
directly reusable. `detect_file_duplicates.py`'s union-find clustering logic is *conceptually*
reusable but is currently coupled to already-registered DB rows — stage 1 should extract/reuse the
hashing primitives, not the DB-coupled script wholesale.

**7. Adjacent prior art.**

`INCOMING_PATH` / `save_incoming()` (`Backend/app/services/image_store.py`,
`docs/superpowers/specs/upload-endpoint.md`) already established a filesystem-only staging
directory for user-uploaded images, promoted later by a separate batch job. It's simpler than what
this spec needs (no DB rows, no embeddings, no dedup), but confirms the "staging directory outside
`BASE_PATH`, promoted by a batch step" pattern is already an accepted shape in this codebase —
worth aligning naming/conventions with it rather than inventing parallel terminology.

### Open questions not addressed in the proposal

- Is `PATH_INGESTION_SOURCE` per-environment, like `BASE_PATH` (i.e. `.env.<environment>` scoped),
  or one shared inbox later routed to an environment?
- What happens if a human review session (stage 2 or 3) is interrupted mid-batch — is progress
  resumable per image, like `tools/agent_duplicates.py`'s state file?
- Is review UI-driven, agent/skill-driven (a `review-duplicates`-style Claude Code command), or
  both, and does that choice differ between stage 2 and stage 3?
- For a stage-3 match (new image resembles an *existing* corpus image), what are the possible human
  decisions? Discard the new image outright, flag the existing one instead, or something else —
  this isn't symmetric with stage 2 (where both candidates are "new" and interchangeable).
- What is undone if a human incorrectly confirms a duplicate — is there a way to recover a moved
  file, or is `duplicates2`/`duplicates3` effectively final once the batch pipeline runs?

---

## Recommendation

Proceed to a full design draft using the same-DB-with-status-marker approach as the working
assumption for embeddings/dedup reuse, but keep it flagged as an explicit decision point (not a
silent default) so it gets confirmed during brainstorming rather than discovered during
implementation. Document the visibility-audit surface area as its own checklist item, since it's
the part most likely to cause a real production leak if rushed.
