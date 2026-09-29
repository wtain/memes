# Precomputed Statistics Snapshot

status: done
Plan: `docs/superpowers/plans/2026-09-29-precomputed-statistics.md`

## Problem

`GET /api/diagnostics/statistics` (`Backend/app/api/diagnostics.py`) runs
`DiagnosticsRepository.get_statistics()` on every request: one `SELECT` with ~28 scalar
subqueries, several of them `COUNT(DISTINCT)` over joins to `images` and `NOT EXISTS`
anti-joins across `ocr_texts`, `image_classifications`, `ocr_text_embeddings` and
`ocr_lemmas`. On a real corpus this is slow, so the statistics page loads after a visible
delay. The numbers only need to be roughly current, not live.

## Decisions

- **Freshness target: hourly is fine.** Confirmed with the user 2026-09-29.
- **No per-mutation invalidation.** Several inputs change with no batch involved (`flagged`,
  description feedback, ingestion promote/reject flipping `images.status`, uploads).
  Hooking every mutation site is fragile and easy to forget in new code; the scheduled
  refresh is the correctness backstop instead.
- **Metrics unchanged.** This spec changes when statistics are computed, not what is counted.

## Design

### 1. Storage

New table `statistics_snapshots`, model in `Storage/models.py`, Alembic migration under
`Storage/`:

| column        | type                 | notes                                   |
|---------------|----------------------|-----------------------------------------|
| `name`        | text, primary key    | only `'corpus'` used today              |
| `payload`     | JSONB, not null      | the full `StatisticsResponse` body      |
| `computed_at` | timestamptz, not null|                                         |
| `duration_ms` | integer, not null    | compute time, for spotting regressions  |

One row per name, upserted (`INSERT ... ON CONFLICT (name) DO UPDATE`). The migration must
be applied to the metal, general and IT databases.

### 2. Compute

- Keep the existing query in `DiagnosticsRepository.get_statistics()`; extract the
  row-to-`StatisticsResponse` mapping currently inline in the router into a shared function
  so the batch script and the fallback path build identical payloads.
- New `batch/build_statistics.py`, following the standard batch-script shape
  (`main(trigger=, run_id=)`, `BatchRun` tracking with `kind="statistics"`, repository
  pattern, no `commit()` inside repositories): compute, upsert the `'corpus'` row, record
  the run. Idempotent; safe to re-run at any time.
- New repository methods (global `repository/` layer): `upsert(name, payload, computed_at,
  duration_ms)` and `get(name)` (the service stamps `computed_at`). Repositories do not
  commit.

### 3. Triggers

1. **Hourly schedule.** New `scheduler.jobs` entry in `environments/settings.yaml`
   (`name: build_statistics`, `script: build_statistics`, `batch_run_kind: statistics`,
   `interval_minutes: 60`, `max_runtime_minutes: 10`). Reuses the existing restart-safe
   scheduler; no new scheduling code.
2. **Post-batch refresh.** In `batch/run_wrapper.py`, after `module.main(...)` returns
   without raising, and only when `args.script != "build_statistics"`, run the statistics
   compute best-effort. Any exception from the refresh is caught and logged and never
   changes the parent batch's outcome or exit code. `run_wrapper` is the common path for
   scheduled and `/admin/batches` runs; scripts invoked directly from a shell bypass it
   and are covered by the hourly job.
3. **Manual.** Register `build_statistics` in `environments/batch_registry.yaml` so it is
   admin-triggerable from `/admin/batches` (Refresh now).

### 4. Read path

- `GET /api/diagnostics/statistics` reads the snapshot and returns the existing
  `StatisticsResponse` plus a new nullable `computed_at` (ISO 8601). Additive: existing
  clients ignore the extra field.
- **Fallback when no snapshot exists** (fresh environment before the first run): compute
  live once, store the result via the same upsert, and return it. The page never breaks and
  never shows an empty state. Concurrent first requests may each compute; the upsert makes
  that harmless.
- **Age bound**: a stored snapshot older than 2 hours (`SNAPSHOT_MAX_AGE`, twice the hourly
  refresh interval) is treated like a missing one and recomputed on request, so a deployment
  without a running scheduler (e.g. the Docker image, which loads only the base
  `settings.yaml` with `scheduler.enabled: false`) has bounded staleness instead of a frozen
  row. A stored payload that no longer matches the response shape (schema drift) is likewise
  recomputed, and logged as a warning.
- Update `backend_api.md` (the `StatisticsResponse` block and the endpoint entry) and the
  shared schema in `shared/schemas/`, then regenerate the TypeScript, Kotlin and Python
  types per CLAUDE.md. Note the gotcha there: `diagnostics.py` hand-writes its response
  models, so `computed_at` must be added to that router's own `StatisticsResponse` directly
  as well as to the generated one.

### 5. Frontend

Statistics page shows "Updated N min ago" derived from `computed_at`; hidden when null. No
other changes.

## Error handling

- Compute failure in `build_statistics`: `BatchRun` marked failed via the normal path; the
  previous snapshot stays in place (stale beats missing).
- Post-batch hook failure: logged, swallowed (see above).
- Fallback compute failure on the read path: propagates as a 500, same as today's endpoint.

## Testing

- Backend mocked-DB tests (`cd Backend && pytest`): snapshot present returns payload and
  `computed_at`; snapshot absent triggers live compute and store.
- Batch tests (`pytest batch/tests/`): `run_wrapper` calls the refresh after a successful
  script, not after a failing one, not for `build_statistics` itself, and a raising refresh
  does not fail the parent run.
- Integration test (`tests/integration/`, against `ocrdb_test`): upsert round-trip and
  overwrite. Per CLAUDE.md, run the entire `tests/integration/` root, not just the new file.
- Manual verification per CLAUDE.md's backend checklist: server starts, new response shape
  matches `backend_api.md`, `/api/diagnostics/health` and `/api/images?limit=1` still work.
  Do not bind ports used by the always-on environments.

## Out of scope

- Changing which metrics are computed or how.
- Per-mutation invalidation.
- Snapshot history or trend charts (the table holds only the latest row).

## Rollout

1. Apply the Alembic migration to the metal, general and IT databases FIRST (or together
   with the merge). It is purely additive, so running it before the new code is safe while
   the old code is still serving.
2. Merge. The three always-on backends run `uvicorn --reload --reload-dir Backend/app`, so
   they reload onto the new code automatically on merge; until the table exists,
   `GET /api/diagnostics/statistics` would return 500, which is why the migration goes
   first. The reloaded scheduler then fires `build_statistics` immediately (there is no
   prior `statistics` run).
3. Run `pip check` in `.venv311` before any manual backend restart (see the venv-rot
   gotcha in CLAUDE.md).
4. Until the first job run, the read-path fallback serves the first request.
