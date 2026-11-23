# Batch Progress Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A running batch reports how far it is (done of total, rate, step of steps) onto its `batch_runs` row, and the Admin page shows it.

**Architecture:** A nullable JSONB column `batch_runs.progress` is written by a best-effort, throttled, fire-and-forget function (`report_progress` / `report_step`) that finds its run through a `ContextVar` set by `tracked_run` / `finish_existing_run`. `ProgressTracker` calls it automatically, drivers call `report_step`, and the runs endpoint returns `progress` for `BatchRow` to draw.

**Tech Stack:** Python 3.11 (asyncio, SQLAlchemy async, Alembic, FastAPI, Pydantic), React + TypeScript (vitest), shared JSON schemas with generated TypeScript, Kotlin and Python types.

**Spec:** `docs/superpowers/specs/2026-11-23-batch-progress-reporting-design.md`

## Global Constraints

- Work in the worktree `.claude/worktrees/batch-progress-spec` (branch `worktree-batch-progress-spec`); the always-on `general` ingestion runs on `main`. Never bind ports 8081-8083 / 5173-5175 or touch the live databases. Tests use `ocrdb_test` (`DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`).
- Progress is best effort (D3): a failed or slow write never fails, slows or blocks a job; exceptions are swallowed and logged without data.
- No private text (D6): stored values are integers, a `label` from the code (script or step name, never derived from data) and a `unit` from the closed list `images`, `pairs`, `descriptions`, `profiles`, `files`, `items`. An unknown `unit` is stored as `items`; a missing label falls back to `"batch"`.
- Throttle: one write per `batch_progress.min_interval_seconds` (10) per run; the first report, a report with `done == total` and a step change always pass; at most one write in flight per run, latest value wins.
- The write is one `UPDATE batch_runs SET progress = ... WHERE run_id = :id AND status = 'started'` on its own session; `updated_at` is the server's `now()`.
- `progress` keeps its last value when the run ends; `rate_per_min` is stored, the ETA is computed by the UI.
- `done` is clamped to `total` when both are set; `total` is `null` when unknown.
- Config: `batch_progress.enabled: true` and `batch_progress.min_interval_seconds: 10` in `environments/settings.yaml`, every key required. The stall threshold is NOT a setting: it is a constant of the web page, `STALE_AFTER_MS = 300_000` (resolves the spec's open choice; amend the spec's Configuration section in Task 8).
- Console output of `ProgressTracker` does not change.
- Never combine test roots in one `pytest` invocation (`Backend/`, `tests/integration/`, `batch/tests/`, `tests/docs/`, `tests/sync/` run separately). Changes to `repository/` and `run_tracking` need the whole `tests/integration/` root run.
- Windows: set `PYTHONIOENCODING=utf-8` for any interactive batch run; use `.venv311`.
- New route: none. The Admin routes already carry a `ROUTE_POLICY` verdict.

## Review Focus

- A script run with no tracked run, or in a sync caller with no running event loop: `report_progress` is a silent no-op (Task 2).
- A late progress write arriving after the run completed or failed must not change the row (Task 1, Task 3).
- `total == 0`, `total == null` and `done > total` must never produce NaN, Infinity or >100% in the UI (Task 8).
- Database not migrated yet (column missing): `BatchRun.progress` is mapped `deferred` (only `get_run`/`list_runs` undefer it), so run tracking keeps working; the progress writes fail and are swallowed, the job continues, and the Admin runs list fails until `alembic upgrade head`. Migrate each live environment (metal, general, it; prod when its code ships) BEFORE merging into the main checkout, because the dev backends reload at merge (final-review fix F1).
- Nested self-tracked children: a child's tracker writes to the child's row and the driver's row afterwards still gets its own step (Task 3).
- Event loop ends with a write pending: the exit drain delivers the last report and leaves no pending task (Task 3).

---

### Task 1: Column, model and repository write

**Files:**
- Create: `Storage/alembic/versions/<rev>_batch_runs_progress.py` (autogenerate, then trim to the one column; `down_revision` = the current head, find it with `alembic heads` from `Storage/`)
- Modify: `Storage/models.py` (class `BatchRun`, ~line 995), `repository/batch_runs.py`
- Test: `tests/integration/test_batch_runs_repository.py` (extend; whole `tests/integration/` root must pass)

**Interfaces:**
- Produces: `BatchRun.progress: Mapped[dict | None]` (JSONB, nullable, no default);
  `BatchRunRepository.set_progress(self, run_id: uuid.UUID, value: dict) -> bool` – returns `True` when a row in status `started` was updated, `False` otherwise. It sets `progress = value || {"updated_at": now()}` in one UPDATE (no read-modify-write) and does not commit.

- [ ] **Step 1: Write failing tests** in `tests/integration/test_batch_runs_repository.py`: `test_set_progress_writes_value_and_server_updated_at` (create a run, `set_progress(run_id, {"done": 5, "total": 10})` returns `True`, reloaded `progress["done"] == 5` and `progress["updated_at"]` is a parseable ISO timestamp), `test_set_progress_overwrites_whole_value` (second call with a different dict leaves no key of the first), `test_set_progress_ignores_finished_run` (after `commit(run_id)` and after `fail(run_id)` it returns `False` and `progress` is unchanged), `test_set_progress_does_not_touch_stats` (stats set by `update_stats` survive).
- [ ] **Step 2: Run** `DATABASE_URL=... pytest tests/integration/test_batch_runs_repository.py -v` – expect failures (`set_progress` and column missing).
- [ ] **Step 3: Add** the model column, the migration (`op.add_column("batch_runs", sa.Column("progress", postgresql.JSONB(), nullable=True))`, downgrade drops it) and `set_progress` (SQLAlchemy `update(BatchRun)` with `.values(progress=cast(value, JSONB).op("||")(func.jsonb_build_object("updated_at", func.now())))`).
- [ ] **Step 4: Apply the migration to the test database** (`alembic upgrade head` from `Storage/` with the test `DATABASE_URL`), run the tests again – expect PASS, then run the whole `tests/integration/` root.
- [ ] **Step 5: Sync registry check:** `pytest tests/sync/ -q` and `pytest tests/docs/ -q` (separate commands) – expect PASS; if a guard asks for the column, add it where it says.
- [ ] **Step 6: Commit** `feat(batch): batch_runs.progress column and set_progress`.

---

### Task 2: The reporting module

**Files:**
- Create: `batch/utils/run_progress.py`
- Modify: `environments/settings.yaml` (new `batch_progress` block with a comment pointing at the spec)
- Test: `batch/tests/test_run_progress.py`

**Interfaces:**
- Consumes: `BatchRunRepository.set_progress` (Task 1), `Storage.db.AsyncSessionLocal`, `settings.BATCH_PROGRESS.ENABLED`, `settings.BATCH_PROGRESS.MIN_INTERVAL_SECONDS`.
- Produces (all in `batch/utils/run_progress.py`):
  - `PROGRESS_UNITS: frozenset[str]` (the six units).
  - `current_run_id: ContextVar[uuid.UUID | None]` (default `None`).
  - `report_progress(label: str, unit: str, done: int, total: int | None, rate_per_min: float | None = None) -> None` – sync, never raises.
  - `report_step(index: int, count: int, label: str) -> None` – sync, never raises; bypasses the throttle.
  - `async drain_progress(run_id: uuid.UUID, timeout: float = 5.0) -> None` – awaits the run's in-flight write (and one final write of the latest unwritten value), then forgets the run's state.
  - `build_value(label, unit, done, total, rate_per_min, step) -> dict` – pure: validates `unit` against `PROGRESS_UNITS` (fallback `items`), clamps `done` to `total`, keeps `total` `None` when unknown, drops negative numbers to 0.
  - `async write_progress(run_id: uuid.UUID, value: dict) -> None` – opens its own `AsyncSessionLocal`, calls `set_progress`, commits; the single place a test replaces (module attribute `_writer`).
- State: per run id the module remembers the latest step, the latest progress value, the last write time and the in-flight task, and writes progress and step together in one value (a driver's `step` and a child-style `done/total` on the same row coexist; step-only values have `label` = step label, `unit` `items`, `done` and `total` `None`).

- [ ] **Step 1: Write failing tests** (no database; replace `_writer` with a recording fake; use `asyncio` tests per `batch/tests` convention, `asyncio_mode` STRICT so mark with `@pytest.mark.asyncio`): `test_noop_without_current_run`, `test_noop_without_running_loop` (call from a plain sync test), `test_noop_when_disabled`, `test_first_and_final_reports_pass_throttle` (reports at `done` 1, 2, 3 inside the interval write once; `done == total` writes), `test_latest_wins_while_in_flight` (writer blocked on an event; three reports; after release exactly two writes, the second carrying the last `done`), `test_writer_exception_is_swallowed` (writer raises, the caller sees nothing, the next report still writes), `test_unknown_unit_becomes_items`, `test_done_clamped_to_total`, `test_total_none_kept` and `test_total_zero_kept`, `test_step_bypasses_throttle_and_merges_with_progress`, `test_drain_delivers_last_value_and_clears_state`, `test_label_and_values_contain_only_allowed_keys` (the written dict's keys are a subset of `label, unit, done, total, rate_per_min, step`).
- [ ] **Step 2: Run** `pytest batch/tests/test_run_progress.py -v` – expect FAIL (module missing).
- [ ] **Step 3: Implement** the module as specified; schedule writes with `asyncio.get_running_loop().create_task` (catch `RuntimeError` → no-op), keep strong references to tasks, use `time.monotonic` for throttling. Add the settings block (`enabled: true`, `min_interval_seconds: 10`).
- [ ] **Step 4: Run** the tests – expect PASS. Also run `pytest batch/tests/test_env_loading.py -q` to confirm settings still load.
- [ ] **Step 5: Commit** `feat(batch): run_progress module (report_progress, report_step, drain)`.

---

### Task 3: Wire runs to the module

**Files:**
- Modify: `batch/run_tracking.py` (`tracked_run`, `finish_existing_run`)
- Test: `tests/integration/test_run_progress_tracking.py` (new)

**Interfaces:**
- Consumes: `current_run_id`, `drain_progress`, `write_progress` (Task 2); `BatchRunRepository` (Task 1).
- Produces: inside `tracked_run(...)` and `finish_existing_run(run_id)` bodies `current_run_id.get()` returns that run's id; on exit it is reset to its previous value (token reset in `finally`); before the final `commit`/`fail` the run's pending write is drained (`await drain_progress(run_id)`).

- [ ] **Step 1: Write failing integration tests** (test database): `test_tracked_run_sets_and_resets_current_run_id`, `test_nested_tracked_runs_use_their_own_id` (driver run kind A, child run kind B inside it: inside the child `current_run_id.get()` is B's id, after the child it is A's again), `test_report_progress_lands_on_the_run_row` (inside `tracked_run`, `report_progress("x", "images", 3, 10)` then leaving the block: reloaded run has `progress["done"] == 3`, status `completed`), `test_late_write_after_completion_does_not_change_row` (call `write_progress(run_id, {...})` after the block: row unchanged), `test_failing_run_still_drains_and_fails` (exception inside the block: run `failed`, progress delivered), `test_finish_existing_run_sets_current_run_id`.
- [ ] **Step 2: Run** `DATABASE_URL=... pytest tests/integration/test_run_progress_tracking.py -v` – expect FAIL.
- [ ] **Step 3: Implement** the contextvar set/reset and the drain in both context managers; the drain must not raise (timeout swallowed).
- [ ] **Step 4: Run** the new tests, then the whole `tests/integration/` root and `pytest batch/tests/ -q` (separately) – expect PASS.
- [ ] **Step 5: Commit** `feat(batch): tracked runs expose their id to progress reporting`.

---

### Task 4: `ProgressTracker` reports and the twelve scripts pass a unit

**Files:**
- Modify: `batch/utils/progress.py`; the 12 call sites: `batch/build_description_lemmas.py:29`, `build_description_note_embeddings.py:27`, `build_description_note_lemmas.py:25`, `build_image_descriptions.py:188`, `build_image_description_embeddings.py:34`, `build_image_embeddings.py:74`, `build_meme_profiles.py:134`, `build_ocr_lemmas.py:62`, `build_ocr_text_embeddings.py:44`, `build_tags_from_ocr.py:87`, `classify_text_heavy.py:30`, `extract_text_from_memes.py:248`
- Test: `batch/tests/test_progress_tracker.py` (new)

**Interfaces:**
- Consumes: `report_progress`, `current_run_id` (Task 2/3).
- Produces: `ProgressTracker.__init__(self, total: int, report_every: int = 10, report_interval_secs: float = 0.0, window_secs: float = 120.0, label: str | None = None, unit: str = "items")`. `label` defaults to `None`, stored as `"batch"` (the `report_progress` fallback). `mark_done` and `skip` call `report_progress(label, unit, done, effective_total, rate_per_min)` where `rate_per_min = 60 / avg_seconds_per_item` from the existing windowed average (`None` before one sample); `summary()` reports once more with `done` and the final total.

- [ ] **Step 1: Write failing tests**: `test_console_output_unchanged` (capsys: same lines as before for a fixed fake clock; copy one expectation from the current format `[done/~effective] elapsed=...  avg=...s/img  eta≈...`), `test_mark_done_reports_done_and_effective_total` (monkeypatch `report_progress` in `batch.utils.progress`; after `skip()` the reported total drops by one), `test_rate_per_min_from_window` (fake `time.perf_counter`: items every 3 s → `rate_per_min == 20.0`), `test_no_rate_before_first_sample`, `test_summary_reports_final`, `test_tracker_without_current_run_is_unaffected` (no monkeypatch; no exception, no output change).
- [ ] **Step 2: Run** `pytest batch/tests/test_progress_tracker.py -v` – expect FAIL.
- [ ] **Step 3: Implement** the tracker change; then at each call site pass `unit=` (images for OCR, embeddings, lemmas, text-heavy, tags; `descriptions` for the description jobs whose tracker counts descriptions (`build_image_descriptions` counts images, so `images`); `profiles` for `build_meme_profiles`; `items` where unsure) and a `label` equal to the script name.
- [ ] **Step 4: Run** `pytest batch/tests/ -q` – expect PASS (existing `*_main` tests must still pass), and `pytest tests/rules/ -q` separately.
- [ ] **Step 5: Commit** `feat(batch): ProgressTracker reports progress to the run row`.

---

### Task 5: Drivers report steps

**Files:**
- Modify: `batch/ingest_auto_prep.py` (`_run_prep_chain`), and the step loops of `batch/description_pipeline.py`, `batch/recut_pipeline.py`, `batch/comics_pipeline.py`
- Test: `batch/tests/test_comics_pipeline.py`, `batch/tests/test_description_pipeline.py` (extend), new `batch/tests/test_ingest_auto_prep_steps.py`, and the recut pipeline's existing test file (find with `ls batch/tests | grep recut_pipeline`; create `test_recut_pipeline_steps.py` if none)

**Interfaces:**
- Consumes: `report_step(index, count, label)` (Task 2).
- Produces: each driver calls `report_step(i, n, <step name>)` immediately before running step `i` of `n` (1-based). For `ingest_auto_prep` the eight steps are, in order: `ingest_hash_dedup`, `ingest_validate_formats`, `build_image_embeddings`, `extract_text_from_memes`, `build_ocr_lemmas`, `classify_text_heavy`, `build_ocr_text_embeddings`, `ingest_find_duplicates` (the hash dedup is the first step, currently called before the `steps` list; fold it into the numbering). For the other three drivers use their existing step lists' names and count.

- [ ] **Step 1: Write failing tests** per driver with `report_step` monkeypatched: `test_reports_each_step_before_running_it` (recorded calls interleave with fake step bodies: report i then body i), `test_skipped_step_still_reports` (a step skipped for a documented reason, e.g. `skipped_not_configured`, still counts toward `count`), and for `ingest_auto_prep` `test_step_numbers_cover_eight_steps_in_documented_order`.
- [ ] **Step 2: Run** the new tests – expect FAIL.
- [ ] **Step 3: Implement** the calls; do not change any driver's failure, skip or chaining behaviour.
- [ ] **Step 4: Run** `pytest batch/tests/ -q` – expect PASS.
- [ ] **Step 5: Commit** `feat(batch): drivers report their step`.

---

### Task 6: Direct reporting in long loops without a tracker

**Files:**
- Modify (audit each, add `report_progress` where a loop over thousands of items has none): `batch/ingest_hash_dedup.py`, `batch/ingest_validate_formats.py`, `batch/ingest_find_duplicates.py`, `batch/rebuild_duplicates.py`, `batch/clusterize.py`, `batch/create_recut_cuts.py`, `batch/detect_recut_crops.py`, `batch/detect_comics_candidates.py`
- Test: `batch/tests/test_progress_direct_reporting.py` (new) plus the existing `*_main` tests of those scripts

**Interfaces:**
- Consumes: `report_progress(label, unit, done, total, rate_per_min=None)` (Task 2).
- Produces: for each script, a call at its main per-item or per-batch loop with `label` = script name, `unit` = `files` (hash dedup, validate), `images` (recut jobs, comics detector), `pairs` (duplicates, clusterize). Scripts whose work is a single SQL statement with no loop (verify while auditing) get no call and are listed in the commit message as "no loop".
- `ingest_hash_dedup`, `ingest_validate_formats` and `ingest_find_duplicates` are not run-tracked themselves; when called from `ingest_auto_prep` the progress lands on the driver's row (its `step` is kept, Task 2), and when run from the shell there is no current run and the call is a no-op.

- [ ] **Step 1: Audit** each script (read its main loop); write a one-line note per script in the commit message: loop found or "no loop".
- [ ] **Step 2: Write failing tests**: for each script with a loop, `test_<script>_reports_progress` – run its main loop function against its existing test fakes (see the `*_main` tests) with `report_progress` monkeypatched in the script's module; assert at least one call with `done <= total`, the expected `label` and `unit`, and a final call with `done == total`. Also `test_no_call_changes_return_values` is covered by the existing tests staying green.
- [ ] **Step 3: Run** the new tests – expect FAIL.
- [ ] **Step 4: Implement** the calls (throttling is the module's job; call per item or per small chunk, not per SQL row of a bulk insert).
- [ ] **Step 5: Run** `pytest batch/tests/ -q`, then the whole `tests/integration/` root – expect PASS.
- [ ] **Step 6: Commit** `feat(batch): report progress from long loops without a tracker`.

---

### Task 7: API field and generated types

**Files:**
- Create: `shared/schemas/batchprogress.schema.json` (object `BatchProgress`)
- Modify: `shared/schemas/runstatusresponse.schema.json`, `shared/schemas/all.schema.json` (register `BatchProgress`), `Backend/app/services/admin_batch_service.py` (`_to_response`), `backend_api.md` (batch run section)
- Regenerate: `Frontend/memes-frontend/src/types/generated/all.d.ts`, `Backend/app/types/generated/` (`batchprogress.py`, `runstatusresponse.py`, `__init__.py`), `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt` (via `AndroidClient/scripts/generate_dtos.py`)
- Test: `Backend/tests/test_admin_batch_service.py`, `Backend/tests/test_admin_batch_endpoints.py`

**Interfaces:**
- Produces: `BatchProgress` = `{label: string|null, unit: string|null, done: integer|null, total: integer|null, rate_per_min: number|null, step: {index: integer, count: integer, label: string}|null, updated_at: date-time}`, all properties listed and nullable except `updated_at` (this also loosens the spec's field table: a step-only value has no `done`; amend the spec in Task 9); `RunStatusResponse` gains required-nullable `progress: BatchProgress | null`. `_to_response` returns `"progress": run.progress` (the JSONB dict or `None`).

- [ ] **Step 1: Write failing tests**: service test `test_to_response_includes_progress` (a fake run with `progress` dict and one with `None`), endpoint tests `test_get_run_returns_progress` and `test_list_runs_progress_null_for_untracked` (response validates against the generated model; `progress` key always present).
- [ ] **Step 2: Run** `cd Backend && pytest tests/test_admin_batch_service.py tests/test_admin_batch_endpoints.py -v` – expect FAIL.
- [ ] **Step 3: Edit** the two schemas and `all.schema.json`; regenerate all three trees with the commands in `documents/generation.md` and CLAUDE.md "Type generation" (Backend command from `Backend/`); implement `_to_response`; update `backend_api.md` (the `RunStatusResponse` section: new `progress` field, example, null cases).
- [ ] **Step 4: Run** `cd Backend && pytest -q` – expect PASS; from `Frontend/memes-frontend/` run `bash ../generate-types.sh` and confirm `git diff` of the generated types shows only the intended change.
- [ ] **Step 5: Commit** `feat(api): RunStatusResponse.progress and generated types`.

---

### Task 8: Admin page

**Files:**
- Create: `Frontend/memes-frontend/src/pages/batchProgress.ts` (pure helpers)
- Modify: `Frontend/memes-frontend/src/pages/AdminBatchesPage.tsx` (`BatchRow`, runs table)
- Test: `Frontend/memes-frontend/src/pages/batchProgress.test.ts` (new), `AdminBatchesPage.test.tsx` (extend)

**Interfaces:**
- Consumes: `RunStatusResponse.progress` (Task 7).
- Produces in `batchProgress.ts`:
  - `export const STALE_AFTER_MS = 300_000`
  - `export function progressFraction(p: {done: number|null; total: number|null}): number | null` – `null` when `total` is null/0 or `done` null; otherwise `done/total` clamped to `[0, 1]`.
  - `export function formatEta(p: {done: number|null; total: number|null; rate_per_min: number|null}): string | null` – `null` when rate is null/≤0 or total null or `done >= total`; else "about 6 h left" / "about 12 min left" / "under 1 min left".
  - `export function isStalled(updatedAt: string, now: number, status: string): boolean` – true only for `status === "running"` and `now - Date.parse(updatedAt) > STALE_AFTER_MS`.
  - `export function describeProgress(p, now): {bar: number | null; counts: string; eta: string | null; step: string | null; updated: string; stalled: boolean}`.
- `BatchRow` renders, for a `running` latest run with `progress`: the bar (only when `bar !== null`), `counts` ("5,203 / 12,336 images"), eta, `step` ("step 2 of 8: extract_text_from_memes"), "updated 20 s ago", amber text when stalled. The runs table gets a "Progress" column with `done/total` (or "—").

- [ ] **Step 1: Write failing tests** in `batchProgress.test.ts`: `progressFraction` for `(5203, 12336)`, `(0, 0)` → `null`, `(5, null)` → `null`, `(12, 10)` → `1`; `formatEta` for 19.7/min with 7,133 left → "about 6 h left", rate `null` → `null`, done ≥ total → `null`; `isStalled` with faked `now` (fresh → false, 6 min old → true, status `completed` → false); `describeProgress` produces no `NaN`/`Infinity` in any string for `total` 0, `total` null and `done > total`. In `AdminBatchesPage.test.tsx`: `renders progress bar and step line for a running run`, `renders no progress for a run whose progress is null`, `shows the stalled hint`, `keeps the final figure in the runs table for a completed run`.
- [ ] **Step 2: Run** `vitest run src/pages` – expect FAIL.
- [ ] **Step 3: Implement** the helpers and the row/table changes (Tailwind classes consistent with the page; the page already polls while a run is `running`, do not add timers beyond a one-second-resolution re-render if "updated … ago" needs it; React Compiler lint rules apply, no refs in render).
- [ ] **Step 4: Run** from `Frontend/memes-frontend/`: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run` – expect all green.
- [ ] **Step 5: Commit** `feat(web): progress bar, step line and stalled hint on the Admin page`.

---

### Task 9: Docs, manual check and spec status

**Files:**
- Modify: `docs/batches/execution-model.md` (new "Progress reporting" section), `docs/batches/inventory.md` (a "reports progress" note per adopted script, or one legend line), `CLAUDE.md` (Batch pipeline section: one paragraph: how a new batch reports progress, `ProgressTracker(unit=...)`, direct `report_progress`, `report_step` for drivers), `docs/superpowers/specs/2026-11-23-batch-progress-reporting-design.md` (Status → `planned`, `Plan:` link, amend: stall threshold is a web-page constant not a setting; `BatchProgress` fields nullable for step-only values), `docs/observability.md` only if its privacy rules need a pointer
- Test: manual check script in the scratchpad (not committed)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Manual check** against the test database only: a throwaway script uses `tracked_run(kind="progress_check", ...)`, loops 40 items with `ProgressTracker(40, unit="items")` sleeping 1 s, and meanwhile `GET /api/admin/batches/runs` of a test backend (a port outside 8081-8083 and 5173-5175, see `environments/Environments.md`) must show a moving `progress`; confirm in a browser that the Admin row shows the bar, ETA and "updated … ago", and that killing the script turns the hint amber after 5 minutes. Record the result in the commit message.
- [ ] **Step 2: Docs:** write the sections above; run `pytest tests/docs/ -q` – expect PASS.
- [ ] **Step 3: Whole-branch verification:** `cd Backend && pytest -q`; `pytest batch/tests/ -q`; `DATABASE_URL=... pytest tests/integration/ -q`; `pytest tests/rules/ -q`; `pytest tests/docs/ -q`; `pytest tests/sync/ -q`; frontend `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`. Every command run separately, all green.
- [ ] **Step 4: Commit** `docs: batch progress reporting (execution model, inventory, CLAUDE.md, spec status)`.
- [ ] **Step 5: Roll-out note for the owner (in the final summary, not a task):** run the migration on each live environment first (`alembic upgrade head`, BEFORE merging into the main checkout, since the `--reload` backends pick up the new code at merge), then restart the three backends; jobs already running keep running without progress until restarted, and the live `general` ingestion will not report (it started before this code).
