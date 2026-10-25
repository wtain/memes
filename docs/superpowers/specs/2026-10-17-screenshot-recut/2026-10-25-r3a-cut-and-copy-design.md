# R3a: Cut and copy - design

Status: draft
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md (project R3a, split from R3)
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
Inventory of derived data: docs/image-derived-data.md

R3a delivers the job `create_recut_cuts`: for each `detected` proposal from R2 it writes the cut file, creates the cut's `images`
row (status `cut_proposed`), copies the inherited derived data (groups 1 and 3), moves the proposal to `proposed` and records the
`proposed` metric event, all in one transaction per cut. R3b (recompute group 2 with `--status cut_proposed`) and R3c ("refresh
stale" modes for group 3) follow; approve and reject are R5.

## 1. Decisions

| # | Decision | Source |
|---|---|---|
| 1 | The cut keeps the source file format (OQ-2). JPEG at quality 95 keeping the source's subsampling; PNG as PNG | user, 2026-10-25 |
| 2 | The job cuts every `detected` proposal, highest confidence first, with an optional `--limit` | user, 2026-10-25 |
| 3 | One proposal is one transaction (NFR-2); the file is written to a temporary name and renamed | NFR-2 |
| 4 | Group 1 rows are copied unstale, group 3 rows are copied with `stale = true`; group 2 and the "none" tables are not copied | docs/image-derived-data.md |
| 5 | Manual-trigger only, admin-triggerable from `/admin/batches`, not scheduled | NFR-3 |

## 2. The job

`batch/create_recut_cuts.py`, kind `create_recut_cuts`, run-tracked, same shape as `detect_recut_crops` (`tracked_run` or
`finish_existing_run`, `--limit`, stats written with `BatchRunRepository.update_stats`). Stats: `proposals_selected`, `cut`,
`skipped`, `failed`, `proposals_remaining`.

**Selection.** `image_cuts` rows in state `detected`, ordered by confidence descending then `id`. A proposal whose source is no
longer `active`, is flagged, or is in the frozen evaluation corpus is skipped (counted, logged, the row stays `detected`).

**Per proposal, one transaction:**
1. Check the source. The file must exist and its content hash must equal `image_cuts.source_content_hash`. A missing or unreadable
   file, or a changed hash, ends in `failed` (section 5) and no cut is made.
2. Write the cut file: a pixel crop of (`crop_x`, `crop_y`, `crop_w`, `crop_h`) in the source format, into `BASE_PATH` under a new
   UUID plus the source extension, via a temporary name and rename.
3. Insert the `images` row: new `id` and `filename`, status `cut_proposed`, `created_at` inherited from the source,
   `ingestion_batch_id` empty, `width`, `height` and `content_hash` computed from the new file.
4. Copy the derived data (section 3).
5. Set `image_cuts.cut_image_id`, move the state to `proposed`, and record
   `BusinessMetricsService.record("recut", "proposed", detect_run_id, image_cuts.id)` (idempotent per `image_cuts.id`).

If anything fails after the file is written, the transaction rolls back and the job deletes the new file. A crash between the
write and the commit can leave an orphan file in `BASE_PATH`; the filename is logged before writing, and R5's audits list files
without a row. Twenty consecutive failures stop the run, as in R2.

## 3. What is copied

A `CutCopier` (in `repository/image_cuts.py` or a sibling module, never commits) runs one method per family with the cut's new
`images.id` as the target. Each family is one `INSERT ... SELECT`; new primary keys come from `gen_random_uuid()` where a table
has its own, and the description child tables go through a source-to-new id map built by the same statement. Only `ocr_texts`
needs Python arithmetic.

**Group 1, copied as is, `stale = false`:** `description_notes` (including `lemmas_built_at` and `embedding_built_at`),
`description_note_lemmas`, `description_note_embeddings`, `image_extras` (`remarks` only; `flagged` and `flagged_reason` empty),
`tags` with source `Note`, and human-verified `meme_profiles` with their values and situation embedding.

**Group 3, copied, `stale = true`:**
- `ocr_texts`: each polygon is shifted by the crop origin. A row is kept when the polygon's centre lies inside the crop, with its
  points clipped to the crop bounds, and dropped otherwise. Both `bbox` shapes (list of points, `[x, y, w, h]`) are handled.
- `ocr_lemmas`, copied wholesale.
- `image_descriptions`, with their `image_description_embeddings` and `description_lemmas` via the new description ids.
  `image_description_feedback` is not copied (it is lost when the stale description is replaced).
- `tags` with sources `OCR` and `Ollama`.
- Model-made `meme_profiles`, `meme_profile_values` and `meme_profile_situation_embeddings`, copied exactly; their staleness
  comes from the existing fingerprint rule and they have no `stale` column.

**Not copied:** group 2 (R3b), `image_processing_status`, `tmp_duplicates`, `tmp_clusters`, `duplicate_decisions`,
`processing_errors`, `image_metrics`, `meme_text_ratings` (R5 moves it on approve).

**Drift guard.** A test walks every table classified in `docs/image-derived-data.md` and asserts the copier either copies it or
the inventory says it is not copied. A new table without an answer fails the test.

## 4. Settings

`recut.jpeg_quality: 95` in `environments/settings.yaml`. `commit_interval` is not used for commits (each cut commits on its own,
per NFR-2); no other keys.

## 5. Failure handling

A per-proposal failure sets `image_cuts.state = failed` with a short `failure_reason` (`source_missing`, `source_changed`,
`unreadable`, `write_error`) and leaves the source untouched. A `failed` proposal is not retried by default. A database error
aborts that proposal's transaction, deletes the new file, counts as `failed` and feeds the 20-in-a-row stop.

This needs one migration adding the nullable `image_cuts.failure_reason` column (nullable with no default, so no table rewrite,
NFR-4). The `ImageCut` model and the status registry docs are updated with it.

## 6. Testing

- **Unit:** crop pixels for JPEG and PNG; quality 95 with subsampling kept; OCR polygon shift, clip and drop for both `bbox`
  shapes; the atomic file write.
- **Integration (`db_session`):** a full cut creates the file, the `images` row, the link, the `proposed` state and the event;
  group 1 rows copied unstale and group 3 rows copied stale; excluded tables stay empty; a failure mid-copy leaves no row and no
  file; a hash mismatch ends `failed`; `--limit` and highest-confidence-first ordering; a re-run is idempotent.
- **Drift test:** section 3.
- **Docs:** `docs/batches/inventory.md`, the `CLAUDE.md` batch entry, `docs/image-derived-data.md` (copy marked implemented), the
  `batch_registry.yaml` entry after `detect_recut_crops`, and the tracker row.

## 7. Out of scope

R3b and R3c (until they run, a cut has no CLIP embedding and stale group 3 rows); approve and reject transitions and their events
(R5); box adjustment (R6); scheduling (R7).
