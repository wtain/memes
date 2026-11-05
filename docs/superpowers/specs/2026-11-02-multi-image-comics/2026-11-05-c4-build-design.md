# C4: Build

Status: draft
Originates from: docs/superpowers/specs/2026-11-02-multi-image-comics/README.md (tracker project C4)
Follow-ups: none yet

## Intent

Turn a draft comics into one image in the library. The owner presses Build on the editor page (C3), says which panel images to keep,
and confirms. The tiled image (C2) becomes a new active meme, the retired panels leave the library through the existing flag and
move flow, and the built meme inherits their popularity. The build cannot be undone.

## Decisions by the owner (brainstorming, 2026-11-05)

- **Straight into the library.** The build writes the file and registers the image as `active` in the same request. It does not go
  through ingestion. Its derived data (text, embedding, tags, description) is filled by the existing incremental jobs afterwards.
- **The build runs inside the request** (like recut box adjustment), in one database transaction. A failure rolls everything back.
- **Sources move at the next move flagged run.** Unkept panels are flagged and disappear from the library views at once; their
  files move to `comics-sources/<comics name>/` when the owner next runs move flagged, whose chain then deletes their rows.
- **Optional keep.** The Build dialog lists every panel with a "keep this image in the library" checkbox, all unchecked. A kept
  image stays active, is not flagged or moved, passes no popularity to the built image, and after the build is released from the
  comics (the comics keeps its provenance row; the image is free to join another comics).
- Earlier rulings still hold: build needs at least two panels; the built image inherits the popularity of the retired panels
  through the absorption mechanism (source `comics_build`), like a duplicate keeper; no new image status.

## Decisions I made (my calls, to check)

- **Confirmation.** The dialog shows the preview, the keep checkboxes, and the sentence "This cannot be undone." with a "Build
  comics" button; no typed confirmation.
- **Built file name.** `comics-<first 12 hex of the sha256 of the file>.<ext>`, written to `BASE_PATH`. The extension follows the C2
  output (jpg or png).
- **`kept` is recorded.** New column `comics_panels.kept BOOLEAN NOT NULL DEFAULT false`, added by a second migration chained after
  the current single alembic head, which is `d7a3b9c5e214` today (nothing has been applied to a live database, so the two ship
  together at rollout; the builder confirms the head with `alembic heads` and picks the new revision id). On build, kept panels get
  `kept = true` and `image_id = NULL`; unkept panels keep `image_id` until their row is deleted by unregister.
- **Not carried over.** Description notes, saves, ratings and history on retired panels are deleted with the rows, as for
  duplicates. The built image starts with none.
- **Re-check at build time.** The checks done when a panel was added can be stale. For every panel, the build refuses if the image
  is no longer active, is flagged, or has a live recut cut (the same checks as `addability`), if its file is missing, or if the
  sha256 of its file differs from `images.content_hash`; each refusal names the panel (1-based). This also settles tracker D7: a
  recut that touched a panel after it was added is caught here, and no guard is added to the recut code.
- **Same content as an existing image.** If an image with the built file's `content_hash` already exists, the build refuses
  (`same_as_existing`, with that image's id) and removes the file.
- **A comics source flag cannot be changed.** `unmark_flagged` on an image flagged with reason `comics_source` answers 409
  `comics_source_flag` (the popularity already moved to the built image and the panel row is retired), and `mark_flagged` with a
  different reason on such an image is refused the same way, so the move routing cannot be lost to an overwritten reason. The
  service reads the reason before it calls `set_flagged` (new `ImageRepository.get_flag_reason(image_id)`). The builder lists every
  caller of `set_flagged(..., False)` and guards the API paths; batch paths that clear flags are checked and listed in the report.
- **Built view.** A retired panel is shown by its `source_filename`; a panel whose image is flagged but not yet deleted (before the
  next move flagged run) is still retired, not kept.
- **Discovery report (tracker D8).** `repository/discovery_report.py` `DEFAULT_SOURCES` excludes `comics_build`, so a build is not
  counted as a meme that keeps coming back.
- **No `comics_pipeline` driver in C4.** A one-click job that chains the incremental steps is C5. C4 documents which existing jobs
  to run after a build.

## The build, step by step

Service `Backend/app/services/comics_build_service.py`, `ComicsBuildService(session).build(comics_id, keep_panel_ids)`, called by the
endpoint. Inside the request's transaction:

1. `ComicsRepository.get(comics_id, lock=True)` (row lock first). Refuse `built` if not a draft, `keep_invalid` (422) if a kept id is
   not one of its panels.
2. Validate: at least two panels (`not_buildable: too_few_panels`); every panel has an image; for each panel the re-check above.
3. Compose with `compose_to_file(paths, dst, jpeg_quality)` in a worker thread (semaphore of 1, the build is rare and heavy) into
   a **unique temporary name** in `BASE_PATH` (`.comics-build-<uuid4>.<ext>`; `compose_to_file` adds its own `.tmp` beside it).
   Hash the temporary file, then decide: if an `images` row with that `content_hash` already exists, or an `images` row with the
   final name `comics-<hash12>.<ext>` exists, refuse `same_as_existing` (with that image's id) and delete only the temporary file.
   Otherwise `os.replace` the temporary file onto the final name; a stray file of that name with no row is an orphan of an earlier
   crashed build with identical content and is replaced. Register a `file_effects` rollback action (pattern of `recut_files.py`) that
   removes the final file if the transaction does not commit; the file of an existing image is therefore never touched.
   `ComposeError` becomes 422 `compose_failed` with the 1-based position, as for the preview.
4. Insert the `images` row: `status active`, `filename`, `content_hash` (the sha256 of the composed file, which is also the
   `built_content_hash`), `width`, `height` from the composed file, `created_at` now, `popularity` default; no metrics event and no
   processing-status row (the incremental jobs treat the new image as unprocessed by itself).
5. `ComicsRepository.mark_built(comics_id, built_image_id, built_content_hash)` (it re-checks the panel count and that every panel
   still has an image, so it must run before kept panels are released).
6. For every unkept panel: `ImageRepository.set_flagged(image_id, True, "comics_source")`, then
   `PopularityRepository.absorb(panel_image_id, built_image_id, "comics_build")`. A popularity failure aborts the whole build (the
   build is the owner's explicit act and a half-credited build is worse than a refusal), unlike flows that tolerate it: do not wrap
   the call in `guarded`. `AlreadyDiscardedError` (the panel already has an open edge into another tree) becomes `not_buildable`
   with `reason: panel_unavailable`, `cause: already_discarded` and the panel position; `WinnerGoneError`, `CycleError` and
   `PopularityConflictError` become 409 `popularity_conflict`. Nothing is committed in either case. The built image's popularity is
   1 plus the tree weights of the absorbed panels (a panel that itself absorbed copies brings them along), not 1 plus the number
   of panels.
7. For every kept panel: set `kept = true`, `image_id = NULL`.
8. Return the detail of the built comics and the built image id. The transaction commits at the end of the request.

Failure modes: any exception before commit rolls back all rows and removes the built file. A crash after commit but before the
response leaves a complete, consistent build. A crash between the file write and the commit leaves an orphan
`comics-<hash12>` file (no row); it is harmless and a rebuild of the same comics reproduces and reuses the same name. Source files are
never touched by the request, so there is nothing to undo on their side (tracker D6).

## Moving the sources (changes to move flagged)

`batch/move_flagged.py` today moves every flagged image to `BASE_PATH/excluded`. Change: an image whose
`image_extras.flagged_reason` is `comics_source` and that is a panel of a built comics (`comics_panels.image_id`) goes to
`BASE_PATH/comics-sources/<comics name>/` instead (created on demand, `move_without_overwrite`, same counters plus a
`moved_to_comics_sources` metric). The query now selects the image id, filename, reason and, through a left join of
`comics_panels` and `comics`, the comics name. The directory name is the comics name, already safe on Windows (C1 name rules:
no reserved device names, no trailing dot or space). A flagged `comics_source` image with no panel row or no comics row falls
back to `excluded`. The chain (`unregister_deleted_images`, then `remove_singletons`)
is unchanged; deleting the image row sets `comics_panels.image_id` to NULL, and the provenance columns remain.

## API

`POST /api/comics/{id}/build` with body `{keepPanelIds: string[]}` (default empty). 200 with `ComicsBuildResult {comics:
ComicsDetail, builtImageId}`. Errors (same `detail: {code, ...}` shape as C3): `not_found` 404; `built`, `not_buildable`
(`reason`: `too_few_panels`, `panel_missing`, `image_not_active`, `image_already_built`, `same_as_existing` with `imageId`, and
`panel_unavailable` with `position` (1-based) and `cause` (`not_active`, `flagged`, `live_cut`, `file_missing`, `file_changed`,
`already_discarded`)) 409; `popularity_conflict` 409; `keep_invalid` 422; `compose_failed` 422 as for the preview. New shared schemas `comicsbuildrequest`, `comicsbuildresult`; the
`ComicsPanel` schema gains `kept` and `sourceImageId` (so the built view can link a kept panel); types regenerated; `backend_api.md`.

## UI

- The Build button on the editor (disabled in C3) opens the **Build dialog**: preview, one checkbox per panel (thumbnail, number,
  "keep in library"), the irreversible sentence, Build and Cancel. On success the page navigates to the built meme
  (`/memes/{builtImageId}`); errors are shown in words in the dialog (`describeComicsError` gains the new codes; a
  `panel_unavailable` names the panel and the cause).
- The built view shows each panel as "kept" with a link to its image, or "retired" with its source filename.
- Tracker D11 (a) and (b) are done here: the editor re-fetches the comics after a `built` or `not_found` error, and "Move from X"
  says X is built when that is the reason.

## Documentation and registries

`backend_api.md`; `docs/schema.md` (the `kept` column and the migration); `docs/image-derived-data.md` and `docs/image-status.md`
only if their text names the flag reasons (C1 already documents `comics_source`: add that unflagging is refused); the
`move_flagged` entry in CLAUDE.md and `docs/batches/` (inventory and the mark-and-move use case); `tools/rollout_preflight.py`
`EXPECTED` (the new column, plus `source_image_id` that C1 missed, tracker D9 (c)) and the recut rollout runbook list of revisions;
a short "after a build" note listing the jobs to run for the new image: `extract_text_from_memes --status active`,
`build_image_embeddings --status active --incremental`, `build_ocr_lemmas --incremental`, `classify_text_heavy`,
`build_ocr_text_embeddings`, and `description_pipeline` (check each against `docs/batches/inventory.md` while writing it).

## Non-goals

Undoing a build; editing a built comics; moving source files at build time; the `comics_pipeline` driver and statistics (C5);
carrying notes, saves or ratings of retired panels; letting a kept panel stay linked to the built comics.

## Tests

- Build service (integration, real session and real small image files): two and three panels all unkept; one kept; all kept;
  `built` on a second build; `too_few_panels`; a kept id that is not a panel (`keep_invalid`); each `panel_unavailable` cause;
  `same_as_existing`; `compose_failed` with position; the rollback test (an exception after the file is written removes the file and
  leaves no `images` row and an unchanged comics); popularity of the built image equals 1 plus the tree weights of the absorbed panels (include a panel that had absorbed a copy);
  an `AlreadyDiscardedError` panel refuses with its position and rolls everything back; kept panels
  keep their popularity and are released with `kept = true`.
- Migration: upgrade, downgrade, upgrade of the `kept` column on a throwaway database (the C1 procedure), plus the shape test
  tracker D9 (b).
- `move_flagged`: a `comics_source` image goes to `comics-sources/<name>/`, other flagged images still go to `excluded`, a
  `comics_source` image with no panel row falls back, an existing name in the target is not overwritten.
- Unflag guard (409), the discovery report exclusion, `rollout_preflight` guard, docs guards.
- Frontend: the Build dialog (checkboxes default unchecked, sent `keepPanelIds`, irreversible sentence present, error wording per
  code, navigation on success), the built view's kept/retired rows, the editor refresh after `built`.
- A two-session test pinning the row lock of `get(lock=True)` (tracker D9 (a)): two concurrent builds of one comics, one wins and
  the other gets `built`.

## Review focus

- Two admins pressing Build on one comics at the same moment: exactly one build, one built image, no duplicate absorption edges.
- A panel file deleted or replaced on disk between adding the panel and the build: a named refusal, nothing written.
- A comics name with Cyrillic or trailing characters at move time: the target directory exists and the move does not overwrite an
  earlier comics folder of a name that differs only in case (the name key is unique, so this cannot happen; test it anyway).
- A build whose commit fails after the file was written: the file is removed; a retry succeeds with the same file name.
- Kept panels are never flagged, moved, or absorbed, and a kept image can be added to a new comics afterwards.
