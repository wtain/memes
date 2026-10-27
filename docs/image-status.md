# Image status registry

Authoritative definition of the values of `images.status`. Specs, `CLAUDE.md` and the batch documents link here and do not
redefine statuses. A new status needs a row here in the same change; `tests/docs/test_image_status_registry.py` fails
otherwise. The constants live in `Storage/models.py` (`ImageStatus`); the column itself is a free `String(20)`.

## Statuses

| Status | Meaning | Set by | Read by |
|---|---|---|---|
| `active` | In the library and searchable. The column default, and the value every list query filters on unless told otherwise | column default; `ingest_promote`; approval of a recut proposal (screenshot-recut R5) | all default queries; jobs without `--status` |
| `pending` | Ingested into an in-flight ingestion run and awaiting duplicate review. The file is already in `BASE_PATH` | `ingest_hash_dedup` | ingestion review endpoints and page; jobs with `--status pending`; `ingest_promote`; `ingest_abort` |
| `rejected` | Rejected during ingestion review. The file was moved out of the library, the row is kept so the decision can be undone | ingestion review | ingestion tooling only |
| `cut_proposed` | A proposed cut of a badly cropped image, awaiting human review. Invisible to search and browse | the cut step (screenshot-recut R3) | recut review (R4); jobs with `--status cut_proposed` (R3) |

## Transitions

- `pending` to `active`: `ingest_promote`, once the image has no unresolved duplicate candidate pairs.
- `pending` to `rejected`: ingestion review confirms a duplicate.
- `rejected` to `pending`: undo in the ingestion review UI (the file is moved back).
- `pending` or `rejected` to removed: `ingest_abort` moves the file back to the ingestion source and deletes the row. It never touches `active` images.
- `cut_proposed` to `active`: the proposal is approved (screenshot-recut R5). The source image is flagged for the existing `move_flagged` path in the same change.
- `cut_proposed` to removed: the proposal is rejected (screenshot-recut R5). The cut is flagged and its file is moved by `move_flagged`. Caveat for R5: `unregister_deleted_images` only reconciles `active` images today (`get_all_images` defaults to `status="active"`), so R5 must extend it to `cut_proposed` or delete the row itself; otherwise a rejected cut would keep a row pointing at a missing file.
- `active` has no outgoing status change. An `active` image leaves the library by being flagged (`image_extras.flagged`), not by a status.

## Accepted by `--status`

These batch scripts take `--status` (checked against the code by `tests/docs/test_image_status_registry.py`). Every one
accepts `active` (the default) and `pending`; `build_image_embeddings`, `classify_text_heavy` and `build_ocr_text_embeddings` also accept
`cut_proposed` (screenshot-recut R3b). Run order for cuts: `build_image_embeddings --status cut_proposed --incremental`; then, after
the cut's OCR is refreshed (R3c), `classify_text_heavy --status cut_proposed`; then `build_ocr_text_embeddings --status cut_proposed`.
The last two skip images that already have a row, so a run before the OCR refresh would keep rows computed from the source's text;
`extract_text_from_memes --refresh-stale` deletes those rows for every cut it re-reads, and `refresh_recut_cuts` runs the whole
sequence in order (R3c).
The admin trigger of these jobs stays `--status active`; cuts are processed from the shell.

- `build_image_embeddings` (also accepts `all`)
- `extract_text_from_memes` (also accepts `all`)
- `build_ocr_lemmas` (`--status pending` requires `--incremental`)
- `classify_text_heavy`
- `build_ocr_text_embeddings`
- `fix_image_formats`
- `build_meme_profiles` (takes `active` and `cut_proposed` only, no `pending`; `cut_proposed` skips cuts whose OCR or descriptions are still stale, and is not combinable with `--reset`)

`all` is a selector meaning "every status", not a status. It would also select `rejected` images, and later `cut_proposed` ones.

## Not image statuses

These look like statuses but belong to something else. Do not mix them with the table above.

| Name | Where | Values | Meaning |
|---|---|---|---|
| `batch_runs.status` | one row per batch job run | `started`, `completed`, `failed`, ... (`RunStatus`) | State of a run, including an ingestion run |
| `image_processing_status.status` | one row per image and pipeline | `processing`, `done`, `failed` | Whether a job has handled that image; also what makes some jobs skip it |
| `image_cuts.state` | one row per recut proposal | `detected`, `proposed`, `approved`, `rejected`, `failed` (`CutState`) | Where a proposal is in its own lifecycle |
| `image_extras.flagged` | one row per image | true / false with a reason | Marks an image to be moved out of the library by `move_flagged` |

Related designs: `docs/superpowers/specs/2026-07-25-image-visibility-status-design.md` (why the column and the default exist),
`docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md` (`pending` and `rejected`),
`docs/superpowers/specs/2026-10-17-screenshot-recut/README.md` (`cut_proposed`).
