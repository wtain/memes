# Runbook: first live rollout of multi-image comics

Status: draft (written 2026-11-06, never run). Design: `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-06-c5-operations-design.md`.
Use case: `comics-build.md`. Nothing here runs by itself. **Applying a migration to any environment needs the owner's explicit
go-ahead, one environment at a time**; no agent applies one. Order: **general, then metal, then it**. Stop at the first failing step
of an environment.

The commands are PowerShell from the repo root unless a step says `Storage/`. `$py` is `.\.venv311\Scripts\python.exe`.

## The migrations

Two migrations belong to comics. They are the last two of the chain (head is `f3a8c1d6b952`), after `b4c9e1f7a028` (S6):

1. `d7a3b9c5e214` C1: `comics` and `comics_panels` (including `source_image_id`, `source_filename`, `source_content_hash`), and the `comics_build` popularity source
2. `f3a8c1d6b952` C4: `comics_panels.kept`

Both only add tables, columns and constraints and rewrite no existing row. An environment that is behind by more than these two also
receives the recut, popularity, saves and impressions migrations in the same `alembic upgrade head`; the full list, and the
backup, quiet-writers and restart steps, are in `screenshot-recut-rollout.md` ("The migrations", eleven in all). If an
environment already ran that rollout up to `b4c9e1f7a028`, only these two are pending. Use that runbook for the shared
steps (read-only URL, backup, upgrade); this one adds what is specific to comics.

## Per environment (general, then metal, then it)

1. **Preflight before.** With `DATABASE_URL_READONLY` set for this environment (see the recut runbook, step 0):
   `& $py -m tools.rollout_preflight --env general --backup-dir Storage\backups`
   Expect `NOT READY` only because of the pending revisions, with `WARN` lines for `comics.*` and `comics_panels.*` columns
   (`schema_matches_models` lists each missing column with the revision that creates it).
2. **Backup**, **quiet the writers**, **upgrade** (`alembic upgrade head` from `Storage/`): as in the recut runbook.
3. **Preflight after.** Expect `READY` and `at head f3a8c1d6b952`, and `expected columns present`.
4. **Restart the backend** of that environment on matching code. The backend image needs Pillow and
   `batch/utils/{__init__,recut_cut,file_hash}.py` (the build composes the image in the request and writes into `BASE_PATH`).
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

## What to record afterwards

Whether the build's spawn worked on the backend host (the log line `comics: could not start comics_pipeline` means it did not),
the time from build to a fully processed image, and any leftover files found in step 5 of the smoke build.
