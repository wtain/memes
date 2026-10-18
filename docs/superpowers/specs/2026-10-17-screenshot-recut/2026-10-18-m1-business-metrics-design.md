# M1: Business metrics infrastructure - design

Status: implementation
Plan: docs/superpowers/plans/2026-10-18-m1-business-metrics.md
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md (project M1)
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
ADR: docs/superpowers/specs/2026-10-17-screenshot-recut/adr/ADR-002-business-metrics.md

M1 delivers the machinery for business metrics: an append-only `process_events` table, `BusinessMetricsService` (record, counts and
rates per batch), one read endpoint and a panel on `/admin/batches`. It records no events itself: R3 (`proposed`) and R5
(`approved`, `rejected`) call `record`. It does not touch duplicate detection (M2).

## 1. Decisions

| # | Decision | Source |
|---|---|---|
| 1 | Append-only event table, rates computed at read time, generic by design | ADR-002, FR-13, FR-14, NFR-5 |
| 2 | Events are kept forever; `batch_run_id` is `ON DELETE SET NULL`, so pruning old runs leaves the events (shown as unattributed) | user, 2026-10-18 (OQ-6) |
| 3 | The numbers are shown as a panel on `/admin/batches`, next to the runs they describe | user, 2026-10-18 |
| 4 | A subject has each event at most once; `record` is idempotent | this design |
| 5 | Allowed processes, events and metric formulas live in a code registry, not in the schema | this design |

## 2. Table `process_events`

New Alembic revision, `down_revision = 'b8d4f1a6c935'`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | default `uuid4` |
| `process` | String(40), not null | `recut`; later `duplicates` |
| `event` | String(40), not null | `proposed`, `approved`, `rejected` for recut |
| `batch_run_id` | UUID, null | FK `batch_runs.run_id`, `ON DELETE SET NULL`: the detection run that produced the subject |
| `subject_id` | UUID, not null | the proposal (`image_cuts.id` for recut); no FK because subjects differ per process |
| `occurred_at` | timestamptz, not null | server default `now()` |

- Unique `(process, event, subject_id)`: one outcome per subject. A retried transaction or a re-run step cannot double-count.
  Counts and rates are per proposal. If R6 regenerates a cut, the new proposal needs a new subject (a new `image_cuts` row) to
  count as a second proposal; R6 must respect this. The unique key is per event, so on its own it would let one subject carry
  both `approved` and `rejected`; the registry's `exclusive` groups close that: `record` raises `ConflictingEventError` when the
  subject already has an event that excludes this one. Concurrent writers must serialise on the subject (R5 locks the `image_cuts` row).
- Index `(process, batch_run_id, event)` serves the per-batch read.
- No CHECK on `process` or `event`: the table stays generic. Vocabulary is enforced by the registry (section 3).
- `process_events` hangs off no image, so `docs/image-derived-data.md` does not change.

## 3. Registry and service

`ProcessDefinition` (frozen dataclass) per process: `name`, allowed `events`, and `metrics`, a mapping of metric name to a function
of the event counts. A zero denominator yields `None`, never 0 or an error. One entry today:

| Process | Events | Metrics |
|---|---|---|
| `recut` | `proposed`, `approved`, `rejected` | `approve_rate` = approved / (approved + rejected); `decided_share` = (approved + rejected) / proposed; `pending` = proposed - approved - rejected |

Pending proposals are reported as `pending` and are never counted as rejections. M2 adds a `duplicates` entry; no new code path.

Layout (matches how batch code already imports backend services, for example `batch/build_statistics.py`):

- `repository/process_events.py`: `insert_event` (`INSERT ... ON CONFLICT DO NOTHING`) and the grouped count query. Never commits.
- `Backend/app/services/business_metrics_service.py`: `BusinessMetricsService` and the registry.
  - `record(process, event, batch_run_id, subject_id)`: raises `ValueError` for an unknown process or event, so a typo cannot write
    a row no report reads. Uses the caller's session, so it commits or rolls back with the state change it describes.
  - `batch_metrics(process, limit)`: one item per run, `{run_id, run_created_at, counts, metrics}`, newest run first, plus an
    `unattributed` bucket for rows whose `batch_run_id` is null (all counts zero when there are none; always present so every generated client type stays simple). Runs with no events are omitted. Unknown process: `UnknownProcessError` (a `ValueError`); unknown event: `UnknownEventError`.

## 4. API

`GET /api/admin/batches/metrics?process=recut&limit=50` in `Backend/app/api/admin.py`, delegating to the service (no SQL in the
router). Response: `{process, items: [{run_id, run_created_at, counts: {proposed, approved, rejected}, metrics: {approve_rate,
decided_share, pending}}], unattributed: {counts, metrics}}` (snake_case, like the other admin responses). Unknown `process`: 404. `limit` caps runs, 1 to 200, default 50.

The response model goes in `shared/schemas/`; TypeScript, Kotlin and Python types are regenerated in the same change (see CLAUDE.md,
Type generation). `backend_api.md` is updated in the same change.

## 5. Panel on `/admin/batches`

A component `BusinessMetricsPanel` with a `process` prop (M2 mounts a second one for `duplicates`), rendered below the runs list on
`AdminBatchesPage`. It fetches separately, so its failure never affects triggering batches.

- One row per detection run: started time, proposed, approved, rejected, pending, approve rate, decided share.
- Rates as percentages; `-` when `None`. A note under the table: pending proposals are not counted as rejections.
- An `unattributed` row only when that bucket has events (any count above zero).
- Empty state: "No recut proposals yet" (true until R3 records events). Error state when the fetch fails.

## 6. Tests

- Integration (`ocrdb_test`), repository and service: `record` is idempotent on `(process, event, subject_id)`; rejects an unknown
  process or event; rolls back with the caller's transaction; counts group per run; rates follow the ADR formulas; a zero
  denominator gives `None`; a late approval is attributed to the proposing run; a null run goes to `unattributed`; deleting a
  `batch_runs` row keeps its events and moves them to `unattributed`; runs without events are omitted.
- Migration: shape test against the model, like R1's.
- API: 404 for an unknown process; response shape; `limit` bounds.
- Frontend (vitest): rows and percentages, `-` for a zero denominator, empty state, error state.
- Registry guard: every `ProcessDefinition` metric returns `None` or a number for all-zero counts (no `ZeroDivisionError`).

## 7. Out of scope

Recording events (R3, R5); backfill; a retention job (decision 2); duplicate detection metrics (M2); charts or trends over time.

## 8. Acceptance

- `process_events` exists with the constraints above and a migration that upgrades and downgrades cleanly.
- `record` and `batch_metrics` behave as in section 6, verified by the tests.
- The panel shows per-run counts and rates from the endpoint, and degrades to its empty and error states.
- `backend_api.md`, the shared schema and the generated types agree with the router.
- Live rollout of the migration follows the same rule as R1: only with explicit go-ahead and a confirmed backup, one environment at a time.
