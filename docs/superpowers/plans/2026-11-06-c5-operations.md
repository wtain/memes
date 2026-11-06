# C5 Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a comics build the exact derived data is produced automatically, statistics show comics counts, the C4 leftovers are closed and the rollout is documented.

**Architecture:** A new driver `batch/comics_pipeline.py` (the `Step`/`run_steps` model of `batch/recut_pipeline.py`) chains the incremental OCR, embedding, lemma, tag and text-heavy jobs over active images. The build endpoint starts it after the build's transaction commits, through the same spawn path as `AdminBatchService.trigger_run`, best effort. Comics counts are added to the statistics snapshot and page. Small fixes and documents close the epic.

**Tech Stack:** Python 3.11 (FastAPI, SQLAlchemy async, Dynaconf), React/TypeScript (vitest), JSON schemas generated to TS, Kotlin and Python.

**Spec:** `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-06-c5-operations-design.md`

## Global Constraints

- Tests use only the throwaway database `postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test`; never the main `DATABASE_URL` of a live environment. Run `Backend/tests`, `tests/integration/`, `batch/tests/`, `tests/docs/` as separate pytest commands (never combined). Python: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe`.
- Frontend checks from `Frontend/memes-frontend/`: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`. A schema change means regenerating TypeScript (`Frontend/generate-types.sh`), Kotlin (`AndroidClient/scripts/generate_dtos.py`) and Python (`documents/generation.md`) types, with no leftover diff.
- A shared-code change needs the whole `tests/integration/` root (CLAUDE.md gotcha).
- `backend_api.md`, `docs/batches/inventory.md`, the CLAUDE.md batch list and `tests/docs/` guards stay in sync with every change.
- No migration is applied to any live database; no stopgap derived data; no change to the recut refresh modes.
- The build must never fail or slow down because of the pipeline trigger.
- Commit messages end with the two attribution lines given in the session reminder.

## Review Focus

- A build refused or rolled back must not start the pipeline (test: no spawn).
- The spawned job must find the built `images` row (the trigger runs only after the commit; test with a second session).
- A busy `comics_pipeline` (`BatchAlreadyRunningError`) or a spawn failure leaves the build response a success.
- On `it` the tag step is skipped, not failed, so the run does not end `failed`.
- A hidden `.comics-build-*` file in `BASE_PATH` is never registered as a meme.
- Two builds with byte-identical output at the same moment: exactly one succeeds, the other answers `same_as_existing`.

---

### Task 1: OCR job skips dot-files (D12a)

**Files:**
- Modify: `batch/extract_text_from_memes.py` (`io_producer`, the `os.listdir` loop near line 50)
- Test: `batch/tests/` (new `test_extract_text_skips_hidden_files.py`, or the existing producer test file if one covers `io_producer`)

**Interfaces:**
- Produces: files whose name starts with `.` are skipped with a metric `skipped.hidden`.

- [ ] **Step 1: Write the failing test** `test_io_producer_skips_dot_files`: a temp directory with `a.jpg` and `.comics-build-x.jpg`; run `io_producer` with mocked repositories (follow the existing producer tests' fixtures); assert only `a.jpg` is queued and `skipped.hidden == 1`.
- [ ] **Step 2: Run it, expect FAIL** (`batch/tests/` root).
- [ ] **Step 3: Implement** the skip in the `os.listdir` loop before the directory check; increment `metrics_listener` key `skipped.hidden`.
- [ ] **Step 4: Run the test and the whole `batch/tests/` root, expect PASS.**
- [ ] **Step 5: Commit** `fix(comics): OCR job skips hidden files`.

### Task 2: `comics_pipeline` driver and registry

**Files:**
- Create: `batch/comics_pipeline.py`, `batch/tests/test_comics_pipeline.py`
- Modify: `environments/batch_registry.yaml` (entry `comics_pipeline`: module `batch.comics_pipeline`, kind `comics_pipeline`, next to `recut_pipeline`)

**Interfaces:**
- Consumes: `batch.description_pipeline.Step/run_steps/FAILED/SKIPPED_*`, `batch.run_tracking.tracked_run/finish_existing_run/record_stats`.
- Produces: `async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None` and a module-level `STEPS` list, same shape as `batch/recut_pipeline.py`.

- [ ] **Step 1: Write failing tests** (mocked children, pattern of `batch/tests/test_recut_pipeline.py`):
  - `test_steps_run_in_order` expects `ocr`, `embeddings`, `ocr_lemmas`, `ocr_tags`, `classify`, `ocr_text_embeddings`.
  - `test_failed_ocr_skips_dependents`: `classify`, `ocr_lemmas`, `ocr_tags` need `ocr`; `ocr_text_embeddings` needs `classify`; `embeddings` has no dependency.
  - `test_busy_child_is_skipped_not_failed`.
  - `test_driver_fails_when_a_step_failed`.
  - `test_unconfigured_tag_step_is_skipped_up_front`: with the tagging vocabulary not configured (the condition under which `build_tags_from_ocr` cannot run on `it`), the step is recorded skipped, not failed, and the run does not raise.
- [ ] **Step 2: Run, expect FAIL** (`batch/tests/`).
- [ ] **Step 3: Implement** `batch/comics_pipeline.py`. Child calls: `extract_text_from_memes.main(trigger=t)` (active), `build_image_embeddings.main(trigger=t, incremental=True)`, `build_ocr_lemmas.main(trigger=t, incremental=True)`, `build_tags_from_ocr.main(trigger=t, incremental=True)`, `classify_text_heavy.main(trigger=t)`, `build_ocr_text_embeddings.main(trigger=t)`. Confirm each argument against the job's `main` and against the after-build list in `docs/batches/use-cases/comics-build.md`; fix the list if a step needs another flag. Determine how a job signals "not configured" on `it` (read `build_tags_from_ocr.py` and `rules/concept_tagger.py` setup; `build_meme_profiles` has a clean "not configured" exit) and add a skip condition to `Step` use (extend `run_steps` minimally, or a guard wrapper that returns a skipped result); do not change behavior for the other drivers. Tracked run kind `comics_pipeline`; stats carry the per-step results.
- [ ] **Step 4: Register** in `environments/batch_registry.yaml`; extend any registry test that lists names.
- [ ] **Step 5: Run `batch/tests/` and `Backend/tests` (registry-dependent), expect PASS. Commit** `feat(comics): comics_pipeline driver`.

### Task 3: Build starts the pipeline; advisory lock (D12b)

Highest-risk task: the most capable model.

**Files:**
- Create: `Backend/app/services/comics_pipeline_trigger.py`, `Backend/tests/test_comics_pipeline_trigger.py`
- Modify: `Backend/app/api/comics.py` (build endpoint), `Backend/app/services/comics_build_service.py` (advisory lock), `backend_api.md` (build endpoint note)
- Test: `tests/integration/test_comics_build_service.py` (lock), `Backend/tests/test_comics_endpoints.py` (trigger wiring)

**Interfaces:**
- Consumes: `AdminBatchService.trigger_run(batch_name)` semantics (`Backend/app/services/admin_batch_service.py`: `create_run`, early commit, `fire_and_forget(spawn_and_track(...))`), `AsyncSessionLocal`.
- Produces: `async def start_comics_pipeline() -> str | None` in the new module: opens its **own** session, calls the same code path as `trigger_run("comics_pipeline")`, returns the run id or `None`; swallows `BatchAlreadyRunningError` (info log) and any other exception (warning log). It never raises.

- [ ] **Step 1: Write failing tests** (`Backend/tests/test_comics_pipeline_trigger.py`, mocked spawn): `test_returns_run_id_on_start`, `test_busy_run_is_swallowed`, `test_spawn_failure_is_logged_not_raised`.
- [ ] **Step 2: Implement `start_comics_pipeline`.** Reuse `AdminBatchService` with a fresh session rather than copying the spawn code; if its constructor needs the repo and session, build them the way `get_admin_batch_service` does.
- [ ] **Step 3: Write failing endpoint tests** (`Backend/tests/test_comics_endpoints.py`): successful build calls `start_comics_pipeline` once; a refused build (409/422/`not_buildable`/`same_as_existing`) never calls it; a trigger that raises still returns the 200 build result.
- [ ] **Step 4: Wire the endpoint.** The trigger must run only after the build's transaction is committed. Choose the mechanism and prove it: either a FastAPI background task (verify against the installed FastAPI version that yield-dependency commit (`get_async_db`) happens before background tasks run) or an explicit `await db.commit()` in the endpoint before the trigger call. Add an integration test in `tests/integration/` that builds, then reads the built `images` row from a **second** session at the moment `start_comics_pipeline` is invoked (patched), asserting it is visible.
- [ ] **Step 5: Write failing lock test** (`tests/integration/test_comics_build_service.py`): two concurrent `ComicsBuildService.build` calls for two draft comics whose composed output is byte-identical (same two source images in two comics is not allowed, so use two comics of copies with identical content and identical order, or patch the compose step to return identical bytes); exactly one succeeds, the other raises `same_as_existing`; no second `images` row and the surviving file exists.
- [ ] **Step 6: Implement the lock** in `comics_build_service.py`: `pg_advisory_xact_lock(hashtext(<content hash>))` (or a two-int key derived from the hash) taken after composing and hashing and before the `same_as_existing` check; it releases with the transaction.
- [ ] **Step 7: Run `Backend/tests`, the whole `tests/integration/` root, and `batch/tests/`; expect PASS.** Update `backend_api.md` (build starts `comics_pipeline`, best effort). **Commit** `feat(comics): build starts comics_pipeline, advisory lock on identical output`.

### Task 4: Public repository and service helpers (D12c)

**Files:**
- Modify: `repository/comics.py` (`_statuses`, `_flagged_ids`, `_live_cut_ids`), `Backend/app/services/comics_service.py` (`_detail`), and every importer found with `grep` (`Backend/app/api/comics.py`, `Backend/app/services/comics_build_service.py`, their tests)

**Interfaces:**
- Produces: `statuses`, `flagged_ids`, `live_cut_ids`, `detail` (same signatures, no leading underscore).

- [ ] **Step 1:** Rename with the editor's find/replace, then `grep -rn "_statuses\|_flagged_ids\|_live_cut_ids\|_detail\b"` over the repo to prove no importer is left (tests that patch these names included).
- [ ] **Step 2: Run `Backend/tests` and the whole `tests/integration/` root, expect PASS. Commit** `refactor(comics): make shared helpers public`.

### Task 5: Statistics (counts and unprocessed built images)

**Files:**
- Modify: `shared/schemas/statisticsresponse.schema.json` (+ a new `shared/schemas/statisticscomicsstats.schema.json`, style of `statisticscontentstats.schema.json`), `Backend/app/repositories/diagnostics_repository.py`, `Backend/app/services/statistics_snapshot_service.py`, `batch/build_statistics.py` if the snapshot is built there, the statistics page under `Frontend/memes-frontend/src/` (find with `grep -rn "contentStats\|statisticsresponse" src`), generated types, `backend_api.md`, `docs/schema.md` if it lists statistics fields
- Test: `Backend/tests/` (diagnostics/statistics tests), `tests/integration/` (repository query), frontend page test

**Interfaces:**
- Produces: response block `comics` with integers `draft`, `built`, `panelsKept`, `panelsRetired`, `builtUnprocessed`.

- [ ] **Step 1: Write the failing integration test** for the repository query: seed one draft comics, one built comics with two kept and one retired panel, one built image with no `ocr_texts` row, one with both rows; assert `draft=1`, `built=2`, `panelsKept`/`panelsRetired` as seeded, `builtUnprocessed=1`. "Unprocessed" = built comics image (by `comics.built_image_id`) with no `ocr_texts` row or no `embeddings` row; if a text-free comics can legitimately have no OCR row, use the OCR job's `image_processing_status` marker (pipeline `easyocr:en`) instead of row absence and state the final definition in the docs.
- [ ] **Step 2: Implement** the query in `diagnostics_repository.py`, add the block to the snapshot payload, add the schema and regenerate TS, Kotlin and Python types (no leftover diff; check for a hand-written response model per the CLAUDE.md gotcha).
- [ ] **Step 3: Frontend:** show a "Comics" block on the statistics page (counts and the unprocessed number) in the style of the neighbouring blocks, with a vitest assertion on the rendered numbers. A snapshot older than this change lacks the block: the page must render without it.
- [ ] **Step 4: Run backend, integration (whole root), frontend checks, `tests/docs/`; expect PASS. Commit** `feat(comics): comics block in statistics`.

### Task 6: Frontend leftovers: preview retry (D11d) and kept-thumbnail fallback (D12d)

**Files:**
- Modify: `Frontend/memes-frontend/src/components/comics/ComicsPreview.tsx`, the built-view panel row in `src/pages/ComicsEditorPage.tsx` (kept panel thumbnail via `sourceImageId`)
- Test: `ComicsEditorPage.test.tsx` and a `ComicsPreview` test

- [ ] **Step 1: Write failing tests:** `shows a Retry button when the preview fails and repeats the request`; `a kept panel whose thumbnail fails to load shows a placeholder, not a broken image` (fire the `error` event on the `img`).
- [ ] **Step 2: Implement** the Retry button (re-runs the preview request, button disabled while loading) and the `onError` placeholder on the kept thumbnail.
- [ ] **Step 3: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`; expect PASS. Commit** `fix(comics): preview retry and kept thumbnail fallback`.

### Task 7: Named schemas for addability and by-image (D11e)

**Files:**
- Create: `shared/schemas/comicsaddabilityitem.schema.json`, `shared/schemas/comicsbyimagecomics.schema.json` (names are the builder's call; keep the repo's lowercase naming)
- Modify: `shared/schemas/comicsaddability.schema.json`, `shared/schemas/comicsbyimage.schema.json` (reference the new schemas), generated TS/Kotlin/Python, `Backend/app/services/comics_service.py` and `Backend/app/api/comics.py` if they build these shapes by hand, Frontend usages if their types change

- [ ] **Step 1:** Extract the inline `items` and `comics` element shapes into named schemas with the same fields (no wire-format change); regenerate all three trees.
- [ ] **Step 2:** Confirm Kotlin no longer types them as `JsonObject`; run `Backend/tests`, frontend checks and `git diff` on generated trees (only the intended files).
- [ ] **Step 3: Commit** `refactor(comics): named schemas for addability and by-image`.

### Task 8: Docs, rollout document, preflight, tracker

**Files:**
- Create: `docs/batches/use-cases/comics-rollout.md` (style of `screenshot-recut-rollout.md`: migrations `d7a3b9c5e214` and `f3a8c1d6b952`, order, verification, rollback, note that the build uses the `/admin/batches` spawn path), `docs/batches/use-cases/comics-pipeline.md` or a section in `comics-build.md` (diagram of the steps)
- Modify: `tools/rollout_preflight.py` (comics tables and the `kept` column in `EXPECTED`, with a test in the existing preflight test file), `docs/batches/inventory.md` (row for `comics_pipeline`: admin-triggerable, started by the build, not scheduled), `CLAUDE.md` batch list (entry `comics_pipeline`; the OCR job's hidden-file skip), `docs/batches/use-cases/comics-build.md` (the after-build list becomes "the build starts `comics_pipeline`"; remove the hidden-file leftover note), `docs/batches/use-cases/screenshot-recut-rollout.md` (runbook: point at the comics rollout), `docs/image-derived-data.md` only if the guard requires it, `docs/superpowers/specs/2026-11-02-multi-image-comics/tracker.md` (C5, D11, D12 closed, status lines), the C5 design status to `done`, memory file `project_comics_epic.md`
- Create: `docs/superpowers/reviews/2026-11-06-c5-operations-review.md` after the final review (controller task, not the implementer's)

- [ ] **Step 1:** Write the documents and registry rows; update the preflight with its test.
- [ ] **Step 2: Run `tests/docs/`, `batch/tests/`, `Backend/tests`, the whole `tests/integration/` root, frontend checks; expect PASS.**
- [ ] **Step 3: Commit** `docs(comics): C5 rollout, inventory and registries`.
