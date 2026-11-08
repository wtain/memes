# Runbook: first live rollout of multi-image comics

Status: the first two comics migrations (`d7a3b9c5e214`, `f3a8c1d6b952`) are applied on all three environments (metal 2026-11-06, general and it 2026-11-07, each after a fresh backup, preflight `READY`, smoke tests passed); the C9 migration `a9d3e6b1c847` is applied on all three environments (2026-11-08, each after a fresh backup `ocrdb-2026-11-08-<env>.dump`, preflight `READY`, smoke tests passed); the smoke build below has not been run yet. Design: `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-06-c5-operations-design.md`.
Use case: `comics-build.md`. Nothing here runs by itself. **Applying a migration to any environment needs the owner's explicit
go-ahead, one environment at a time**; no agent applies one. Order: **general, then metal, then it**. Stop at the first failing step
of an environment.

The commands are PowerShell from the repo root unless a step says `Storage/`. `$py` is `.\.venv311\Scripts\python.exe`.

## The migrations

Three migrations belong to comics. They are the last three of the chain (head is `a9d3e6b1c847`), after `b4c9e1f7a028` (S6):

1. `d7a3b9c5e214` C1: `comics` and `comics_panels` (including `source_image_id`, `source_filename`, `source_content_hash`), and the `comics_build` popularity source
2. `f3a8c1d6b952` C4: `comics_panels.kept`
3. `a9d3e6b1c847` C9: `comics_candidates` and `comics_candidate_images` (suggested groups)

All three only add tables, columns and constraints and rewrite no existing row. An environment that is behind by more than these three also
receives the recut, popularity, saves and impressions migrations in the same `alembic upgrade head`; the full list, and the
backup, quiet-writers and restart steps, are in `screenshot-recut-rollout.md` ("The migrations", twelve in all). If an
environment already ran that rollout up to `b4c9e1f7a028`, only these three are pending. Use that runbook for the shared
steps (read-only URL, backup, upgrade); this one adds what is specific to comics.

## Per environment (general, then metal, then it)

1. **Preflight before.** With `DATABASE_URL_READONLY` set for this environment (see the recut runbook, step 0):
   `& $py -m tools.rollout_preflight --env general --backup-dir Storage\backups`
   Expect `NOT READY` only because of the pending revisions, with `WARN` lines for `comics_candidates*` (only `a9d3e6b1c847` is pending on an environment that ran the first two comics
   migrations; one that did not also gets `comics.*` and `comics_panels.*` lines). `schema_matches_models` lists each missing
   column with the revision that creates it.
2. **Backup**, **quiet the writers**, **upgrade** (`alembic upgrade head` from `Storage/`): as in the recut runbook.
3. **Preflight after.** Expect `READY` and `at head a9d3e6b1c847`, and `expected columns present`.
4. **Restart the backend** of that environment on matching code. The backend image needs Pillow and
   `batch/utils/{__init__,recut_cut,file_hash,comics_compose}.py` (the build composes the image in the request and writes into `BASE_PATH`).
   Smoke test: `/api/diagnostics/health`, `/api/images?limit=1`, `GET /api/comics?limit=1`, and the `comics` block of
   `GET /api/diagnostics/statistics?live=true` (counts zero, `builtUnprocessed` zero).

## What the build triggers

After its commit, `POST /api/comics/{id}/build` starts `comics_pipeline` through the same spawn path as `/admin/batches`
(run row, commit, spawn of the batch wrapper). The backend host must therefore be able to run the batch stack (Python
environment, `ai/` dependencies, GPU where the OCR needs one) wherever the admin trigger already works; if it cannot, the build
still succeeds, the failure is logged, and the pipeline can be triggered from `/admin/batches` on a host that can. A pipeline
that is already running is not an error: the built image is picked up by the next run. On `it` the tag step is
`skipped_not_configured` (no tagging vocabulary), which is expected.

## Smoke build (owner, one environment, after the upgrade)

Not run by an agent. Make a throwaway comics of two or three images that are expendable (copies, or images the owner would
accept to see flagged), then:

1. Create the comics and add the images from the meme page; `GET /api/comics/{id}/preview` should show the tiling.
2. Build it with none of the panels kept. Expect `200`, a new `active` image `comics-<hash12>.<ext>` in `BASE_PATH`, the
   panels' sources flagged `comics_source`, and a `comics_pipeline` row on `/admin/batches`.
3. Wait for the pipeline: every step `ok` (`ocr_tags` `skipped_not_configured` on `it`). The statistics `comics.builtUnprocessed`
   falls back to zero and the image is found by search on its text.
4. Run `move_flagged`: the sources move to `BASE_PATH/comics-sources/<name>/` (`moved_to_comics_sources`).
5. Look in `BASE_PATH` for leftover `comics-*` files without an image row and hidden `.comics-build-*` files (the latter are
   skipped by OCR but should not accumulate).

## After the C9 migration (suggestions)

Once an environment is at head `a9d3e6b1c847`, run `detect_comics_candidates` once from `/admin/batches` (or `python -m batch.detect_comics_candidates --env <env>`), then open `/admin/comics/candidates`. The job writes only the candidate tables and is safe to re-run. In `general` expect on the order of a hundred groups; `metal` and `it` have fewer filename timestamps. See `comics-candidates.md`.

## If something fails

- *Preflight after is not READY, or the upgrade failed:* the upgrade is one transaction, so a failure leaves the starting
  revision; read the error, never edit `alembic_version` by hand.
- *Smoke test fails and nothing new was written:* `alembic downgrade` to the revision noted before the upgrade.
  `f3a8c1d6b952` drops `comics_panels.kept`. `d7a3b9c5e214` **refuses once a `comics_build` popularity row exists** and its
  downgrade drops the comics tables, including the records of every built comics. If either refuses or fails, restore the
  dump of that environment only; the cost is the writes since the backup.
- *After a real build:* do not downgrade. A built comics has an image, flagged sources and absorbed popularity; restoring the
  dump loses those along with the build.
- *The pipeline did not start or a step failed:* trigger `comics_pipeline` again from `/admin/batches`; every step is incremental.
- *`comics.builtUnprocessed` stays above zero and no new run appears:* look for a stuck `started` `batch_runs` row of kind
  `comics_pipeline`. A busy run makes later build triggers no-ops, and nothing reaps it for an unscheduled kind; it needs manual
  cleanup (mark it failed), see `docs/batches/execution-model.md` section 3, "Concurrency: one active run per kind".

## Known cost

Every pipeline run walks the whole `BASE_PATH` in `extract_text_from_memes`, about two queries per file
(`find_image_by_filename` and `should_process`). At about 22,000 files on a local database the expected order is a minute or
two, but it is unmeasured. Measure it with the first smoke build and narrow the walk to unprocessed images only if it is slow
(tracker D13).

## What to record afterwards

Whether the build's spawn worked on the backend host (the log line `comics: could not start comics_pipeline` means it did not),
the time from build to a fully processed image, and any leftover files found in step 5 of the smoke build.
