# Use case: after a comics build

Status: draft (written 2026-11-06). Design: `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-05-c4-build-design.md`.

`POST /api/comics/{id}/build` (backend_api.md, "Build a comics") composes the panels into one file in `BASE_PATH`, registers it
as an `active` image and freezes the comics. The call fills no derived data; the new image gets it from the usual jobs.

## Jobs to run for the new image

Run from the repo root, in this order (flags checked against `inventory.md` and each script's argparse):

| Job | Command | Why |
|---|---|---|
| OCR | `python -m batch.extract_text_from_memes --status active` | Text of the new image (the default status is already `active`) |
| Embedding | `python -m batch.build_image_embeddings --status active --incremental` | CLIP vector, so search and duplicates see it. `--incremental` embeds only images without one |
| Lemmas | `python -m batch.build_ocr_lemmas --incremental` | Smart search index. `--incremental` skips images already indexed |
| Text-heavy | `python -m batch.classify_text_heavy` | Classifier, needs the OCR text |
| OCR text embedding | `python -m batch.build_ocr_text_embeddings` | Only for text-heavy images, needs `classify_text_heavy` |
| Descriptions and tags | `python -m batch.description_pipeline` | Tags from OCR, descriptions, profiles; each re-trigger covers the next part of the backlog |

All of them are also triggerable from `/admin/batches`.

## The source images

- Every panel's source image that is not in `keepPanelIds` is flagged with reason `comics_source`. It disappears from the library
  views at once, because flagged images are filtered out, but its file stays where it is until the next `move_flagged` run.
- `move_flagged` moves such a file to `BASE_PATH/comics-sources/<comics name>/` (not `excluded/`) and counts it in
  `moved_to_comics_sources`. `unregister_deleted_images` then removes the rows, as for any moved file.
- Unflagging a `comics_source` image through the images API is refused with `409` `comics_source_flag`, and so is flagging one again.
