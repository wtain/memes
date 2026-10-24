# R2: Crop detection - design

Status: planned
Plan: docs/superpowers/plans/2026-10-24-r2-crop-detection.md
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md (project R2)
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
Spike findings: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-24-r2-spike-findings.md

R2 delivers the production detection job `detect_recut_crops`: it finds active images with a flat margin or frame around the
meme, and stores one `detected` proposal per image in `image_cuts`. It creates no files, changes no image status and records no
metric event; R3 cuts and derives, and records `proposed`. The approach was chosen by the spike (two labelled rounds, 197 owner
judgments).

## 1. Decisions

| # | Decision | Source |
|---|---|---|
| 1 | Detector `margin_trim`, version `1`: peel flat bands from the four edges. No inner-rectangle detection, no vision model, no YOLOv8 | spike: margin 46 of 48 graded "use as is", inner 2 of 14, vision box 11 of 40 |
| 2 | A proposal removes between 7% and 75% of the image area; trims under 7% are not proposed | spike (round 2 floor); user, 2026-10-24 |
| 3 | A proposal that would clip an OCR text region is dropped (guard); kept only if the calibration (section 5) shows it separates bad from good cuts | spike: the only bad margin cuts clipped text or a picture edge |
| 4 | Checked images are remembered in `image_processing_status` under a pipeline name carrying the detector version | this design |
| 5 | Admin-triggerable from `/admin/batches`, manual-trigger only, not scheduled | NFR-3; scheduling is R7's decision |

## 2. Behaviour

**Candidates** (`repository/image_cuts.py`, `select_candidates(limit)`): images with `status = 'active'`, known `width` and
`height`, that

- are not in the frozen evaluation corpus (`evaluation/gold/corpus.json`, FR-17). The ids are for `general`; on metal and IT the
  exclusion matches nothing, which is harmless;
- have no live `image_cuts` row as source (R1's unique live rule);
- are not themselves a cut (`image_cuts.cut_image_id`, FR-3);
- have no `rejected` `image_cuts` row as source with the same `detector_version` (FR-3);
- have no `image_processing_status` row for pipeline `recut_detect_v1` (decision 4).

**Detection** (`batch/utils/crop_detection.py`, pure, Pillow and numpy only): grayscale the image, downscale to at most 600 px on
the long side, then peel rows and columns from each edge while their standard deviation is below `flat_tolerance`. The box is the
remainder, scaled back to source pixels. It is proposed only when the removed share of the area is in
[`min_removed_share`, `max_removed_share`] and the image is at least 120 px on both sides. Confidence is
`0.6 + 0.4 * min(1, removed_share / 0.3)`, so it ranks proposals (R4 orders its queue by it); it is not a probability.

**OCR guard** (`clips_ocr(box, regions, max_overlap)`): regions come from `ocr_texts.bbox` at or above `settings.OCR.CONFIDENCE_MIN`.
The proposal is dropped when, for any region, more than `ocr_overlap_max` of the region's area lies outside the box.

**Per image outcome**, each recorded in `image_processing_status` (`pipeline = recut_detect_v1`):

| Outcome | `image_cuts` row | processing status | stat |
|---|---|---|---|
| proposal | `detected`, box, `detector = margin_trim`, `detector_version = 1`, confidence, `detect_run_id`, `source_content_hash` | `done` | `proposed` |
| no margin found, or outside the 7% to 75% range | none | `done` | `no_cut` |
| dropped by the guard | none | `done` | `clipped_text` |
| file missing or unreadable | none | `failed`, with `error_message` | `unreadable` |

Bumping `DETECTOR_VERSION` (a constant in code, not a setting) changes the pipeline name, so every image is checked again.
A row for the old version is never touched. An image whose live row exists is skipped, so two runs cannot create two live cuts.

## 3. Job

`batch/detect_recut_crops.py`, shaped like `batch/classify_text_heavy.py`: `main(trigger="manual", run_id=None)`,
`tracked_run(kind="detect_recut_crops")` or `finish_existing_run(run_id)` for the admin trigger. Options: `--env`, `--limit N`.
Commits every `commit_interval` images, so an interrupted run keeps its work. Run stats: `images_selected`, `proposed`, `no_cut`,
`clipped_text`, `unreadable` (they sum to the images processed) and `images_remaining` (images selected but not reached because of
`--limit`). An unreadable file is counted and never fails the run. Registered in the batch registry as kind `detect_recut_crops`,
admin-triggerable, with the three environment overlays; listed in `docs/batches/inventory.md` and in `CLAUDE.md`'s batch list.

## 4. Settings

Group `recut` in `environments/settings.yaml`, every key required (so the overlays are overrides only):
`min_removed_share: 0.07`, `max_removed_share: 0.75`, `flat_tolerance: 7.0`, `ocr_overlap_max: 0.15` (final value from
section 5), `commit_interval: 200`.

## 5. Guard calibration (a plan task, outside CI)

A one-off script reads, with the read-only role, the OCR regions of the images in `evaluation/gold/recut/` and computes the
guard's overlap for each judged box. Decision rule: the guard stays if, at some `ocr_overlap_max`, it drops at least half of the
judged 1s and 0s of the margin kind and at most 5% of the judged 2s; the value chosen is the largest that meets this. Otherwise
the guard is left out of the job and the spec is amended. The numbers go into this spec's addendum. The images are
database-local, so this cannot run in CI.

## 6. Tests

Written first, each watched failing.

- Detector (unit, synthetic images): a bordered image gives the right box; a plain image, a trim under 7%, a trim over 75% and an
  image under 120 px give none; confidence ranks a larger trim higher.
- Guard (unit): a region inside, one clipped, one exactly at the limit.
- Candidates (integration, `ocrdb_test`): one case per exclusion in section 2, plus a positive case.
- Job (integration, temp folder of generated images): rows, stats summing to the images processed, the `done` and `failed`
  processing rows, an idempotent second run, `--limit` with `images_remaining`, an unreadable file, a version bump rescanning.
- Fixtures: the detector reproduces the spike's box (within 2 px) on a few generated images built from graded-2 geometry.
- Settings: a missing `recut` key is a startup error (like the other groups).

## 7. Out of scope

Cut files, the `cut_proposed` status and recording `proposed` (R3); the review page and queue ordering (R4); approve and reject
(R5); box adjustment (R6); scheduling and live precision monitoring (R7); any other detector.

## 8. Rollout and acceptance

No new migration (`image_cuts` is R1's, `image_processing_status` exists). R1's and M1's migrations are not yet applied to the
live databases, so the job cannot run live until they are; that rollout stays an explicit go-ahead with a confirmed backup, one
environment at a time. The first live run is `general` with `--limit 500`, checked against the spike's 27% proposal share and the
guard's drop share; the rows wait in `detected`, nothing is shown or changed.

Acceptance: every exclusion has a test that failed first; a re-run adds no rows; stats add up; the guard decision is recorded
(kept with a value, or removed); the inventory, `CLAUDE.md`, the registry and the tracker row are updated; the throwaway
`batch/tools/recut_spike/` is deleted and `evaluation/gold/recut/` stays.
