# R3c: Refresh stale - design

Status: done
Plan: docs/superpowers/plans/2026-10-26-r3c-refresh-stale.md
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md (project R3c, split from R3)
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
Inventory of derived data: docs/image-derived-data.md
Follows: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-26-r3b-derive-group-2-design.md

R3a copies the group 3 derived data onto a cut with `stale = true`. R3c gives each group 3 job a "refresh stale" mode that
replaces those copies with rows computed from the cut's own pixels or text, and one driver that runs the modes in dependency
order. The modes say nothing about when they run; R7 decides that.

## 1. Decisions

| # | Decision | Source |
|---|---|---|
| 1 | Refresh modes are timing-neutral: they work on whatever `cut_proposed` images carry stale rows. Whether a refresh runs before or after review is decided in R7 | user, 2026-10-26 |
| 2 | Each job gets its own refresh mode, plus one driver, `refresh_recut_cuts`, that runs them in order (same model as `description_pipeline`) | user, 2026-10-26 |
| 3 | A stale row is deleted only in the transaction that writes its replacement (or once the job knows the result is empty). A failure leaves the stale copy and the image stays selectable | this design, NFR-4 |
| 4 | A job skips an image whose upstream rows are still stale (`skipped_upstream_stale`); the next driver run picks it up | this design |
| 5 | The OCR refresh also deletes the cut's `image_classifications` and `ocr_text_embeddings` rows, which were computed from the old OCR, so `classify_text_heavy` and `build_ocr_text_embeddings` recompute them. (`image_processing_status` markers need no handling: R3a does not copy them) | this design, closes the R3b run-order caveat |
| 6 | No migration and no change to `Storage/models.py` | the `stale` columns and `Storage/stale.py` already exist |

## 2. The mode contract

Five jobs accept `--refresh-stale`: `extract_text_from_memes`, `build_ocr_lemmas`, `build_tags_from_ocr`,
`build_image_descriptions` and `build_tags_from_descriptions`. The other group 3 jobs need no flag (section 3). With it the job:

1. selects the `cut_proposed` images that have stale rows in the job's own table (`images_with_stale_rows`), whose upstream rows
   (the previous job in section 3) are no longer stale;
2. per image, in one transaction: computes the new rows with the job's normal logic, deletes the image's stale rows
   (`delete_stale_rows`), inserts the new ones (default `stale = False`);
3. records stats `images_selected`, `refreshed`, `skipped_upstream_stale`, `failed`, `images_remaining`.

`--refresh-stale` combines with `--limit` where the job has one, and conflicts with `--reset`, `--incremental` and `--retry-failed` (rejected at argument
parsing). It ignores `--status`: it always means `cut_proposed`. It does not touch images without stale rows, so
a normal run and a refresh run never overlap. A job whose new result is empty (no OCR text, no tags) deletes the stale rows and
writes nothing, so no stale copy outlives the refresh (decomposition.md section 3).

## 3. Jobs and order

| Step | Job | Stale table | Refresh does | Needs |
|---|---|---|---|---|
| 1 | `extract_text_from_memes` | `ocr_texts` | OCR of the cut file for the selected images (read from the database, not a directory walk); rewrites rows per language; deletes the image's `image_classifications`, `ocr_text_embeddings` and the `image_processing_status` markers of the OCR-derived jobs (decision 5) | - |
| 2 | `build_ocr_lemmas` | `ocr_lemmas` | re-lemmatises the new OCR rows | step 1 |
| 3 | `build_tags_from_ocr` | `tags` (source OCR) | rewrites the image's OCR tags from the new OCR | step 1 |
| 4 | `build_image_descriptions` | `image_descriptions` | re-describes the cut for each configured prompt | - |
| 5 | `build_image_description_embeddings` | none (rows go with the description) | no flag: replacing a stale description cascades away its copied embedding, so the normal incremental run embeds the new one | step 4 |
| 6 | `build_description_lemmas` | none | no flag: same, the normal incremental run indexes the new description | step 4 |
| 7 | `build_tags_from_descriptions` | `tags` (source Ollama) | rewrites the image's Ollama tags | step 4 |
| 8 | `build_meme_profiles`, `build_meme_profile_embeddings` | none (derived staleness) | `build_meme_profiles --status cut_proposed` (no `--reset`) extracts from the new inputs and skips cuts whose OCR or descriptions are still stale; the embeddings job needs no change: it embeds any profile without a vector and a changed situation deletes the old one | steps 1, 4 |

`classify_text_heavy --status cut_proposed` and `build_ocr_text_embeddings --status cut_proposed` (R3b) run after step 1 and are
not refresh modes; the driver calls them after OCR. Tables without a stale column lose their copied rows when the description they
hang off is deleted (foreign key cascade), so only `ocr_texts`, `ocr_lemmas`, `image_descriptions` and `tags` need the flag.

A refresh run selects by the stale flag of the job's own table only; a normal run is unchanged. A cut whose source had no row in
that table has nothing stale to replace there; the normal run of the job fills it once the cut is `active` (the same principle as
R3b decision 1). The descriptions refresh is sequential and replaces a description per prompt; the feedback copied with a stale
description is lost with it (the feedback judged the old text).

## 4. The driver

`batch/refresh_recut_cuts.py`, kind `refresh_recut_cuts`, run-tracked, admin-triggerable from `/admin/batches`, manual-trigger
only, not scheduled (`environments/batch_registry.yaml` entry). It runs steps 1 to 8 as children, each self-tracked under its own
kind. A failed step skips only the steps that need it; an already-running child is skipped, not failed. Steps 4 to 8 are skipped
on an environment without the job's configuration (`it` has no profile model), as `description_pipeline` does. Stats: per step
outcome plus the images still stale (`stale_counts`).

## 5. Failure handling

- An unreadable or missing cut file leaves the image stale and counts `failed`; in the OCR refresh 20 unreadable files in a row
  stop the job, as in `create_recut_cuts`.
- A crash rolls back to the stale copies. A refresh is idempotent and safe to re-run.
- A failed image's dependants skip it (decision 4) until it succeeds. The descriptions refresh records no failure rows: a failed
  prompt keeps its stale description and is tried again on the next run.
- The job's own run lock keeps two runs of one job apart. An image approved while a refresh runs only changes status; the next
  run works from its new state.

## 6. Documentation and registry

- `docs/image-derived-data.md`: group 3 rows name the refresh flag and mark the refresh as implemented.
- `docs/image-status.md`: the cut's life up to review names the refresh as the step after `create_recut_cuts`.
- `docs/batches/inventory.md`, `CLAUDE.md` batch list, `environments/batch_registry.yaml` (CRLF): the new flag on each job and the driver.
- Job docstrings. `tests/docs/test_status_flag_choices.py` stays valid because no new `--status` value is introduced.

## 7. Testing

Test roots run separately, with the test database.

- Per job: an image with stale rows is refreshed (rows replaced, `stale = False`); an image without stale rows is untouched; an
  empty result leaves no stale rows; a failing image keeps its stale rows and is counted `failed`; the flag conflicts are rejected.
- OCR refresh: classification and OCR-text embedding rows of the image are gone afterwards; other images keep theirs.
- Upstream skip: a description-derived step skips an image whose descriptions are still stale and refreshes it once they are not.
- Driver: runs the steps in order, a failed step skips only its dependants, an already-running child is skipped, stats are recorded.
- Registry and docs guards, then the whole `tests/integration/`, `batch/tests/` and `tests/docs/` roots (the jobs share selection code).

## 8. Out of scope

When the refresh runs and its scheduling or admin-page exposure beyond the registry entry (R7); the live rollout; re-describing
active images that have no stale rows; moving ratings on approve (R5); the review UI (R4).
