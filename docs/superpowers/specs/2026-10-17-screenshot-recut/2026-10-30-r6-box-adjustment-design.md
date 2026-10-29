# R6: Box adjustment - design

Status: draft
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md (project R6)
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
Follows: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-29-r7-operations-and-evaluation-design.md
Amends: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-28-r5a-approve-reject-lifecycle-design.md (a decision now copies the source file to `pre-cuts/`, see section 5)

The detector proposes one rectangle; the labelled sample says about 5% of the proposals are slightly off (5 of 124). R6 lets the
owner correct the rectangle on `/admin/recut` before approving. The cut is rebuilt from the corrected box and the review goes on
with the same proposal. Requirements covered: FR-15.

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | The proposal keeps its `image_cuts` row, id and state `proposed`; the box columns are overwritten | The queue position, the metrics subject and the links stay valid |
| 2 | The first adjustment stores the detector's box in new nullable columns `detected_crop_x/y/w/h`; NULL means never adjusted | "Reset to detected box" and the adjusted statistics need it; metadata only, no data is derived from it |
| 3 | The cut is rebuilt as a NEW `cut_proposed` image, `cut_image_id` is repointed and the old cut image is deleted | The derived data is copied the same way as in R3a (shifted OCR boxes, stale flags), so no second copy path exists |
| 4 | The rebuild runs inside the API request, with Pillow in the backend image | One cut is a single crop; a job would add a state and a wait the owner sees |
| 5 | New metric event `adjusted` on the same subject, not exclusive with `approved` or `rejected` | An adjusted proposal is still approved or rejected later |
| 6 | Reset is the same endpoint with the stored detector box | One code path |
| 7 | Every approve copies the source file to `BASE_PATH/pre-cuts/<same filename>` after the commit | The owner asked for a backup of the original before the source leaves through `move_flagged` |
| 8 | No live crop preview in the browser | It would duplicate the server crop logic; the overlay shows the draft box and the cut pane updates after Apply |

## 1. Data model

One Alembic revision on the head at implementation time adds four nullable integer columns to `image_cuts`:
`detected_crop_x`, `detected_crop_y`, `detected_crop_w`, `detected_crop_h`. `Storage/models.py` gets the matching attributes.
Nothing else changes in the schema; `uq_image_cuts_live_source` and `uq_image_cuts_cut_image_id` keep holding because the row is
updated in place. Rollback drops the four columns.

The first successful adjustment copies the current `crop_x/y/w/h` into the `detected_*` columns when all four are NULL. Later
adjustments never touch them.

## 2. The adjust endpoint

`POST /api/recut/proposals/{id}/adjust`, body `{x, y, w, h}` (new schema `shared/schemas/recutadjustrequest.schema.json`, types
regenerated for TypeScript, Kotlin and Python). It returns the updated `RecutProposalDetail`, which gains `detectedBox`
(`{x, y, w, h}` or `null`).

| Status | When |
|---|---|
| 200 | cut rebuilt, detail returned |
| 404 | unknown proposal id |
| 409 | `already_decided`, `cut_missing`, `source_missing`, `source_not_active`, `source_flagged` (the R5a codes), plus `source_changed` (the source file's hash differs from `image_cuts.source_content_hash`) and `source_unreadable` |
| 422 | non-UUID id; a non-integer or negative value; a side under 16 px; a box equal to the current box; a box that does not fit the source (measured from the source file header with Pillow) |
| 500 | `write_error` (the new cut file could not be written) |

`RecutAdjustService` (new, `Backend/app/services/recut_adjust_service.py`), same shape as `RecutDecisionService`:

1. Lock the `image_cuts` row `FOR UPDATE` (`RecutRepository.lock_proposal`), check every precondition before any write.
2. Crop on a worker thread (`asyncio.to_thread`) with `batch.utils.recut_cut.crop_to_file(src, dst, box, expected_hash=image_cuts.source_content_hash, jpeg_quality=settings.recut.jpeg_quality)`. A `CutError` maps to 409 (`source_missing`, `source_changed`, `source_unreadable`) or 500 (`write_error`).
3. Insert the new `cut_proposed` image (`created_at` from the source, as `create_recut_cuts` does) and copy the derived data with `CutCopier.copy(source_id, new_id, crop)` for the new box.
4. Update the row: box, `detected_*` if first adjustment, `cut_image_id` to the new image, `confidence` untouched. Delete the old cut image (its derived rows cascade).
5. Record the `adjusted` event on the proposal's metric subject.
6. File handling is tied to the transaction with SQLAlchemy session hooks: the old cut file is removed after the commit, the new cut file is removed if the transaction rolls back. Removal failures are logged and swallowed.

The `confidence` stays the detector's value for the original box; the page says so.

## 3. Packaging

- `Pillow` is added to `Backend/requirements-backend.txt` at the version pinned in the root `requirements.txt` (no new transitive dependencies, so the full-closure rule holds).
- `Dockerfile.backend` and `.dockerignore` carry `batch/utils/__init__.py`, `batch/utils/recut_cut.py` and `batch/utils/file_hash.py`. `tests/docker/` proves the image boots and imports them.
- The backend writes into `BASE_PATH` (new cut files, `pre-cuts/`). The runbook states that the mount must be writable.
- No job walks `BASE_PATH` subdirectories, so `pre-cuts/` is invisible to the batch jobs (same as `excluded/` and `converted_originals/`). Purging it is not part of R6.

## 4. Review page

- **Edit mode.** An "Adjust" button turns the source pane's overlay into a draggable rectangle (reusing the R4 `BoxOverlay`): drag the body to move, drag the four corners and four edges to resize; pointer events, so mouse and touch work.
- **Numeric fields** `x`, `y`, `w`, `h` stay in sync with the drag and allow exact and keyboard entry. The draft is clamped inside the source and to at least 16 px per side. A line "Removes N% of the source" updates live.
- **Buttons.** Apply (enabled only for a valid box different from the current one), Cancel (also `Esc`), "Reset to detected box" (shown when `detectedBox` exists and differs from the current box; it loads that box into the draft and Apply sends it).
- **While editing**, Approve, Reject, the `A`/`R` keys and the arrow keys are off.
- **After Apply** the page swaps in the returned detail, so the cut pane loads the new image, and shows: "Adjusted. Derived data (OCR, descriptions, embeddings) is stale until `recut_pipeline` has run; run it before approving." The info block shows an "Adjusted" mark and the detector's box.
- **Errors.** 409 reuses the R5a flow (a sentence per code, then the queue reloads). 422 shows "That box does not fit this image". Anything else shows "Could not adjust the box" and keeps the draft editable.
- Pure helpers for move, resize and clamp live in their own module and are unit-tested with exact numbers.

## 5. Metrics and pre-cuts

- `adjusted` is added to the `recut` process in `BusinessMetricsService.PROCESSES`, outside the exclusive approved/rejected group, with a metric `adjusted_share = adjusted / proposed`, shown in the `/admin/batches` panel.
- Each band of `GET /api/recut/stats` gains `adjusted`, counted from the stored detector box (so it needs no events); the totals row and the stats table get an "Adjusted" column.
- **Pre-cuts.** `RecutDecisionService.approve` copies the source file to `BASE_PATH/pre-cuts/<source filename>` after the commit, with `shutil.copy2`, never overwriting an existing file. A failure is logged and swallowed (the approval stands). Reject and unreviewed proposals copy nothing. This replaces the statements "a decision moves no file" in the R5a spec, `backend_api.md` and the runbook with "a decision moves no file; an approval also copies the source to `pre-cuts/`".

## Docs touched

`backend_api.md` (adjust endpoint, `detectedBox`, `adjusted` in stats, pre-cuts note), `docs/batches/use-cases/screenshot-recut.md` (adjusting, `pre-cuts/`, writable `BASE_PATH`, refresh before approving, the real-browser check), `docs/image-derived-data.md` (an adjusted cut is a fresh copy), the R5a spec (amendment note), `CLAUDE.md` (backend image carries Pillow and three `batch/utils` files), this epic's tracker.

## Testing

- **Integration** (temporary `BASE_PATH` with small real JPEG and PNG files): happy path (new image, copied data with shifted OCR boxes, old image gone, file dimensions and format right); second adjustment keeps the first detector box; reset; every 409 code and each 422 rule; old file removed after commit and new file removed after rollback; the event; pre-cuts on approve (copy exists, never overwritten, copy failure swallowed, reject copies nothing); migration columns exist and are nullable.
- **Endpoint tests** with a mocked service: status and code mapping.
- **Docker smoke** (`tests/docker/`): the image boots with Pillow and the copied files.
- **Frontend:** helper unit tests; the editor under simulated pointer events; page tests for edit mode, disabled decisions, Apply, Reset, Cancel, `Esc`, errors and the stale-data note.
- **Verification:** each test root separately, plus the type-generation diff check.

## Known limits

- The drag handles, their alignment on a tall source image and touch behaviour cannot be tested under jsdom; they are a manual check listed in the runbook's first-rollout steps.
- An approve before the refresh has run still leaves stale derived data (the open R3c decision to widen refresh to `active` cuts is not made here); the page warns after an adjustment.
- Migration `d7a3b9e2c451` and its predecessors, plus R6's revision, have not run against a live database; rollout needs the owner's go-ahead.
