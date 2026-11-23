# Batch progress reporting on the Admin page

Status: done
Plan: `docs/superpowers/plans/2026-11-23-batch-progress-reporting.md`
Originates from: a live `ingestion_auto_prep` on `general` (2026-11-23, 12,336 images): OCR ran for hours and the only way to see how far it was was a read-only SQL query.
Follow-ups: none yet

## Purpose

A long batch job tells the operator nothing while it runs. `/admin` shows one badge per batch (`started`, `completed`,
`failed`) and a table of runs; the progress lines the job prints (`[5203/~12336] elapsed=... eta≈...`) go to a console
nobody sees when the job was started from the Admin page (the backend spawns it detached). On 2026-11-23 the answer to
"how far is it?" needed a query against `batch_runs`, `images`, `embeddings` and `ocr_texts`, and an extrapolation by hand.

This spec makes every batch able to report **how far it is** (done of total, rate, step of steps, when it last reported),
stores that on its `batch_runs` row, and shows it on the Admin page. It is generic: a job that reports nothing looks
exactly as it does today.

## Decisions

| # | Decision |
|---|---|
| D1 | **Structured progress, not log lines.** The job reports numbers and a short fixed label; the UI draws a bar. Log tails are out of v1 (see "Not in v1"). |
| D2 | **Stored on the run row**, in a new nullable JSONB column `batch_runs.progress`, separate from `stats`. `stats` are end-of-run business counters merged by read-modify-write; `progress` is a small, overwritten, live value. Two columns means a progress write can never clobber a stats merge and vice versa. |
| D3 | **Reporting is best effort.** A failed or slow progress write never fails, slows or blocks a job. It is dropped and the next one is tried. |
| D4 | **`ProgressTracker` reports automatically.** Twelve batch scripts already use it, so they gain progress with no per-script change (list in "Adoption"). Scripts without one call a small function directly. |
| D5 | **Drivers report steps.** A driver (`ingest_auto_prep`, `description_pipeline`, `recut_pipeline`, `comics_pipeline`) reports "step N of M: label" on its own row; each child already has its own run row and reports its own bar. The UI needs no parent/child link. |
| D6 | **No private text.** Progress carries counts, a unit from a fixed list and a step label from a fixed list. Never a file name, image id, path or query text (CLAUDE.md invariant, `docs/observability.md` privacy rules). |

## The progress value

`batch_runs.progress`, nullable JSONB, overwritten whole on each report:

```json
{
  "label": "extract_text_from_memes",
  "unit": "images",
  "done": 5203,
  "total": 12336,
  "rate_per_min": 19.7,
  "step": {"index": 2, "count": 8, "label": "extract_text_from_memes"},
  "updated_at": "2026-11-23T17:03:00Z"
}
```

| Field | Rule |
|---|---|
| `label` | A fixed string chosen by the code: the script or step name. Never derived from data. |
| `unit` | One of `images`, `pairs`, `descriptions`, `profiles`, `files`, `items` (a closed list in one module; add by code change). |
| `done`, `total` | Non-negative integers, nullable (both `null` in the step-only value of a driver that has reported no counts yet). `total` is `null` when unknown, and the UI then shows a count with no bar and no ETA. `done` is clamped to `total` when both are set. |
| `rate_per_min` | The tracker's own windowed rate (its existing 120 s window), `null` before two samples. The UI derives the ETA as `(total - done) / rate`; the server does not store an ETA, so it cannot go stale between reports. |
| `step` | Present only on a driver's own row. `index` is 1-based. |
| `updated_at` | Always present: server time of the write (`now()` in the UPDATE, not the worker's clock). |

`label`, `unit`, `done`, `total`, `rate_per_min` and `step` are all nullable in the API type (`BatchProgress`), because a driver's
first step report carries no counts. The service (`_normalise_progress`) turns a stored value that is partial or malformed into
`progress: null` rather than failing the runs list.

When a run ends, `progress` keeps its last value (so a finished row still reads "12,336 / 12,336"); the badge, not the
progress, says whether it is still running.

## Reporting side (batch code)

New module `batch/utils/run_progress.py`; `batch/run_tracking.py` gets a context variable.

1. **Which run am I?** `tracked_run` and `finish_existing_run` set a `ContextVar[uuid.UUID | None]` (`current_run_id`, defined in `batch/utils/run_progress.py`) on
   entry and reset it on exit. A script's code, including `ProgressTracker`, finds its run without the id being passed
   through every function. A nested self-tracked child sets its own value for its duration, so a child's tracker writes
   to the child's row and the driver's row stays with the driver.
2. **`report_progress(label, unit, done, total, rate_per_min=None)`** is a plain function (not a coroutine). It
   - does nothing when there is no current run or no running event loop (a script run outside tracking, or a sync caller);
   - is throttled per run to one write per `batch_progress.min_interval_seconds` (default 10), always letting the first
     report and a report with `done == total` through;
   - keeps at most one write in flight per run; a report that arrives meanwhile replaces the pending value (latest wins);
   - schedules the write with `loop.create_task`, holds a strong reference to the task, and swallows and logs (without
     data) any exception.
3. **The write** is one statement on its own short session: `UPDATE batch_runs SET progress = :value, ... WHERE run_id = :id
   AND status = 'started'`. It touches no other column, takes no row lock beyond the update itself, and does nothing to a
   finished run (a late write cannot overwrite a run that already completed or failed).
4. **Drain on exit.** `tracked_run` and `finish_existing_run` await the run's in-flight write before the final commit or
   fail, with a short timeout, so the last report lands and no task is left pending at loop shutdown.
5. **`ProgressTracker`** gains optional `label` and `unit` arguments (defaults: the current run's kind, `items`). In
   `mark_done` it calls `report_progress` with its own `done`, effective total and windowed rate. Its console output is
   unchanged. The `report_every` and `report_interval_secs` gates stay for console output only; the progress throttle is
   independent, so a job that prints every 100 items still reports every 10 s.
6. **`report_step(index, count, label)`** for drivers: writes the `step` object the same way. `ingest_auto_prep` calls it
   before each of its eight steps (hash dedup and format validation included, which have no tracker today).

### Adoption

- **Automatic through `ProgressTracker`** (12 call sites): `build_image_embeddings`, `extract_text_from_memes`,
  `build_ocr_lemmas`, `classify_text_heavy`, `build_ocr_text_embeddings`, `build_tags_from_ocr`, `build_image_descriptions`,
  `build_image_description_embeddings`, `build_description_lemmas`, `build_description_note_lemmas`,
  `build_description_note_embeddings`, `build_meme_profiles`. Each only needs a `unit`.
- **Direct `report_progress` calls** where a long loop has no tracker. To audit while writing the plan; the candidates are
  `ingest_hash_dedup` (hashing the inbox), `ingest_validate_formats`, `ingest_find_duplicates`, `rebuild_duplicates`,
  `clusterize`, `create_recut_cuts`, `detect_recut_crops`, `detect_comics_candidates`.
- **Drivers** (`report_step`): `ingest_auto_prep`, `description_pipeline`, `recut_pipeline`, `comics_pipeline`.

A job that is not adopted is not broken: its row shows no bar.

## API

`GET /api/admin/batches/runs` and the single-run response (`RunStatusResponse`, `shared/schemas/runstatusresponse.schema.json`)
gain `progress`: an object as above, or `null`. Additive and nullable, so existing clients (the Admin page, tests) keep
working. Regenerate the three type trees (TypeScript, Kotlin, Python; see CLAUDE.md, "Type generation") and update
`backend_api.md` in the same change. No new route, so no `ROUTE_POLICY` entry (the Admin routes are already dev-only).

The runs list is already fetched while any run is `started` (the page polls). v1 reuses that poll; no new endpoint, no
websocket.

## Admin page

In `AdminBatchesPage.tsx`, `BatchRow` shows, under the badge of a `started` run that has `progress`:

- a bar with `done / total` and the percentage; with `total == null`, only the count;
- the rate and ETA when `rate_per_min` is set ("19.7/min, about 6 h left");
- `step 2 of 8: extract_text_from_memes` for a driver row;
- "updated 20 s ago" from `updated_at`.

**Stalled hint.** When a run is `started` and `updated_at` is older than `batch_progress.stale_after_seconds` (default
300), the line turns amber: "no progress for 7 min". It is a hint, not a state: a job between two long steps legitimately
reports nothing for a while. A `started` run with no progress at all is shown as today.

The runs table gets one compact column ("Progress", `done/total`) so finished runs keep their final figure.

## Configuration

`environments/settings.yaml` (common, every key required):

```yaml
batch_progress:
  enabled: true
  min_interval_seconds: 10
```

`enabled: false` makes `report_progress` and `report_step` no-ops. The stall threshold (300 s) is **not** a setting: it is the
constant `STALE_AFTER_MS` of the web page (`src/pages/batchProgress.ts`), since only the page uses it.

## Data and migration

- Alembic migration: `ALTER TABLE batch_runs ADD COLUMN progress JSONB NULL`. No backfill, no index, no default; the table
  is small and the change is metadata-only.
- `Storage/models.py`: `BatchRun.progress: Mapped[dict | None]`.
- `sync/registry.py` already classifies `batch_runs` (class X, not synced); the new column needs no classification, but
  run `pytest tests/sync/ tests/docs/` to confirm, and add the column to `docs/image-derived-data.md` only if that guard
  asks.
- Deploy order: migrate first. Old code ignores the column; new code against an unmigrated database would fail the first
  progress write, which D3 swallows, so a late migration costs only the bar.

## Privacy and safety

- Only counts and fixed labels are stored (D6). The `label` and `unit` are validated against closed lists at write time; an
  unknown value is replaced by `items` / the run's kind rather than stored.
- Writes are bounded (one small row update, at most every 10 s per run), so a hundred jobs cost nothing measurable.
- No metric or log line gains a label or a value from this feature. If a Prometheus gauge is wanted later, it must follow
  `docs/observability.md` (bounded labels only).

## Not in v1

- **Log tails.** The detached jobs have no log capture today (stdout goes nowhere), and raw output holds paths and file
  names. If wanted later: a bounded ring buffer (for example the last 20 lines per run) filled only through an
  explicit `report_event(code, **counts)` with codes from a closed list, never from `print`. That gives "what is it doing
  right now" without scraping stdout. Separate spec.
- A progress history or chart, push updates (websocket), notifications on stall, an Android view, per-item failure lists.
- Changing the existing orphan guard (`max_runtime_minutes`) to use `updated_at`. Worth a follow-up: a run whose progress
  has been fresh is not orphaned however long it takes.

## Testing

- Unit (`batch/tests/` or `tests/batch/`, no database): throttle (first and final reports always pass, others dropped
  inside the interval), latest-wins while a write is in flight, no-op without a current run or loop, exceptions swallowed,
  `ContextVar` scoping for nested runs (child writes to the child, driver afterwards to the driver), closed-list
  validation of `unit` and `label`, `done` clamped to `total`.
- `ProgressTracker`: console output unchanged; reports `done`, effective total (after `skip`) and rate.
- Integration (`tests/integration/`, run the whole root): a real `tracked_run` with a tracker writes `progress`; a write
  after `commit` does not change a completed run; the drain on exit delivers the last report.
- Backend: `RunStatusResponse` carries `progress` (null and populated); the generated types are in sync.
- Frontend (`vitest`): bar, no-total, step line, stalled hint with a faked clock, no progress for a run without the field.
- Manual check on the test database with a short script that loops and reports, before relying on it for a real run.

## Open questions for the owner (decided)

1. Keep the last progress on a finished row: **decided, kept** (the badge says whether the run is live).
2. Write interval and stall hint: **decided, 10 s and 300 s**.
3. The orphan guard using `updated_at`: **decided, a separate follow-up**, not part of this epic.
