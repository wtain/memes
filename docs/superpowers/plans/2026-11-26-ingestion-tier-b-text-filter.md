# Ingestion Tier B text filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An optional batch job, `ingest_text_filter`, that settles Tier B candidate pairs whose OCR text is clearly different, per pair, reversibly, with a dry run and a plan hash before anything applies.

**Architecture:** One nullable column on `tmp_duplicates` tags auto-kept pairs with their run. A repository module holds the set-based SQL (plan, apply, undo). A thin job module adds settings, the report, the plan hash, run tracking and the CLI. The review queue, progress counts and `ingest_promote` already ignore pairs with `tier_b_reviewed_at` set, so they need no change.

**Tech Stack:** Python 3.11, SQLAlchemy async (raw `text()` SQL, as in `Backend/app/repositories/ingestion_repository.py`), Alembic, pgvector (`<=>`), pytest (integration root against `ocrdb_test`).

**Spec:** `docs/superpowers/specs/2026-11-26-ingestion-tier-b-text-filter-design.md`

## Global Constraints

- Rule thresholds (spec "Rule"): `min_lemmas` 3, `max_overlap` 0.2 (`shared / min(n1, n2)`, strictly below), `veto_distance` 0.10 (cosine distance strictly below vetoes), `sample_size` 100. Settings group `ingestion_text_filter`, every key required; read as `settings.INGESTION_TEXT_FILTER.X`, and the same keys in `environments/settings.yaml` apply to all three environments.
- Pair set is exactly the Tier B review queue's: `tier_b_reviewed_at IS NULL`, `distance >= tier_a_high AND < DUPLICATES.THRESHOLD` (use the same band source `batch/ingest_promote.py` uses), one side `status = 'pending'` with `ingestion_batch_id` = the active ingestion run, other side `status <> 'rejected'`. A pair with both sides pending counts once.
- Never auto-keep a pair whose `distance_source = 'ocr_text'`.
- Only the judged pair gets `tier_b_reviewed_at = now()` and `tier_b_auto_kept_run`. No image status change, no file move, no popularity edge, no metric event.
- Set-based SQL only; no per-pair Python loop (the whole `general` queue is about 50,000 pairs). Avoid correlated subqueries per pair (a measurement query written that way ran over two minutes).
- Apply and undo are shell only; the admin contract `main(trigger, run_id)` is always a dry run.
- No private text (OCR text, filenames) in logs, metrics or stats. The report file may contain it and lives in git-ignored `Storage/ingestion-text-filter/<run id>/`.
- Batch scripts call `config.settings.load_env()` first in `main()`; run-tracked via `batch/run_tracking.py` (`tracked_run` / `finish_existing_run` / `record_stats`); progress via `batch/utils/run_progress.report_progress(label, unit, done, total)` with unit `pairs`.
- Tests: separate pytest invocations per root. Integration needs `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the command line. Use `.venv311` of the main checkout (`H:/workspace_sandbox/memes/.venv311/Scripts/python`); the worktree has none.

## Review Focus

- An image whose lemmas are `stale = true` (an unrefreshed recut cut): its text is not its own, so its pairs stay (Task 2 test).
- Both sides pending in the batch: the pair is evaluated once, not twice, and applied once (Task 2 test).
- A pair already settled by a reviewer, or whose other side was rejected after the dry run: apply never touches it; the plan-hash mismatch refuses apply (Task 3 test).
- A second apply of the same plan, or apply with a wrong hash: no change, non-zero exit, no partial state (Task 3 test).
- Undo after the reviewer already decided the image: undo reopens only pairs still tagged with the run, never one the reviewer settled afterwards (they keep their own `tier_b_reviewed_at`, and the tag is only cleared where `tier_b_auto_kept_run` still equals the run) (Task 3 test).

---

### Task 1: Column, model and migration

**Files:**
- Modify: `Storage/models.py` (`TmpDuplicates`)
- Create: `Storage/alembic/versions/<new rev>_tmp_duplicates_tier_b_auto_kept_run.py` (down_revision `c4f8a1d7e925`, the current head; confirm with `alembic heads` from `Storage/`)
- Test: `tests/integration/test_ingest_text_filter_schema.py`

**Interfaces:**
- Produces: `TmpDuplicates.tier_b_auto_kept_run` (`UUID(as_uuid=True)`, nullable, no FK) and partial index `idx_tmp_duplicates_tier_b_auto_kept_run` `WHERE tier_b_auto_kept_run IS NOT NULL`.

- [ ] **Step 1: Write the failing test** `test_tier_b_auto_kept_run_defaults_to_null_and_round_trips`: create two images and a `TmpDuplicates` row with the factory style used in `tests/integration/test_ingest_promote.py`; assert the new attribute is `None` by default, set it to a `uuid4`, flush, reload, assert equal.
- [ ] **Step 2: Run** `DATABASE_URL=... pytest tests/integration/test_ingest_text_filter_schema.py -v`; expected FAIL (no attribute).
- [ ] **Step 3: Add the column and index to the model; write the migration** with `op.add_column` and `op.create_index(..., postgresql_where=sa.text("tier_b_auto_kept_run IS NOT NULL"))`, plus a `downgrade` that drops both.
- [ ] **Step 4: Run** the test again, plus `cd Storage && alembic heads` shows one head; and `pytest tests/sync/ tests/docs/` (a new column on a class X table should need nothing; if `docs/sync-registry.md` lists columns, run `python -m sync.render --write`). Expected PASS.
- [ ] **Step 5: Commit** `feat(ingestion): tmp_duplicates.tier_b_auto_kept_run column`.

### Task 2: Repository: plan, apply, undo

**Files:**
- Create: `repository/ingestion_text_filter.py`
- Test: `tests/integration/test_ingestion_text_filter_repository.py`

**Interfaces:**
- Consumes: `TmpDuplicates.tier_b_auto_kept_run` (Task 1); tables `tmp_duplicates`, `images`, `ocr_lemmas` (with `stale`), `ocr_text_embeddings`.
- Produces:
  - `@dataclass(frozen=True) FilterParams(min_lemmas: int, max_overlap: float, veto_distance: float)`
  - `@dataclass(frozen=True) PlannedPair(pair_id: UUID, image_id1: UUID, image_id2: UUID, distance: float, overlap: float | None, text_distance: float | None, bucket: str)`; `bucket` is one of `"eligible"`, `"vetoed"`, `"no_text"`, `"overlap_high"`, `"ocr_text_source"`, `"stale_text"`.
  - `async def plan_pairs(session, batch_id, band: tuple[float, float], params: FilterParams) -> list[PlannedPair]` returns every open Tier B pair with its bucket (the counts and samples come from this; only `"eligible"` is ever applied).
  - `async def apply_pairs(session, run_id: UUID, pair_ids: list[UUID]) -> int` sets `tier_b_reviewed_at = now()` and `tier_b_auto_kept_run = run_id` on those rows where `tier_b_reviewed_at IS NULL`, returns the count; never commits.
  - `async def undo_run(session, run_id: UUID) -> int` sets both columns back to NULL where `tier_b_auto_kept_run = run_id`, returns the count; never commits.

- [ ] **Step 1: Write failing integration tests** (one assertion focus each, helper builds images, lemmas and pairs): `test_clearly_different_text_is_eligible` (3+ lemmas each, 0 shared, bucket `eligible`, overlap 0.0); `test_overlap_at_threshold_is_not_eligible` (shared/min = 0.2 exactly gives `overlap_high`); `test_fewer_than_min_lemmas_is_no_text`; `test_embedding_within_veto_distance_is_vetoed` (both embeddings, distance below 0.10 gives `vetoed`; one side without an embedding is still eligible); `test_ocr_text_sourced_pair_is_never_eligible`; `test_stale_lemmas_keep_the_pair` (`bucket == "stale_text"`); `test_pair_with_both_sides_pending_is_planned_once`; `test_settled_rejected_and_out_of_band_pairs_are_not_planned`; `test_apply_marks_only_given_pairs_and_tags_run`; `test_undo_reopens_only_pairs_still_tagged_with_that_run` (a pair re-reviewed by a person afterwards, `tier_b_auto_kept_run` set to a different run or `tier_b_reviewed_at` changed, stays settled).
- [ ] **Step 2: Run** them; expected FAIL (module missing).
- [ ] **Step 3: Implement** the three functions with raw `text()` SQL. `plan_pairs`: one statement that builds the pair set (UNION ALL of both directions de-duplicated to one row per `tmp_duplicates.id`), joins a per-image lemma-count CTE (`ocr_lemmas` grouped by `image_id`, counting only `stale = false`, and flagging images with any stale row), a shared-lemma count by joining `ocr_lemmas` on the pair's two image ids and equal `lemma` grouped by pair id, and `LEFT JOIN ocr_text_embeddings` twice with `ea.embedding <=> eb.embedding`; the bucket is a `CASE` in the precedence `ocr_text_source`, `stale_text`, `no_text`, `vetoed`, `overlap_high`, `eligible`. Set `SET LOCAL work_mem = '256MB'` as the neighbouring repository does.
- [ ] **Step 4: Run** the tests; expected all PASS.
- [ ] **Step 5: Commit** `feat(ingestion): text filter repository (plan, apply, undo)`.

### Task 3: The job: settings, report, plan hash, apply, undo, CLI

**Files:**
- Create: `batch/ingest_text_filter.py`
- Modify: `environments/settings.yaml` (new `ingestion_text_filter:` group with the four keys), `.gitignore` (`Storage/ingestion-text-filter/`)
- Test: `tests/integration/test_ingest_text_filter_job.py`

**Interfaces:**
- Consumes: `plan_pairs`, `apply_pairs`, `undo_run`, `FilterParams`, `PlannedPair` (Task 2); `tracked_run`, `finish_existing_run`, `record_stats`.
- Produces:
  - `def plan_hash(pair_ids: list[UUID], params: FilterParams) -> str`: sha256 hex of the sorted eligible pair ids joined with the three param values.
  - `async def run_dry(run_id: UUID) -> str` writes the report under `Storage/ingestion-text-filter/<run_id>/` (`report.json`: counts per bucket, `plan_hash`, params; `sample.md`: `sample_size` random eligible pairs and `sample_size` random `overlap_high` pairs with overlap between 0.2 and 0.3, each with both filenames, both OCR texts via the existing OCR text query used by the review page, CLIP distance, overlap, text distance), records stats `pairs_open`, `pairs_eligible`, `pairs_vetoed`, `pairs_no_text`, `pairs_applied` (0), returns the plan hash.
  - `async def run_apply(plan_hash_arg: str) -> int` recomputes the plan in one transaction, raises `PlanMismatch` (new `Exception` subclass) when the hash differs, otherwise applies the eligible ids and commits, stats `pairs_applied`.
  - `async def run_undo(undo_run_id: UUID) -> int`.
  - `async def main(trigger: str = "manual", run_id: UUID | None = None) -> None` is always the dry run, same shape as `batch/ingest_promote.py`'s `main`, kind `ingest_text_filter`; raises a clear error when no ingestion run is active.
  - CLI: `--env`, `--apply --plan-hash H`, `--undo --run-id R`; apply and undo are also run-tracked under kind `ingest_text_filter`; `PlanMismatch` exits non-zero with a message and changes nothing.

- [ ] **Step 1: Write failing integration tests**: `test_dry_run_changes_nothing_and_writes_report` (no `tier_b_reviewed_at` set, `report.json` has the hash and counts, run stats recorded, no OCR text in the stats); `test_apply_with_matching_hash_settles_only_eligible_pairs`; `test_apply_with_wrong_hash_changes_nothing` and `test_apply_refuses_when_pair_set_changed_since_dry_run` (a pair settled by a reviewer after the dry run gives `PlanMismatch`); `test_second_apply_of_same_plan_is_refused`; `test_undo_restores_the_queue`; `test_main_fails_visibly_without_active_ingestion_run`.
- [ ] **Step 2: Run** them; expected FAIL.
- [ ] **Step 3: Implement** the module; resolve the batch id and band as `batch/ingest_promote.py` does; report written with `pathlib`, UTF-8; progress reported per bucket pass.
- [ ] **Step 4: Run** the new tests; expected PASS.
- [ ] **Step 5: Commit** `feat(ingestion): ingest_text_filter job`.

### Task 4: Queue effects, registration and docs final pass

**Files:**
- Modify: `environments/batch_registry.yaml` (entry `ingest_text_filter`: module `batch.ingest_text_filter`, kind `ingest_text_filter`, between `ingest_find_duplicates_tier_b` and `ingest_promote`), `CLAUDE.md` (drop "(planned; ...)" and keep the entry), `docs/batches/inventory.md` row (unchanged text, confirm accurate), `docs/superpowers/specs/2026-11-26-ingestion-tier-b-text-filter-design.md` (status `done`)
- Test: `tests/integration/test_ingest_text_filter_queue.py`

**Interfaces:**
- Consumes: `apply_pairs` (Task 2); `IngestionService.list_tier_b_review`, `IngestionRepository.get_review_progress`, `batch.ingest_promote.get_promotable_ids`.

- [ ] **Step 1: Write failing tests**: `test_auto_kept_pair_disappears_from_tier_b_page_and_progress` (before: subject listed with the candidate and counted in `tier_remaining`; after `apply_pairs`: candidate gone, subject gone when it was its only pair, count lower); `test_image_with_only_auto_kept_pairs_becomes_promotable`; `test_image_with_one_kept_and_one_open_pair_is_not_promotable`.
- [ ] **Step 2: Run** them; they should PASS without code changes (the spec's claim); if one fails, fix the cause in the queue query, not the test.
- [ ] **Step 3: Register the job**, check the admin batch list lists it (the admin trigger endpoint resolves kinds from `environments/batch_registry.yaml`; run the Backend tests that cover the registry), update the docs notes above.
- [ ] **Step 4: Verify the whole change:** `cd Backend && pytest`; `pytest tests/docs/`; `pytest tests/sync/`; `pytest batch/tests/`; and the **entire** integration root `DATABASE_URL=... pytest tests/integration/ -v` (shared `ocr_lemmas` consumers, per CLAUDE.md). Expected all PASS.
- [ ] **Step 5: Commit** `feat(ingestion): register ingest_text_filter, queue-effect tests, docs`.

### Task 5: Dry run against `general` (controller only, read-only)

- [ ] **Step 1:** With `DATABASE_URL_READONLY` only, run the plan query (not apply) against `general` and compare the bucket counts with the earlier measurement (about 33,450 pairs with no shared lemma, about 2,100 images with a pair at 0.2 or more). Report the counts and the first `sample.md` to the owner. No apply, no write to any live database, no restart of the three backends; the owner restarts them and decides on `--apply`.

---

Self-review: spec coverage: rule conditions 1-5 (Task 2 tests), per-pair settling and tag (Tasks 1-2), dry run, report, sample, hash (Task 3), apply/undo shell only and admin dry run (Task 3, `main`), settings on all environments (Task 3), queue and promote effects (Task 4), docs and registry (Task 4), no endpoint change so `backend_api.md` untouched. Types: `FilterParams`, `PlannedPair`, `plan_pairs`, `apply_pairs`, `undo_run`, `plan_hash`, `run_dry`, `run_apply`, `run_undo`, `PlanMismatch` are defined once and used consistently.
