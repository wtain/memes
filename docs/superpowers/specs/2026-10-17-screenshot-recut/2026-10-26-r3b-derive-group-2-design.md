# R3b: Derive group 2 - design

Status: implementation
Plan: docs/superpowers/plans/2026-10-26-r3b-derive-group-2.md
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md (project R3b, split from R3)
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
Inventory of derived data: docs/image-derived-data.md
Follows: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-25-r3a-cut-and-copy-design.md

R3a creates `cut_proposed` images and copies groups 1 and 3. R3b lets the existing jobs that recompute from pixels (group 2)
run on those cuts: `--status cut_proposed` for three jobs, one guard against the new CLIP vectors leaking into the concept
tags, and the documentation of the run order. The YOLO and CONCEPT tag jobs are not extended (decision 1).

## 1. Decisions

| # | Decision | Source |
|---|---|---|
| 1 | The cut's YOLO and CONCEPT tags are not derived at proposal time. They have no `--status` and no per-image mode (both delete and rebuild every tag of their kind); the next full run covers the cut once it is `active`. Tags are not part of the review view and a `cut_proposed` image is invisible to search | user, 2026-10-26; departs from `docs/image-derived-data.md`, which is updated |
| 2 | `--status cut_proposed` is added to `build_image_embeddings`, `classify_text_heavy` and `build_ocr_text_embeddings` | decomposition.md |
| 3 | `top_images_for_concept` only considers `active` images | this design: cut vectors would otherwise enter concept rankings |
| 4 | The run order for cuts is documented, not enforced | this design |

## 2. Changes

**Jobs.**
- `build_image_embeddings`: `--status` choices become `{pending, active, cut_proposed, all}`. `--incremental` works as today, so
  `--status cut_proposed --incremental` embeds only cuts without a vector. `all` keeps its meaning (no status filter).
- `classify_text_heavy` and `build_ocr_text_embeddings`: `--status` choices become `{active, pending, cut_proposed}`. Both
  already select images that have no row of their own and take the status as a parameter of their repository query, so only the
  accepted choices change.
- The admin trigger is unchanged: those jobs stay "always `--status active`" there; cuts are processed from the shell.

**Concept guard.** `ConceptsRepository.top_images_for_concept` gets `Image.status == 'active'` in its image subquery. Before,
it ranked every image with a CLIP vector, so a `cut_proposed` (and today a `pending`) image could be tagged and take one of the
top-N slots from a library image. Pending images stop being CONCEPT candidates until promoted; the tag appears on the first
run after promotion.

**Duplicate probes need no change.** `rebuild_duplicates` filters `active`; `ingest_find_duplicates` filters `active` or the
run's `pending` images. A `cut_proposed` image with a vector is in neither. R3b adds a test that pins this.

## 3. Run order for cuts

1. `build_image_embeddings --status cut_proposed --incremental`
2. after R3c has refreshed the cut's OCR: `classify_text_heavy --status cut_proposed`
3. then `build_ocr_text_embeddings --status cut_proposed`

Steps 2 and 3 read the cut's OCR. Until R3c rewrites it, that OCR is the stale copy from the source, and both jobs skip images
that already have a row, so a run before the refresh leaves rows computed from the wrong text. Not enforced here; R3c decides
whether its refresh deletes those two rows for the images it rewrites.

## 4. Documentation

- `docs/image-status.md`, "Accepted by `--status`": the three jobs also accept `cut_proposed`; drop "none accepts it yet".
- `docs/image-derived-data.md`: the `embeddings`, `image_classifications` and `ocr_text_embeddings` rows name the new flag value
  (and the OCR-first order); the YOLO and CONCEPT tag rows say "not derived at proposal time; the next full run covers the cut
  once it is active".
- `docs/batches/inventory.md` and `CLAUDE.md`: the new `--status` values and the run order.
- The `--status` job docstrings.

## 5. Testing

- `build_image_embeddings`: `--status cut_proposed` embeds only `cut_proposed` images; with `--incremental` it skips those
  already embedded; `active` and `pending` runs ignore cuts; `all` is unchanged.
- `classify_text_heavy`, `build_ocr_text_embeddings`: each takes up a `cut_proposed` image with OCR text and no row of its own;
  the same call with `--status active` ignores it; the OCR-text job is tested with a `text_heavy`-classified cut.
- Concept guard: `top_images_for_concept` returns no `cut_proposed` or `pending` image even when it is the nearest match (fails
  before the change).
- Duplicate probes: a `cut_proposed` image with a vector is never a candidate for `rebuild_duplicates` or
  `ingest_find_duplicates`.
- Docs guard: every `--status` job's `choices=` list is a subset of `ImageStatus.ALL` plus `all` (the existing guard only checks
  which scripts have the flag).
- Whole `tests/integration/`, `batch/tests/` and `tests/docs/` roots, because the concept query is shared code.

## 6. Out of scope

Per-image modes for the YOLO and CONCEPT jobs; chaining these jobs after `create_recut_cuts`; the group 3 refresh modes (R3c);
any change to the admin trigger.
