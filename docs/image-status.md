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
- `cut_proposed` to removed: the proposal is rejected. The cut is flagged and leaves through `move_flagged` and `unregister_deleted_images`.
- `active` has no outgoing status change. An `active` image leaves the library by being flagged (`image_extras.flagged`), not by a status.

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
