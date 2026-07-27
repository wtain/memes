# Batch Job Scheduler — Design

Status: planned
Plan: docs/superpowers/plans/2026-07-27-batch-job-scheduler.md

**Date:** 2026-07-27.

---

## Motivation

Batch jobs like `trends_batch.py` (news scraping + GLiNER NER, needs to run repeatedly to stay
useful — trends go stale) are currently only ever invoked manually
(`python -m batch.trends_batch --env <env>`). Nothing in the repo runs any batch job on a
recurring basis; `ARCHITECTURE.md` and `Readme.md` list "batch orchestration (Airflow or similar)"
as an aspirational, unimplemented future item. This spec adds a lightweight, generic recurring-job
scheduler so jobs like `trends_batch` can run on an interval without a human remembering to kick
them off.

## Scope

**In scope:** a generic scheduler embedded in the backend process, configured via a job list
(name, module, interval, max runtime), with `trends_batch` as the first configured job. Restart-safe
timing and a crash/orphan-recovery mechanism, both built on the existing `BatchRun` table
(`docs/superpowers/specs/2026-07-25-batch-run-tracking-design.md`).

**Out of scope:**
- Cron-style scheduling (specific times of day, days of week) — plain "every N minutes" intervals
  only. Nothing today needs finer control than that; add it later if a real need shows up.
- Any new API endpoint or UI surface for schedule status / manual trigger. Observability is the
  existing `batch_runs` table plus process logs.
- Retry-on-failure logic. A failed run is logged; the scheduler just waits for its next regular
  tick, matching `trends_batch`'s existing accumulate-and-move-on semantics.
- Scheduling any job that doesn't already write `BatchRun` rows (see "Jobs without BatchRun
  integration" below) — not blocked by this spec, just not solved by it.
- A real deployed-production target. metal/general/IT are all local-dev environments today
  (`CLAUDE.md`); this spec has no specific hosted deployment to design for. It does, however,
  document the operational trade-off explicitly so a future real deployment can make an informed
  call (see "Operational caveat" below).

## Non-goals / rejected alternatives

- **A separate scheduler process/service.** Would decouple scheduling from backend uptime, but
  introduces a new kind of deployment artifact this repo doesn't have, for three environments that
  are all "just run the backend locally" today. Rejected in favor of embedding in each
  environment's own backend process — matches the existing per-environment isolation (separate
  DB/config/ports already exist per environment; no new moving part).
- **APScheduler / celery / similar.** Considered and rejected: the interval-only requirement (no
  cron, no distributed workers) is fully covered by a plain `asyncio` loop with no new dependency.
  Revisit only if cron-style expressiveness becomes a real requirement.
- **In-process (non-subprocess) job invocation.** Batch jobs are standalone scripts, each calling
  `asyncio.run(main())` and building their own DB engine — calling that directly from the
  backend's already-running event loop isn't possible (nested `asyncio.run`), and refactoring every
  batch script's entrypoint into an awaitable callable sharing the backend's engine is a much larger
  and riskier change than spawning a subprocess. Subprocess invocation needs zero changes to any
  existing batch script.

---

## Design

### Config schema

New `scheduler` domain in `environments/settings.yaml` (+ per-environment override files, following
the existing flat-boolean-under-domain convention used by `ollama.enabled` /
`rules.lemmatize`):

```yaml
scheduler:
  enabled: true            # global on/off switch for this environment's backend process
  jobs:
    - name: trends_batch
      module: batch.trends_batch   # invoked as: <python> -m batch.trends_batch --env <APP_ENV>
      batch_run_kind: trends       # matches BatchRun.kind — ties this job into the run-tracking table
      interval_minutes: 360
      max_runtime_minutes: 60      # legitimate trends_batch runs take minutes; well above worst case
      enabled: true
```

`scheduler.enabled: false` disables the scheduler for that environment's backend entirely (no jobs
loop is started). Each job entry also has its own `enabled` flag so individual jobs can be toggled
without touching the global switch.

### Where it lives

`Backend/app/scheduler.py` (new module). Started/stopped from the existing FastAPI `lifespan`
context manager in `Backend/app/main.py`:

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    _configure_logging()
    scheduler_task = await start_scheduler()  # no-op if scheduler.enabled is false
    yield
    await stop_scheduler(scheduler_task)
```

`start_scheduler()` reads `settings.scheduler.jobs`, and for each entry with `enabled: true` spawns
one `asyncio.Task` running that job's loop (`_job_loop(job_config)`). Each job's loop is independent
— one job's failure or a long run doesn't block another job's schedule.

### Per-job loop

```
_job_loop(job):
    session = AsyncSessionLocal()
    repo = BatchRunRepository(session)

    delay = _initial_delay(repo, job)   # restart-safe: see below
    loop:
        await asyncio.sleep(delay)
        if not await _should_run(repo, job):
            delay = job.interval_minutes * 60
            continue
        await _spawn(job)
        delay = job.interval_minutes * 60
```

**Restart-safe initial delay** (`_initial_delay`): query the most recent `BatchRun` row for
`job.batch_run_kind` (any status). If its `created_at` is within `interval_minutes` of now, the
first fire is delayed by the remainder rather than firing immediately — so a dev `--reload` cycle
(or any backend restart) doesn't cause more-frequent-than-configured runs. If no prior run exists,
or the most recent one is already older than the interval, the first fire happens immediately.

**Concurrency / orphan-recovery check** (`_should_run`): calls `get_active_run(job.batch_run_kind)`.

- No active run → proceed.
- Active run found, and `now - run.created_at < job.max_runtime_minutes` → assume it's legitimately
  still in progress (this process's own prior tick, a not-yet-finished subprocess surviving a
  backend restart, or — incidentally — another instance sharing the same DB). Skip this tick.
- Active run found, and `now - run.created_at >= job.max_runtime_minutes` → treat as orphaned
  (crashed process, killed machine, anything that stopped short of calling `commit()`/`fail()`).
  Call `repo.fail(run.run_id, error="orphaned: presumed crashed or killed")` to close out the stale
  row (so `batch_runs` never has a permanently-dangling "still running" row), log a warning, then
  proceed with a fresh run.

This reuses `BatchRunRepository.get_active_run()` and `.fail()` exactly as they exist today
(`repository/batch_runs.py`) — no repository changes needed.

**Per-tick error isolation:** each iteration of the loop body (the `_should_run` check and the
`_spawn` call) is wrapped in its own `try/except`, logging and continuing to the next tick rather
than letting the exception propagate out of the loop. An `asyncio.Task`'s exception is otherwise
silent until something awaits or inspects it — an uncaught exception here would permanently and
invisibly stop that job from ever being scheduled again, with no crash and no obvious signal. A
caught, logged exception at least leaves a trace and lets the next tick retry.

### Job invocation (`_spawn`)

```python
proc = await asyncio.create_subprocess_exec(
    sys.executable, "-m", job.module, "--env", app_env,
    stdout=<log file>, stderr=<log file>,
)
await proc.wait()
```

- `sys.executable` is the interpreter already running the backend — same venv (`.venv311`), no
  separate venv resolution needed.
- `app_env` is the backend process's own `APP_ENV` — the scheduler only ever schedules jobs for its
  own environment (see "Per-environment scope" below), so this is always correct without extra
  config.
- stdout/stderr are redirected to a log file (one per job, rotated/appended — exact path a detail
  for the implementation plan, e.g. `logs/scheduler-<job.name>.log`). The scheduler does **not**
  inspect the subprocess exit code to decide success/failure — the job's own `BatchRun` row
  (written via `runs_repo.commit()`/`.fail()` inside `trends_batch.py`'s existing `try/except`) is
  the source of truth for outcome. The exit code and log are for human debugging only.
- On backend shutdown (`stop_scheduler`), the scheduler cancels its own `asyncio.Task` loops (so no
  *new* runs get scheduled) but does **not** kill any in-flight subprocess — it's left to finish on
  its own. This matters specifically for dev `--reload` cycles: killing a partially-done LLM/NER
  run would waste completed work for nothing, and letting it finish is safe because the next
  process's scheduler will see the still-active `BatchRun` row via `_should_run` and skip
  (not double-fire) until that row's `max_runtime_minutes` window truly elapses.

### Per-environment scope

Each of metal/general/IT's backend process runs its own scheduler instance (started in that
process's own `lifespan`), reading that environment's own `scheduler.*` config and operating against
that environment's own database — consistent with the existing per-environment isolation (separate
DB, config, ports; see `environments/Environments.md`). There is no cross-environment coordination
and none is needed, since each environment's `batch_runs` table is already a separate database.

### Jobs without `BatchRun` integration

The restart-safe delay and orphan-recovery check both key off `BatchRun.kind`, so `batch_run_kind`
is a **mandatory** field for any `scheduler.jobs` entry — `_load_job_configs` skips (logging an
error, not crashing the backend) any job config missing it or any other required key, rather than
falling back to a naive in-memory-only timer. A job that doesn't write `BatchRun` rows (most of
`batch/` today — see the "out of scope" note in `2026-07-25-batch-run-tracking-design.md`) cannot
be scheduled through this mechanism yet: give it proper `BatchRun` tracking (a small, job-specific
effort) first.

### Operational caveat (explicit)

Embedding the scheduler in the backend process is a pragmatic choice for the current reality: three
environments, all running as long-lived local processes on a developer's workstation. It is **not**
a recommendation for a real hosted/production deployment: a user-facing API process spawning heavy
LLM/NER subprocesses competes for CPU/memory with request traffic, can't be scaled or restarted
independently of the scheduler, and ties job cadence to backend uptime. If a real production
deployment of this backend is ever stood up, `scheduler.enabled: false` for that deployment plus an
OS-level scheduler (cron / Windows Task Scheduler) invoking the same
`python -m batch.<module> --env <env>` commands directly is the documented alternative — no code
change needed to switch, since the batch scripts themselves are unchanged either way.

---

## Testing

Unit tests under `Backend/tests/` (existing `Mode.AUTO` async pytest setup — do not combine with
other test roots per the "Running the right test scope" gotcha in `CLAUDE.md`):

- `_initial_delay`: given a fixture/mocked most-recent `BatchRun` at various ages relative to
  `interval_minutes`, asserts the computed delay (including the "no prior run" and
  "already older than interval" → immediate-fire cases).
- `_should_run`: given a mocked `get_active_run` returning `None` / a fresh active run / a stale
  active run (age vs. `max_runtime_minutes`), asserts skip vs. proceed vs. proceed-and-mark-failed,
  and that `.fail()` is called with the orphan error message only in the stale case.
- Config parsing: a `scheduler.enabled: false` config yields zero started job tasks; a job entry
  with `enabled: false` is skipped while others in the same list still start.

No new integration test root — the DB interactions above are exercised through mocks/fixtures of
`BatchRunRepository`, consistent with how `Backend/tests/` mocks the DB elsewhere per `CLAUDE.md`.

## Rollout

1. Add `scheduler` domain to `environments/settings.yaml` (defaults, `enabled: true`) with the
   `trends_batch` entry; no per-environment override needed unless an environment wants a different
   interval.
2. Add `Backend/app/scheduler.py` (loop, config parsing, `_initial_delay`, `_should_run`, `_spawn`).
3. Wire `start_scheduler()`/`stop_scheduler()` into `Backend/app/main.py`'s `lifespan`.
4. Unit tests per above.
5. Manually verify: start a backend with a short `interval_minutes` (e.g. `1`) and
   `max_runtime_minutes` (e.g. `1`) locally, confirm a `batch_runs` row with `kind="trends"`
   appears on schedule, confirm a restart within the interval doesn't cause an early re-fire, and
   confirm manually killing a spawned subprocess mid-run gets recovered (marked failed, new run
   spawned) after `max_runtime_minutes` elapses.
