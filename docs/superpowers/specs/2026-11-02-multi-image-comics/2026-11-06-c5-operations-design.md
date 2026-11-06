# C5: Operations

Status: approved
Originates from: docs/superpowers/specs/2026-11-02-multi-image-comics/README.md (tracker project C5)
Follow-ups: none yet

## Intent

C1 to C4 let the owner build a comics, but the built image stays unprocessed until someone runs a series of jobs by hand, nobody
can see how many comics exist, the code has a few known leftovers from C4's review, and the two migrations are not applied
anywhere. C5 closes these: after a build the exact derived data is produced automatically, a few counts appear in statistics,
the leftovers are fixed, and the rollout is written down. After C5 the epic is done.

## Decisions by the owner (brainstorming, 2026-11-06)

- **Exact data only, no stopgap.** The built image is not given provisional text, tags or an averaged embedding. It is in the
  library at once (browse, details page) and appears in search when the pipeline's rows land, normally within minutes. Descriptions
  and the meme-from-text profile wait for the regular description jobs, which the owner accepts. (The embedding-averaging spike is
  dropped with the stopgap.)
- **The build starts the refresh itself.** Nothing is required from the owner after pressing Build. The backend starts
  `comics_pipeline` the way `/admin/batches` starts any job (`AdminBatchService.trigger_run`: a `batch_runs` row, then a
  `batch.run_wrapper` subprocess).
- **Statistics: counts plus the unprocessed number.** Comics by state (draft, built), panels kept and retired, and how many built
  images still lack their derived data. Nothing more.
- **All leftovers are in.** Every item of tracker D12 and D11 that was left for C5 is done here.

## Decisions I made (my calls, to check)

- **The trigger is best effort and after the commit.** The build response never fails because the pipeline could not start. If a
  `comics_pipeline` run is already going (the trigger meets `BatchAlreadyRunningError`), nothing is started and the image waits
  for the next run, since every step is incremental. The trigger runs only after the build's transaction is committed (the
  subprocess must find the image row), for example from a FastAPI background task or after an explicit commit in the endpoint,
  like `trigger_run`'s own early commit; the builder picks the mechanism and checks that the commit really precedes the spawn.
  A failure to start is logged and swallowed.
- **`comics_pipeline` chains only incremental steps over active images**, in dependency order, using the same `Step`/`run_steps`
  driver model as `recut_pipeline` (each child self-tracks under its own kind, a busy child is skipped, a failed step only skips
  the steps that need it, the driver fails if any step failed): `extract_text_from_memes --status active` (OCR), then
  `build_image_embeddings --incremental`, `build_ocr_lemmas --incremental`, `build_tags_from_ocr --incremental`,
  `classify_text_heavy`, `build_ocr_text_embeddings`. It does not include the description chain (`description_pipeline`), duplicate
  detection or the profile jobs: they are slow, run on the owner's own schedule and pick the built image up like any active image.
  The builder confirms the exact incremental entry points and arguments against each job and the `after-build` list already in
  `docs/batches/use-cases/comics-build.md`, and corrects the list above if a step needs another flag.
- **Environments.** `comics_pipeline` is registered on all three environments. A step whose job cannot run on an environment
  (the ConceptTagger tag step on `it`, which has no tagging vocabulary files) is skipped there and recorded as skipped, not as a
  failure, so a build on `it` does not leave a failed run behind. The builder reads each job's own "not configured" behavior and
  makes the driver skip a step up front when its job is not configured, rather than letting it fail.
- **Stuck images.** No retry loop and no second pass. An image built while a run is going, or after a failed run, is processed by
  the next trigger (the next build or a manual run from `/admin/batches`); the unprocessed number in statistics is the signal.
- **Definition of "lacking derived data".** Built comics images with no `ocr_texts` row or no `embeddings` row. Tags are not
  counted (a text that yields no tags is legitimate). If a text-free comics legitimately has no OCR row, the builder uses the
  OCR job's processing marker instead of row absence, and states the final definition in the docs.
- **Tests.** The trigger is tested with a mocked spawn: it fires only after the commit, never on a refused or rolled-back build, a
  busy run is swallowed, and a start failure is logged without failing the build. The pipeline driver is tested with mocked
  children (order, skip on dependency failure, skip on busy, skip when not configured). The real OCR and CLIP models are not run
  in the suites.
- **Runtime of the spawned job.** The build uses exactly the same spawn path as `/admin/batches` (`AdminBatchService.trigger_run`),
  so it works wherever that already works; no new image dependencies are added. The rollout document says so.
- **Cost of the OCR step.** `extract_text_from_memes` lists the whole `BASE_PATH` directory; the builder measures what one run
  costs on a library of this size and, if it is large, restricts the comics run to the unprocessed images (no change in what is
  selected, only in the walk). Do not change what the job does for its other callers.
- **Manual trigger and registry.** `comics_pipeline` is also admin-triggerable from `/admin/batches` (registered in
  `environments/batch_registry.yaml`), manual-trigger only, not scheduled (the build triggers it).
- **Statistics shape.** The counts are added to the statistics snapshot (`build_statistics`, `statistics_snapshots`) and to the
  statistics page and its response schema, in the style of the existing blocks. "Built images lacking derived data" means built
  comics images with no `ocr_texts` row, no `embeddings` row, or no tag; the builder fixes the exact definition and states it in
  the docs.
- **Leftovers, how:**
  - D12(a): `extract_text_from_memes` skips files whose name starts with `.` while listing the directory. The build's temporary
    file is removed on cancellation or failure where the code can; a stray `.comics-build-*` file is harmless once skipped.
  - D12(b): an advisory lock (`pg_advisory_xact_lock`) around the build's content-hash check, file replace and `images` insert,
    keyed on the output content hash, so two builds with identical output cannot both pass `same_as_existing`.
  - D12(c): `_statuses`, `_flagged_ids`, `_live_cut_ids` and the service `_detail` lose the leading underscore; every importer is
    updated; no behavior change.
  - D12(d): a kept panel whose image is deleted later shows a placeholder thumbnail in the built view instead of a broken image.
  - D11(d): the failed preview shows a Retry button that repeats the preview request.
  - D11(e): named schemas for `ComicsAddability.items` and `ComicsByImage.comics` in `shared/schemas/`; the three generated
    trees (TypeScript, Kotlin, Python) are regenerated; the hand-written router models, if any, are checked (see the CLAUDE.md
    gotcha on hand-written response models).
- **Rollout is documents and a check, not an action.** C5 does not apply a migration to any live environment. It adds a rollout
  document (`docs/batches/use-cases/comics-rollout.md`, in the style of `screenshot-recut-rollout.md`) with the steps and
  verification, adds the comics tables and the `kept` column to `tools/rollout_preflight.py`, and updates the runbook. Applying
  `d7a3b9c5e214` and `f3a8c1d6b952` to metal, general and IT happens only with the owner's go-ahead, after the epic is merged.

## Goals

- A built comics gets its OCR text, embedding, lemmas, tags and text-heavy data without anyone starting a job.
- The owner can see how many comics exist and whether any built image is still unprocessed.
- No known C4 leftover remains open.
- The epic can be rolled out by following one document.

## Non-goals and anti-goals

- No stopgap or provisional derived data for the built image, no new stale-data mechanism, no change to the recut refresh modes.
- No descriptions, profile or duplicate detection in `comics_pipeline`.
- No scheduling of `comics_pipeline`; no retry loop for a pipeline that could not start.
- No live migration, no change to any running environment during C5.
- The build must never fail or slow down because of the trigger.

## Constraints

- Follow `docs/batches/` conventions: a registry entry, a row in `docs/batches/inventory.md`, the CLAUDE.md batch list entry and a
  use-case note; `tests/docs/` guards stay green.
- Never touch the live databases or the main `DATABASE_URL`; tests use only the throwaway database `ocrdb_test`.
- Backend, `tests/integration/` (the whole root), `batch/tests/`, `tests/docs/` and the frontend checks run as separate commands.
- Generated types and `backend_api.md` stay in sync with any schema or endpoint change.

## What is left to the builder

Exact step arguments of the pipeline, the trigger mechanism (background task or explicit commit), the definition of "lacking
derived data", test layout, the names of the new settings if any (none are required), the placeholder image for D12(d), and the
order of the plan's tasks.

## Done when

`comics_pipeline` runs from `/admin/batches` and after a build; a build in a test environment leaves a searchable image after the
pipeline step runs; statistics show the three numbers; every D12 and D11 item is closed in the tracker; the rollout document and
preflight exist; all suites are green; the review report is saved; the tracker marks C5 done.
