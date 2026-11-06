# Use case: after a comics build

Status: draft (written 2026-11-06). Design: `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-05-c4-build-design.md`,
`docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-06-c5-operations-design.md`. First live rollout: `comics-rollout.md`.

`POST /api/comics/{id}/build` (backend_api.md, "Build a comics") composes the panels into one file in `BASE_PATH`, registers it
as an `active` image and freezes the comics. The call fills no derived data itself: after its commit it starts `comics_pipeline`,
and the new image gets its data from that run.

## The build starts `comics_pipeline`

The build commits first, then starts the driver through the same path as a trigger from `/admin/batches` (run row, commit,
spawn of the batch wrapper), in its own session. This is best effort: a `comics_pipeline` that is already running is not an
error (every step is incremental, so the image is picked up by the next run) and any other failure is logged; the build
response never depends on it. The backend host therefore must be able to run the batch stack wherever the admin trigger works.

`comics_pipeline` is also admin-triggerable by hand; it is manual-trigger only and not scheduled. Every step is incremental over
the active library, so resuming after a failure is triggering again.

```
extract_text_from_memes (ocr) ─┬─► build_ocr_lemmas --incremental (ocr_lemmas)
                               ├─► build_tags_from_ocr --incremental (ocr_tags)   [skipped_not_configured on `it`]
                               └─► classify_text_heavy (classify) ──► build_ocr_text_embeddings (ocr_text_embeddings)
build_image_embeddings --incremental (embeddings)      no dependency
```

- A step whose upstream step failed is `skipped_dependency`; a child that is already running is `skipped_busy`; `ocr_tags`
  is `skipped_not_configured` where no tagging vocabulary exists (`it`). Each child self-tracks under its own kind, the driver
  records the per-step results in `stats.steps` and fails at the end if any step failed.
- Descriptions, profiles and tags from descriptions are not part of it: they are slow and capped per run. Run
  `description_pipeline` (re-trigger until the backlog is empty) when the new image should get them.
- `extract_text_from_memes` skips hidden files (a name starting with a dot, counted as `skipped.hidden`), so the temporary
  `.comics-build-*` file of a build that is still running, or left by a cancelled one, is never registered as a meme.
- The statistics page counts built comics images that are not processed yet (`comics.builtUnprocessed`): a built image with no
  finished OCR marker (`easyocr:en`) or no embedding. Tags are not counted.

## The source images

- Every panel's source image that is not in `keepPanelIds` is flagged with reason `comics_source`. It disappears from the library
  views at once, because flagged images are filtered out, but its file stays where it is until the next `move_flagged` run.
- `move_flagged` moves such a file to `BASE_PATH/comics-sources/<comics name>/` (not `excluded/`) and counts it in
  `moved_to_comics_sources`. `unregister_deleted_images` then removes the rows, as for any moved file.
- Unflagging a `comics_source` image through the images API is refused with `409` `comics_source_flag`, and so is flagging it again with a different reason. A request with reason `comics_source` is refused too (`422` `comics_source_reserved`): the reason is reserved for the build.
- A leftover file in `BASE_PATH` from a crashed or cancelled build (`comics-<hash12>.*` with no image row) is still picked up by `extract_text_from_memes` as an ordinary new image; check `BASE_PATH` for such files. The hidden `.comics-build-*` files are skipped.

## Concurrent builds with identical output

Two different comics whose composed output is byte-identical cannot both become images (the content hash is unique). The check,
the file replace and the insert run under a transaction-scoped advisory lock on the content hash, so the second build sees the
first and is refused (`same_as_existing`). Accepted residual, very rare: if the first build fails late and rolls back, the
rollback releases the lock before the first build's cleanup removes its file. A second build waiting on the lock can write the
same file name in that gap and then lose it to the first build's removal, leaving a built image without a file. Check the
second comics' image and rebuild it if its file is missing (`unregister_deleted_images` removes a row without a file).
