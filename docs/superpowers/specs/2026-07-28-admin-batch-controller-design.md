# Admin Batch Controller — Design

Status: done
Plan: docs/superpowers/plans/2026-07-28-admin-batch-controller.md
Originates from: docs/superpowers/specs/2026-07-28-batch-run-wrapper-design.md
Follow-ups: docs/superpowers/specs/2026-07-31-admin-web-ui-design.md

**Date:** 2026-07-28.

Third of a 3-spec sequence for the batch admin controller feature. Depends on
`2026-07-28-batch-run-trigger-tracking-design.md` (trigger column, `BatchAlreadyRunningError`) and
`2026-07-28-batch-run-wrapper-design.md` (`batch/registry.py`, `batch/run_wrapper.py`,
`finish_existing_run`).

Related: `docs/security/admin-permissions-todo.md` tracks the permission-guarding gap this spec
deliberately leaves open (see "No permission model" below).

---

## Motivation

An operator needs to trigger `trends_batch`, `move_flagged`, or `unregister_deleted_images` on
demand from the backend (rather than only via SSH/terminal access to the box), see whether a
triggered run is still going, and see recent run history. This spec adds that HTTP surface on top
of the run-tracking and wrapper machinery built in the first two specs.

## Scope

**In scope:** `POST .../run`, `GET .../runs/{run_id}`, `GET .../runs` (paginated); a shared
subprocess-spawn helper extracted from the scheduler so both depend on one implementation.

**Out of scope (see security doc):** any authentication/authorization. No request body beyond the
URL path — the endpoint never accepts a module path, extra CLI args, or anything else that could
widen what gets executed beyond the fixed three-script allow-list.

---

## Design

### Router: `Backend/app/api/admin.py`

`APIRouter(prefix="/api/admin/batches", tags=["admin"])`, registered in `Backend/app/main.py`
alongside the existing routers.

#### `POST /api/admin/batches/{batch_name}/run`

```python
class RunTriggerResponse(BaseModel):
    run_id: str
    status: str  # "running" — see status mapping below
```

1. `batch_name` validated via `batch.registry.BatchRegistry().get(batch_name)` (from spec 2 —
   externalized, hot-reloadable YAML-backed registry, not a static constant) — `404` if `None`
   (not a recognized name).
2. **Deliberate exception to the usual `get_async_db` convention:** this endpoint calls
   `repo.create_run(kind=entry["kind"], trigger="manual")` (where `entry` is the `BatchRegistry().get(batch_name)`
   result from step 1) on its own session (still
   via `Depends`, matching the rest of `Backend/app/api/*`) but then **explicitly commits
   immediately**, before spawning anything — not left to `get_async_db`'s usual after-the-handler
   commit. The spawned wrapper is a *separate OS process* with its own DB connection; it must be
   able to see the new `batch_runs` row the instant it starts. Relying on the normal
   request-scoped commit-after-handler-returns timing would risk the subprocess querying/updating a
   row that isn't committed yet. Everywhere else in this router (e.g. reads in the other two
   endpoints), the normal `get_async_db` convention applies unchanged.
3. On `BatchAlreadyRunningError` (from spec 1): return `409` with a body naming the batch and that
   it's already running (no run_id to return — nothing new was created).
4. On success: spawn `python -m batch.run_wrapper --script {batch_name} --env {APP_ENV} --trigger
   manual --run-id {run_id}` via the shared subprocess helper below, **not awaited** — the handler
   returns `{"run_id": ..., "status": "running"}` as soon as the subprocess is launched, without
   waiting for it to finish.

`APP_ENV` here is this backend process's own environment (`os.environ["APP_ENV"]`) — this endpoint
only ever triggers a run for its own environment, matching the scheduler's existing per-environment
scope. There is no cross-environment triggering (a metal-backend request cannot start a job against
the general or IT database).

#### `GET /api/admin/batches/runs/{run_id}`

```python
class RunStatusResponse(BaseModel):
    run_id: str
    batch_name: str      # BatchRegistry().name_for_kind(BatchRun.kind) — see spec 2
    trigger: str          # "manual" | "scheduled" | "unknown"
    status: str            # "running" | "completed" | "failed" — see mapping below
    created_at: datetime
    completed_at: Optional[datetime]
    error: Optional[str]
```

`404` if the `run_id` doesn't exist, **or** if it exists but its `kind` isn't one of the three admin
kinds (e.g. an ingestion run_id) — this endpoint is intentionally scoped, per the earlier decision
that ingestion keeps its own richer `/api/ingestion/*` endpoints rather than being folded into a
generic list it doesn't cleanly fit (multi-stage runs, `stage` field, etc.).

#### `GET /api/admin/batches/runs?limit=&offset=`

```python
class RunListResponse(BaseModel):
    items: list[RunStatusResponse]
    total: int   # total matching rows (all three kinds), independent of limit/offset — lets the
                 # client render "N total runs" / compute page count without a second request
```

Filtered to `kind IN ('trends', 'move_flagged', 'unregister_deleted_images')`, ordered
`created_at DESC`. `limit` defaults to a sane value (e.g. 50, capped at some max like 200); `offset`
for paging; `total` from a separate `COUNT(*)` query with the same filter. No status/trigger
filtering for now — not asked for, and the three-kind admin list is small enough that client-side
filtering of one page is fine until a real need for server-side filtering shows up.

### Status mapping

`BatchRun.status` (`started`/`completed`/`failed`) maps to the API's `running`/`completed`/`failed`
— `"started"` never appears in a response, matching how you described the run-list column.

### Shared subprocess helper: `Backend/app/batch_subprocess.py`

Extracted from `Backend/app/scheduler.py`'s `_spawn`/`_wait_for_process` (the `subprocess.Popen` +
manually-created daemon-thread-bridged-via-`call_soon_threadsafe` mechanism, *not*
`asyncio.create_subprocess_exec`/`asyncio.to_thread` — both already proven, at real cost, to either
get the child killed on cancellation or block backend shutdown; see
`2026-07-27-batch-job-scheduler-design.md` and its implementation history) — a decoupled module
`scheduler.py` now imports from, rather than admin code reaching into scheduler internals:

```python
async def spawn_and_track(args: list[str], log_path: Path, label: str) -> int:
    """Spawn args via Popen, redirect stdout/stderr to log_path, await completion via a
    daemon thread (survives cancellation and doesn't block shutdown), log the exit code
    (attributed to `label`, a logging-only identifier -- e.g. job name or batch name --
    never passed to Popen), return it. Caller decides whether to await this inline
    (scheduler) or fire-and-forget it as a background task (admin endpoint)."""
```

**Log path naming** — one file per invocation, nested by environment, replacing the scheduler's
current per-job-forever-append naming (`logs/scheduler-<name>.log`, which — since all three
environments run from the same repo checkout — is actually already silently shared/colliding across
metal/general/it today, a latent gap this incidentally fixes):

```
logs/{env}/{script}_{YYYYMMDDTHHMMSS_ffffff}.log
```

e.g. `logs/metal/trends_batch_20260728T143022_481932.log`. The caller (scheduler or admin endpoint)
computes this path itself — env and script name are already known before spawning, and the
timestamp is generated by the *parent* process at the moment of spawning, with microsecond
resolution to make a same-instant collision practically impossible. No PID and no `run_id` is
involved in the filename: `Popen()`'s `stdout=` file must be opened *before* the child process
exists, so the name can never depend on anything only the child would know (the child's own PID,
in particular) without an open-then-rename dance — fragile in general and less reliable on Windows
specifically, where renaming an open file is less predictable than on POSIX. There is deliberately
no link back to a `batch_runs.run_id` and no admin endpoint to fetch a log by run_id — an operator
who needs a specific run's output looks in `logs/{env}/` directly, correlating by script name and
timestamp against the run's `created_at`.

For the admin endpoint's fire-and-forget use, the request handler wraps the call in
`asyncio.create_task(...)` — and, mirroring a lesson already learned in the scheduler's own history
(an earlier fix round there needed a module-level strong-reference set specifically so a detached
task's `Popen()` call couldn't be garbage-collected before it ran), this module also exposes a small
`fire_and_forget(coro)` helper that creates the task, holds a strong reference in a module-level set
until it's done, and logs (rather than swallows) any exception the task itself raises — reusing that
already-hard-won pattern instead of re-deriving it.

### Testing

- Unit tests (mocked repository/session, matching `Backend/tests/test_scheduler.py`'s style):
  `POST .../run` happy path returns `run_id`+`"running"`; `BatchAlreadyRunningError` → `409`;
  unknown `batch_name` → `404`.
- `GET .../runs/{run_id}`: found (each status mapped correctly), not found, found-but-wrong-kind
  (e.g. an `ingestion` row) → `404`.
- `GET .../runs`: pagination (`limit`/`offset` respected), ordering, scoping to the three kinds only
  (a mixed fixture with an ingestion row present must not leak into the list).
- `batch_subprocess.py`: same real-subprocess verification standard as the scheduler's own tests —
  a genuine child process, not a mock, proving survival across cancellation and prompt shutdown
  (both properties, per the scheduler's hard-won test suite).
- Integration-level (real DB + a stub target script, not the real `trends_batch`): triggering via
  the endpoint produces a `batch_runs` row with `trigger="manual"`, and the row transitions to
  `completed`/`failed` once the spawned wrapper finishes.

### `backend_api.md`

Update with the three new endpoints, their request/response shapes, and status codes — required by
`CLAUDE.md`'s "Adding a new endpoint" checklist.

## Rollout

1. `Backend/app/batch_subprocess.py` (extracted from `scheduler.py`) + its own tests; update
   `scheduler.py` to import from it instead of defining `_spawn`/`_wait_for_process` itself
   (behavior-preserving extraction, existing scheduler tests should still pass unchanged in intent,
   updated only for the new import location).
2. `Backend/app/api/admin.py` (all three endpoints) + tests.
3. Register the router in `Backend/app/main.py`.
4. Update `backend_api.md`.
5. Write `docs/security/admin-permissions-todo.md` (see below — can happen in parallel with 1-4).
