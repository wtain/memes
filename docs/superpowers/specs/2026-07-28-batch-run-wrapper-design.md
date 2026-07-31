# Universal Batch Run Wrapper — Design

Status: done
Plan: docs/superpowers/plans/2026-07-28-batch-run-wrapper.md
Originates from: docs/superpowers/specs/2026-07-28-batch-run-trigger-tracking-design.md
Follow-ups: docs/superpowers/specs/2026-07-28-admin-batch-controller-design.md

**Date:** 2026-07-28.

Second of a 3-spec sequence for the batch admin controller feature. Depends on
`2026-07-28-batch-run-trigger-tracking-design.md` (the `trigger` column and
`BatchAlreadyRunningError` this spec relies on). Followed by
`2026-07-28-admin-batch-controller-design.md` (the HTTP API, depends on this).

---

## Motivation

The admin controller (spec 3) needs to trigger `trends_batch`, `move_flagged`, and
`unregister_deleted_images` on demand, get a `run_id` back synchronously, and have that run
correctly tagged `trigger="manual"`. The scheduler (already shipped,
`2026-07-27-batch-job-scheduler-design.md`) needs the same three scripts tagged `"scheduled"`
instead — currently it invokes `trends_batch.py` directly, which self-tags every run the same way
regardless of who asked for it. Neither of the other two scripts has any run-tracking at all today.

This spec makes run-tracking generic and correct for all three call paths — direct CLI, scheduler,
admin controller — without losing the ability to run any of these scripts by hand exactly as today.

## Scope

**In scope:** a shared tracking helper (`batch/run_tracking.py`); a fixed script allow-list
(`batch/registry.py`); a `run()`/`main()` split for `trends_batch.py` (the other two already have
it); a new `batch/run_wrapper.py` entry point; updating `scheduler.py` to invoke through the
wrapper instead of the raw module.

**Out of scope:** the HTTP API (spec 3); anything about permissions/auth (tracked separately in
`docs/security/admin-permissions-todo.md`).

---

## Current state (for reference)

`move_flagged.py` and `unregister_deleted_images.py` already separate work from setup:

```python
async def run(session, base_path):
    ...  # the actual work

async def main():
    async with AsyncSessionLocal() as session:
        base_path = os.path.abspath(settings.BASE_PATH)  # or os.getenv('BASE_PATH') for the latter
        await run(session, base_path)

if __name__ == "__main__":
    parser.add_argument("--env", ...)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
```

Neither calls `BatchRunRepository` at all — no tracking exists for either today.

`trends_batch.py`'s work is inlined directly in `main()`, interleaved with its own tracking
(`create_run`/`commit`/`fail`) — no separate `run()` exists.

`scheduler.py`'s `_spawn` (`Backend/app/scheduler.py`) invokes
`sys.executable -m <job["module"]> --env <app_env>` directly — the target module's own `main()`
handles its own tracking as a side effect, with no way for the scheduler to pass `trigger` through.

## Design

### `batch/registry.py` — the fixed allow-list, externalized and hot-reloadable

Not a Python constant — a tracked YAML file, following the same common-file-plus-per-environment-
override convention already used for `environments/settings.yaml`, since the set of triggerable
batches may differ per environment later even though all three environments share the same set
today:

`environments/batch_registry.yaml` (common):

```yaml
trends_batch:
  module: batch.trends_batch
  kind: trends
move_flagged:
  module: batch.move_flagged
  kind: move_flagged
unregister_deleted_images:
  module: batch.unregister_deleted_images
  kind: unregister_deleted_images
```

An optional `environments/batch_registry.<env>.yaml` can add/override entries per environment later
— not needed yet since all three environments expose the same three batches today, but the loader
supports it from day one so a future environment-specific batch doesn't need a loader change.

`batch/registry.py` provides a small class rather than a bare dict, and — deliberately — reads the
file(s) fresh on every call rather than caching:

```python
class BatchRegistry:
    def __init__(self, base_dir: Path = Path("environments")):
        self._base_dir = base_dir

    def get(self, script_name: str) -> dict | None:
        """Fresh read on every call -- editing the registry file takes effect immediately,
        no backend/scheduler restart needed."""
        return self._load().get(script_name)

    def all_names(self) -> list[str]:
        return list(self._load().keys())

    def name_for_kind(self, kind: str) -> str | None:
        """Reverse lookup -- BatchRun.kind -> public script name, for API responses that need
        to show a human-facing batch_name without exposing the internal kind value directly
        (used by spec 3's status/list endpoints)."""
        for name, entry in self._load().items():
            if entry["kind"] == kind:
                return name
        return None

    def _load(self) -> dict:
        common = _read_yaml(self._base_dir / "batch_registry.yaml")
        env = os.environ.get("APP_ENV")
        override = _read_yaml(self._base_dir / f"batch_registry.{env}.yaml") if env else {}
        return {**common, **override}
```

This is the *only* place that maps a public script name to a Python module path — a fixed,
server-side-controlled file, never editable by a client. Both `run_wrapper.py` (this spec) and the
admin API (spec 3) use `BatchRegistry`; neither ever constructs a module path from client input.
`kind` values for the two previously-untracked scripts are new (`move_flagged`,
`unregister_deleted_images`) — `trends`'s existing `kind` value is kept as-is, no rename/backfill of
historical rows.

Re-reading the file on every lookup costs a small amount of I/O per trigger/scheduler-tick, which is
negligible at this call frequency (nowhere near a hot path) and is the deliberate trade-off for
"no restart needed to pick up a registry change."

### `batch/run_tracking.py` — shared tracking helper

```python
@asynccontextmanager
async def tracked_run(kind: str, trigger: str):
    """Self-creates a run, yields its run_id, commits/fails it around the wrapped work.
    Used by each script's own main() for direct-CLI/self-tracked use, and by the wrapper
    when no run_id was pre-created (the scheduler's case)."""
    async with AsyncSessionLocal() as session:
        repo = BatchRunRepository(session)
        run_id = await repo.create_run(kind=kind, trigger=trigger)
        await session.commit()
    try:
        yield run_id
    except Exception as e:
        async with AsyncSessionLocal() as session:
            await BatchRunRepository(session).fail(run_id, error=str(e))
            await session.commit()
        raise
    else:
        async with AsyncSessionLocal() as session:
            await BatchRunRepository(session).commit(run_id)
            await session.commit()


@asynccontextmanager
async def finish_existing_run(run_id: uuid.UUID):
    """Commits/fails a run_id the CALLER already created (the admin endpoint's case, where the
    run_id must exist synchronously before the subprocess is even spawned)."""
    try:
        yield
    except Exception as e:
        async with AsyncSessionLocal() as session:
            await BatchRunRepository(session).fail(run_id, error=str(e))
            await session.commit()
        raise
    else:
        async with AsyncSessionLocal() as session:
            await BatchRunRepository(session).commit(run_id)
            await session.commit()
```

### Script shape: `main(trigger, run_id=None)`

Every script's `main()` becomes the single entry point for both direct-CLI and wrapper use. Shown
here for `move_flagged.py` specifically — **`kind` is that script's own registry `kind` value in
every case** (`"trends"` for `trends_batch.py`, `"unregister_deleted_images"` for that script), not
a value to copy verbatim across all three:

```python
# move_flagged.py
async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(settings.BASE_PATH)
                await run(session, base_path)
    else:
        async with tracked_run(kind="move_flagged", trigger=trigger):  # this script's own kind
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(settings.BASE_PATH)
                await run(session, base_path)

if __name__ == "__main__":
    parser.add_argument("--env", ...)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" — unchanged direct-CLI behavior
```

`move_flagged.py` and `unregister_deleted_images.py` need only this `main()` change (their `run()`
is untouched) — they gain tracking they never had, with `trigger="manual"` for direct use exactly
like today. `trends_batch.py` additionally needs its inlined work extracted into a `run(session)`
matching the other two's shape (moving the `sources_repo`/`results_repo`/loop logic out of `main()`
verbatim, no behavior change), then the same `main(trigger, run_id=None)` shape applied. Its
temporary `trigger="unknown"` call site from spec 1 is fully replaced here.

### `batch/run_wrapper.py`

```python
async def main():
    registry = BatchRegistry()
    parser.add_argument("--script", choices=registry.all_names(), required=True)
    parser.add_argument("--env", choices=["metal", "general", "it"], required=True)
    parser.add_argument("--trigger", choices=["manual", "scheduled"], required=True)
    parser.add_argument("--run-id", default=None,
                         help="Pre-created run id (admin controller); omitted for the scheduler, "
                              "which lets this wrapper create its own run.")
    args = parser.parse_args()
    load_env(args.env)

    entry = registry.get(args.script)  # re-read here too, not reused from the choices lookup above
    module = importlib.import_module(entry["module"])
    run_id = uuid.UUID(args.run_id) if args.run_id else None
    await module.main(trigger=args.trigger, run_id=run_id)

if __name__ == "__main__":
    asyncio.run(main())
```

Each invocation of `run_wrapper.py` is already a fresh process, so `BatchRegistry()` reading the
file fresh here is inherent, not an extra step — the "no restart needed" property specifically
matters for the two long-running processes that use `BatchRegistry` repeatedly over their lifetime
(the admin API and the scheduler, both covered below/in spec 3), where the whole point is that
neither needs restarting for a registry edit to take effect.

`import_module` only ever receives a value looked up from `BatchRegistry` by an `argparse
choices`-constrained `--script` — never a client-supplied string used directly.

### `scheduler.py` change

`_spawn` (`Backend/app/scheduler.py`) invokes the wrapper instead of the raw module:

```python
proc = subprocess.Popen(
    [sys.executable, "-m", "batch.run_wrapper",
     "--script", job["script"], "--env", app_env, "--trigger", "scheduled"],
    stdout=log_file, stderr=log_file,
)
```

Job config (`environments/settings.yaml`'s `scheduler.jobs`) changes its `module` key to `script`,
using the same registry names as `environments/batch_registry.yaml` (`trends_batch`, not
`batch.trends_batch`) — one shared vocabulary between the scheduler's config and the admin
controller's allow-list, rather than two separately-maintained mappings. `_load_job_configs` (Task 2
of the scheduler plan) is updated accordingly. Everything else about the scheduler's own logic
(restart-safe timing, orphan recovery, per-tick error isolation) is unchanged — this only touches
what gets spawned and with what arguments. The daemon-thread subprocess-wait *mechanism* itself is
also unchanged in behavior, but its code moves out of `scheduler.py` into a shared module as part of
spec 3 (which also changes the log file naming scheme it uses) — see
`2026-07-28-admin-batch-controller-design.md`.

Note: `scheduler.py` itself never needs to call `BatchRegistry` directly for validation — the
`script` value in `environments/settings.yaml` is server-side tracked config already, not client
input, and `run_wrapper.py` (a fresh process each tick) is what actually resolves it against the
registry. The "no restart needed" property that matters for the scheduler is about
`environments/batch_registry.yaml` edits taking effect on the *next tick* without a scheduler
restart — which holds automatically, since each tick spawns a brand-new `run_wrapper.py` process
that reads the registry fresh.

### Testing

- Unit tests for `run_tracking.py`'s two context managers (mocked repository, matching the style
  already used in `Backend/tests/test_scheduler.py`): commit-on-success, fail-on-exception, for
  both `tracked_run` and `finish_existing_run`.
- Integration tests (real DB, `tests/integration/`): running `move_flagged.main()` and
  `unregister_deleted_images.main()` directly now produces a `batch_runs` row with the right `kind`
  and `trigger="manual"`; running with a pre-created `run_id` finishes that exact row instead of
  creating a new one.
- `run_wrapper.py`: a test invoking it as a subprocess (or importing and calling its `main()`
  directly) against each of the three registry entries, confirming the right module gets imported
  and the right `trigger`/`run_id` flow through.
- `scheduler.py`: update `Backend/tests/test_scheduler.py`'s `_spawn`-related tests for the new
  `run_wrapper` invocation shape (registry `script` name instead of raw `module` path).

## Rollout

1. `batch/registry.py`.
2. `batch/run_tracking.py` + unit tests.
3. `move_flagged.py`/`unregister_deleted_images.py` `main()` change + integration tests.
4. `trends_batch.py` `run()`/`main()` split + integration test (behavior-preserving refactor —
   verify trend-scraping output is unchanged, not just that a `BatchRun` row appears).
5. `batch/run_wrapper.py` + tests.
6. `scheduler.py` + `environments/settings.yaml` job-config key rename (`module` → `script`,
   registry names) + updated scheduler tests.
