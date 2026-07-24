# Ingestion Pipeline Design (Draft)

**Date:** 2026-07-24
**Status:** Draft for brainstorming — contains open decisions and ambiguities, not an approved
implementation plan. Builds on `2026-07-24-ingestion-pipeline-pre-spec.md`.

---

## Goal

Give each environment (metal / general / it) a repeatable way to bring a batch of new images from
an external drop location into its active library, filtering out exact and near-duplicates —
against both the incoming batch itself and the existing corpus — before the expensive per-image
enrichment pipeline (OCR, tags, descriptions) ever runs on them, and without recomputing CLIP
embeddings that were already computed during duplicate review.

## Non-goals (for this draft)

- Cross-environment ingestion (one drop location feeding multiple environments, or moving images
  between environments) — out of scope; one `PATH_INGESTION_SOURCE` maps to one environment.
- Automatic (non-human/agent-reviewed) duplicate resolution — every near-dup decision in stages 2
  and 3 requires explicit confirmation, same as the existing `review-duplicates` flow.
- Re-architecting the existing active-library duplicate review (`ExploreDuplicatesPage`,
  `rebuild_duplicates.py`) — this design scopes/reuses it, doesn't replace it.

## Terminology

| Term | Meaning |
|---|---|
| **Pending image** | An image registered in the DB but not yet visible in normal browse/search — mid-ingestion. |
| **Active image** | An image visible in normal browse/search — today's only state; the implicit default. |
| **Ingestion batch** | One run of the pipeline over one drop of files in `PATH_INGESTION_SOURCE`; a unit of scoping for stage-2 dedup and review progress. |
| **Promotion** | Moving a pending image's file into `BASE_PATH` and flipping its status to active. |

---

## Architecture decision (working assumption — see Open Questions)

**Pending images are registered in the same `images`/`embeddings` tables as the target
environment's active library**, distinguished by a new `images.status` column
(`pending` / `active`, default `active` for all pre-existing rows via migration default).

This is chosen over a separate staging table/DB because:

- Embeddings computed in stage 2 are FK'd to `images.id` and never need to move or be recomputed —
  promotion is `UPDATE images SET status = 'active' WHERE id = ...` plus a filesystem move.
- Stage 3 (new vs. existing corpus) can reuse the existing cosine-distance/HNSW similarity query
  as-is, just adding `status = 'active'` to the candidate-corpus side — no cross-database vector
  work.
- The existing dup-review stack (schema, repository query shapes, `tools/agent_duplicates.py`
  pattern) is reusable with scoping rather than forked.

**Cost of this choice:** every read path that lists or searches images must filter to
`status = 'active'` by default, or pending images leak into production results. This needs to be
enumerated exhaustively (see Visibility Audit below) and covered by a test that asserts no pending
image is ever returned by a production-facing endpoint.

---

## Data model changes

### `images` table

- Add `status` column: `VARCHAR` or Postgres enum, values `pending` | `active`. Default `active`.
  (Considered `ingestion_batch_id IS NULL` as an implicit "active" signal instead of an explicit
  status column — rejected because it conflates "not part of a tracked batch" with "visible",
  which breaks the moment we want to keep ingestion-batch history around after promotion for
  audit/debugging.)
- Add `ingestion_batch_id` (nullable FK → `ingestion_batches.id`). Null for images that predate
  this feature or were registered outside the ingestion pipeline.

### New table: `ingestion_batches`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `environment` | string | metal / general / it — redundant with the DB itself but useful if batches are ever inspected cross-environment |
| `source_path` | string | `PATH_INGESTION_SOURCE` value at time of run, for audit |
| `created_at` | timestamp | |
| `stage` | enum | `hash_dedup` / `in_batch_review` / `cross_corpus_review` / `promoted` / `aborted` — coarse progress marker for resumability |
| `stats` | JSON | counts per stage: intake, hash-duplicates removed, in-batch duplicates removed, cross-corpus duplicates removed, promoted |

Whether per-image review progress (stage 2/3 resumability) is tracked on `ingestion_batches.stats`
or via a state file like `tools/agent_duplicates.py`'s `.agent_state/` pattern is an open question
below.

### `image_extras.flagged`

**Not reused for pending/visibility.** Its existing semantics (visible admin marker for pending
bulk ops) stay as-is. A future "flag this pending image during review" affordance, if wanted, would
need its own field — not folded into this one, to avoid re-litigating the June 2026 rename.

---

## Visibility audit (must filter `status = 'active'`)

Every one of these currently has no status filter and must gain one:

- `Backend/app/repositories/image_repository.py`: paginated browse, search, `get_flagged`,
  `get_duplicates_clustered`, similarity/recommendation queries — every `select(Image...)`.
- `Backend/app/repositories/diagnostics_repository.py`: stats/counts (`/api/diagnostics/health`,
  totals) — pending images should not inflate corpus-size stats shown to users.
- `repository/images.py` global repo, where used outside the ingestion pipeline itself.
- Batch jobs that iterate "all images" for enrichment (`build_tags_from_ocr`,
  `build_image_descriptions`, `build_tags_from_descriptions`, `build_concept_embeddings`,
  `build_bow`) — pending images must **not** be picked up by these before promotion, since stage 2
  intentionally runs before OCR/tags exist.
- Android client — no separate filtering needed if the backend never returns pending images, but
  worth a note in `backend_api.md` that "all image-returning endpoints implicitly exclude pending
  images" is now a documented contract, not an accident.

`build_image_embeddings.py` is the one deliberate **exception** — the ingestion pipeline needs to
call embedding computation for pending images specifically (stage 2), so this script needs a mode
(or the ingestion pipeline needs a dedicated call path) that targets pending images rather than
excluding them. See Stage 2 below.

---

## Stage-by-stage design

### Stage 0 — Intake

- `PATH_INGESTION_SOURCE` is a new per-environment secret in `.env.<environment>`, analogous to
  `BASE_PATH`. One source directory per environment (see Non-goals).
- No DB interaction. Files just sit on disk.

### Stage 1 — Coarse filter (file hash dedup)

- Pure filesystem operation, no DB, no registration.
- Reuse `batch/utils/file_hash.py` (`sha256_file`, `files_are_identical`) directly.
- New script (working name `batch/ingest_hash_dedup.py`): hash every file in
  `PATH_INGESTION_SOURCE`, union-find on identical hashes (mirroring `detect_file_duplicates.py`'s
  approach but without any DB read/write), keep one per hash cluster, move the rest to
  `PATH_INGESTION_SOURCE/duplicates/`.
- Tie-break for "which one to keep" among byte-identical files: doesn't matter which (they're
  identical), so filename sort is fine — unlike `detect_file_duplicates.py`'s "keep oldest
  `created_at`" rule, there's no DB row yet to have a `created_at`. Use filesystem mtime or just
  lexicographic filename order.

### Stage 2 — In-batch near-duplicate review

1. **Register** surviving files as `Image` rows with `status = 'pending'` and
   `ingestion_batch_id` set to the current batch. Filenames must be preserved verbatim (see
   pre-spec point 5) — no UUID-renaming at this stage (contrast with `save_incoming()`, which does
   rename; that's fine for the upload endpoint's crash-safety goals but would break the
   "filename is the join key" assumption `extract_text_from_memes` relies on later).
   - File stays physically in `PATH_INGESTION_SOURCE` at this point — it does **not** need to be
     under `BASE_PATH` yet, since nothing that reads pending images (embedding computation, the
     review UI) needs to resolve paths via `BASE_PATH`. Needs a path-resolution strategy that
     isn't just "`BASE_PATH` + filename" (see Open Questions).
2. **Compute embeddings** for this batch's pending images only. `build_image_embeddings.py`
   already supports `--incremental` (images with no embedding yet); extend it (or add a sibling
   entry point) to accept an explicit image-id/batch scope so a stage-2 run doesn't accidentally
   sweep in every pending image from unrelated concurrent batches, and reads file paths from
   `PATH_INGESTION_SOURCE` rather than `BASE_PATH` for pending rows.
3. **Cluster within the batch only.** A scoped variant of `rebuild_duplicates.py` +
   `clusterize.py`: cross join restricted to `WHERE ingestion_batch_id = :batch_id` on both sides,
   not the full `images` table. Output into the existing `tmp_duplicates`/`tmp_clusters` shape (or
   a parallel batch-scoped table, if reusing the global tmp tables risks colliding with a
   concurrently-running active-library `rebuild_duplicates` run — see Open Questions) so the
   existing repository query shape (`get_duplicates_clustered`) can be reused with a batch filter
   added.
4. **Review.** Reuse the `review-duplicates` skill / `tools/agent_duplicates.py` pattern, scoped to
   `ingestion_batch_id = :batch_id`, and/or a frontend page reusing `ExploreDuplicatesPage.tsx` /
   `MemesList` with a `status=pending&batch=...` filter instead of the current unscoped
   `listDuplicates` prop. Both surfaces are candidates; see Open Questions on whether both are
   needed for v1.
5. **Apply decisions.** Confirmed duplicates: move file from `PATH_INGESTION_SOURCE` to
   `PATH_INGESTION_SOURCE/duplicates2/` and delete (or mark deleted-state on) their `Image` row —
   needs a decision on whether rejected pending images keep a DB row (audit trail) or are hard
   -deleted (simpler, avoids ever having to filter them out later too). Leaning delete, since
   they never had OCR/tags/descriptions to lose and the `duplicates2/` directory is itself the
   audit trail.

### Stage 3 — Cross-corpus near-duplicate review

1. For each surviving pending image, run the existing similarity search
   (`embedding.cosine_distance(...)` ANN query) against `status = 'active'` images only.
2. Present matches above a similarity threshold for review — reusing the same review surface as
   stage 2, but the decision shape is different (see pre-spec point on stage-3 asymmetry): the two
   candidates are not interchangeable — one is new, one is already-published. Needs an explicit
   decision UX for "discard the new one" vs. "this isn't actually a duplicate, keep both" (there's
   no realistic "replace the existing active one" case in scope here — that would be a separate,
   riskier operation).
3. Confirmed duplicates: move to `PATH_INGESTION_SOURCE/duplicates3/`, same row-deletion question
   as stage 2.

### Stage 4 — Promotion

1. For each image still `status = 'pending'` in the batch: move its file from
   `PATH_INGESTION_SOURCE` to `BASE_PATH` (same filename), then `UPDATE images SET status =
   'active'`.
2. Run `extract_text_from_memes` in its normal incremental mode against `BASE_PATH` — since these
   images are already registered (from stage 2) with a filename `extract_text_from_memes` will
   find via `find_image_by_filename`, it will skip re-registration and go straight to OCR, which
   is exactly the desired incremental behavior. No changes needed to this script itself, *provided*
   the visibility audit is done — it currently has no status filter and doesn't need one added
   (it operates on `os.listdir(BASE_PATH)`, which pending images aren't in until this exact step).
3. Continue the rest of the standard pipeline order from CLAUDE.md
   (`build_tags_from_ocr` → `build_ocr_lemmas` → `build_image_descriptions` → ...) unmodified.
4. Mark `ingestion_batches.stage = 'promoted'`.

---

## Batch / tooling changes summary

| Component | Change |
|---|---|
| `batch/ingest_hash_dedup.py` | **New.** Stage 1, filesystem-only. |
| `batch/build_image_embeddings.py` | **Extend.** Scope to pending images in a given batch, read from `PATH_INGESTION_SOURCE`. |
| `batch/rebuild_duplicates.py` / `clusterize.py` | **Extend or fork.** Batch-scoped cross join instead of whole-corpus. |
| `tools/agent_duplicates.py` | **Extend.** Accept `--batch_id`, filter cluster query accordingly. |
| `.claude/commands/review-duplicates.md` | **Extend or new sibling command.** Ingestion-scoped review entry point. |
| `batch/ingest_promote.py` | **New.** Stage 4 file-move + status flip, then hands off to `extract_text_from_memes`. |
| `extract_text_from_memes.py` | **No change** — works as-is once files are physically in `BASE_PATH`. |
| Rest of pipeline (tags, lemmas, descriptions, concepts) | **No change.** |

## Backend / API changes

- New endpoints (exact shape TBD, follow Router → Service → Repository per CLAUDE.md):
  - List pending images for a batch (`status=pending&ingestion_batch_id=...`).
  - Batch-scoped duplicate clusters (extends `/api/images/duplicates` with a batch filter, or a
    new `/api/ingestion/batches/{id}/duplicates`).
  - Confirm/reject a pending duplicate decision.
  - Trigger/inspect batch stage progress (`/api/ingestion/batches/{id}`).
- `backend_api.md` must be updated per CLAUDE.md's API contract requirement.
- Every existing endpoint touched by the visibility audit needs its query updated, not its
  contract — response shape is unchanged, just row filtering.

## Frontend changes

- New route/page for browsing a pending batch — likely reuses `MemesList` with new
  `status`/`batch` filter props rather than a bespoke component, per the existing
  `ExploreDuplicatesPage` pattern of thin pages wrapping a shared list component.
- A way to kick off / monitor batch stages — could be CLI-only for v1 (agent/operator runs the
  batch scripts by hand) with UI added later, or UI-first — open question below.

---

## Open questions / ambiguities

These need resolving (via brainstorming) before this becomes an implementation plan.

1. **Same-DB-with-status vs. separate staging store** — this draft assumes same-DB. Confirm.
2. **Path resolution for pending images.** Stage 2/3 need to read pending image bytes (for
   embedding, for thumbnails in the review UI) while the file still lives in
   `PATH_INGESTION_SOURCE`, not `BASE_PATH`. `Backend/app/services/image_store.py`'s
   `get_image_path()` is hardcoded to `_IMAGES_DIR` (`BASE_PATH`). Does the review UI need a new
   "serve from ingestion source" path, or does stage 2 physically copy (not move) files into a
   `BASE_PATH/_pending/` subdirectory up front so all existing file-serving code keeps working
   unmodified, and stage 4 becomes a rename within `BASE_PATH` instead of a cross-directory move?
   This changes the answer to point 5 in the pre-spec (filename stability) very little, but changes
   a lot about how much file-serving code needs touching.
3. **Do batch-scoped `tmp_duplicates`/`tmp_clusters` reuse the global tables (row-level filtered)
   or need parallel tables?** Reusing the global tables risks a concurrently-running full
   `rebuild_duplicates` (active-library maintenance) truncating/rebuilding out from under an
   in-progress ingestion review, since `rebuild_duplicates` currently does a blanket
   `DROP TABLE IF EXISTS` / recreate. If ingestion review and active-library dedup maintenance can
   ever run concurrently, they need separate tables.
4. **Rejected-image row lifecycle** — hard delete vs. soft delete/audit trail for images excluded
   at stages 2/3.
5. **Review surface: UI, agent/skill, or both for v1?** The existing `review-duplicates` skill is
   agent-driven and stateful via a file; `ExploreDuplicatesPage` is human/UI-driven. Building both
   scoped variants doubles the work; picking one for v1 needs a call on who actually reviews
   ingestion batches day to day.
6. **Stage 3 decision UX** — for an asymmetric match (new vs. active), what are the actual allowed
   outcomes, and does a human/agent need to see the *active* image's context (tags, description) to
   decide, which the pending image won't have yet?
7. **Resumability** — if a batch is interrupted mid-review, does `ingestion_batches.stage` plus
   `images.status = 'pending'` rows fully describe resumable state, or is a per-image review
   progress marker (like `tools/agent_duplicates.py`'s state file) still needed within a stage?
8. **`PATH_INGESTION_SOURCE` scoping** — confirmed per-environment per the proposal; does the
   ingestion pipeline need to guard against two batches being ingested concurrently into the same
   environment (e.g. a lock or a "one active batch per environment" constraint)?
9. **Threshold tuning** — stage 2 reuses `PROXIMITY_THRESHOLD = 0.05` from `clusterize.py`; stage 3
   needs its own threshold for "new vs. active" similarity, which may reasonably differ (looser or
   tighter) from the in-batch one. Needs empirical tuning, not just reuse of the existing constant.
10. **Failure mid-embedding-computation** — if `build_image_embeddings` for a pending batch dies
    partway (as it can today — see the `except Exception` swallow-and-skip in the existing script),
    does stage 2 review proceed with partial embeddings (some pending images with no embedding
    yet), or block until the whole batch has embeddings?

---

## Risks

- **Visibility leak** (see Architecture decision) — highest risk, needs explicit test coverage,
  not just code review.
- **Cross join blast radius** — if the batch-scoped duplicate query is implemented as a filtered
  version of the existing unscoped query rather than a genuinely restricted join, a large batch
  (thousands of images) still produces an O(n²) row explosion within the batch; likely fine at
  typical batch sizes but worth sizing before committing to the approach.
- **Two review surfaces drifting** — if both a UI and an agent/skill path are built for the same
  review step, decision logic (thresholds, tie-breakers) needs to live in one place both surfaces
  call, not be duplicated per surface.

## Out of scope (explicitly, for this draft)

- Automated re-scoring or ML-based duplicate resolution beyond the existing OCR-text +
  embedding-distance + description heuristic already used by `review-duplicates`.
- Multi-environment or cross-environment ingestion routing.
- UI/tooling for un-doing a promotion once the full enrichment pipeline has run on an image.

## Next steps

Brainstorm the open questions above, in particular #1–#3 (they gate almost everything else), then
turn this into a phased implementation plan — likely: Phase A (stage 1, filesystem-only, lowest
risk) → Phase B (schema + visibility audit, no new UI yet) → Phase C (stage 2 scoped dedup + review
reuse) → Phase D (stage 3 cross-corpus review) → Phase E (promotion + UI polish), given the
explicit sizing/risk concerns already called out above.
