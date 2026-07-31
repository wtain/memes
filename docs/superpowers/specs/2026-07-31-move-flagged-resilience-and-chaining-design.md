# move_flagged: Resilience, Stats, and Chained Unregister — Design

Status: done
Plan: docs/superpowers/plans/2026-07-31-move-flagged-resilience-and-chaining.md

**Date:** 2026-07-31.

Three requested improvements to `batch/move_flagged.py`: don't abort the whole run on a per-file
move error, report counts instead of discarding them, and follow up with
`unregister_deleted_images` so the DB reconciles with what was actually moved out of `BASE_PATH`.

---

## Motivation

`move_flagged.py` currently aborts entirely on the first `shutil.move()` failure (e.g. a file
already missing on disk), leaving every remaining flagged file unmoved and the run marked
`failed` in `batch_runs` even though most files may have moved fine. It also reports nothing —
no count of what moved, what didn't, or why. And after moving files out of `BASE_PATH`, an
operator currently has to remember to run `unregister_deleted_images` separately to reconcile the
DB; nothing does that automatically.

## Scope

**In scope:** `batch/move_flagged.py`'s move loop (per-file error handling, metrics), persisting
those metrics to the run's `batch_runs.stats`, and chaining a call to
`batch/unregister_deleted_images.py`'s existing `main()` after move_flagged's own run completes.

**Out of scope:**
- Changes to `unregister_deleted_images.py` itself (its own error handling/behavior is unchanged).
- Admin API/UI changes to surface `stats` — this spec only ensures stats are captured and
  persisted (`batch_runs.stats`, queryable), not exposed through `RunStatusResponse` yet.
- Scheduler changes — neither script is currently in `SCHEDULER.JOBS`, so no interaction with
  scheduled runs to consider.

## Design

### Per-file resilience

`move_flagged.run()`'s loop wraps each `shutil.move()` individually:

```python
try:
    shutil.move(path_from, path_to)
    metrics.increment("moved")
except FileNotFoundError as e:
    print(f"Skipping {filename}: not found ({e})")
    metrics.increment("error.file_not_found")
except Exception as e:
    print(f"Skipping {filename}: move failed ({e})")
    metrics.increment("error.move_failed")
```

The loop always continues to the next file — no exception from an individual move propagates out
of `run()`. `os.makedirs(flagged_path, exist_ok=True)` (creating `excluded/`) stays unguarded — a
failure there means the run genuinely cannot proceed and should still fail the whole run, matching
current behavior.

### Metrics and stats

A `SimpleMetricsListener` (`metrics.listener`, the same class every other batch script already
uses) is created at the top of `run()`, used for every `increment()` call in the move loop, and
**returned** by `run()` so the caller can report and persist it:

```python
async def run(session, base_path) -> SimpleMetricsListener:
    metrics = SimpleMetricsListener()
    ...  # existing query, existing os.makedirs, then the per-file loop using metrics.increment(...)
    return metrics
```

`SimpleMetricsListener` needs one small addition: a `counters_dict()` method returning
`dict(self._counters)` (it currently only exposes `.print()`; nothing reads its counts
programmatically yet) — a minimal, backward-compatible addition, not a behavior change for any
existing caller.

Both branches of `main()` capture `run()`'s return value and, still inside the same
`async with AsyncSessionLocal() as session:` block (so `update_stats`'s flush lands in the same
transaction as everything else, committed explicitly before the block exits — the run isn't marked
`completed` by `tracked_run`/`finish_existing_run` until after this point, so a failure here still
correctly fails the run rather than silently losing the stats):

```python
metrics = await run(session, base_path)
await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
await session.commit()
```

`metrics.print()` (called once in `main()`, after both branches, using the returned object) writes
to stdout, which lands in the run's log file (`logs/{env}/move_flagged_<timestamp>.log`, per the
admin batch controller's existing log-path convention) — matching how every other batch script
already reports counts. `update_stats` persists the same counts onto the `batch_runs` row so
they're queryable later even without reading the log file.

### Chaining unregister_deleted_images

After `move_flagged`'s own tracked run commits (successfully — regardless of any per-file errors
logged above, since those no longer fail the run), `main()` calls
`unregister_deleted_images.main(trigger=trigger)` directly:

```python
async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(settings.BASE_PATH)
                metrics = await run(session, base_path)
                await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
                await session.commit()
    else:
        async with tracked_run(kind="move_flagged", trigger=trigger) as run_id:
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(settings.BASE_PATH)
                metrics = await run(session, base_path)
                await BatchRunRepository(session).update_stats(run_id, **metrics.counters_dict())
                await session.commit()

    metrics.print()
    await unregister_deleted_images.main(trigger=trigger)
```

This creates a **second, independent** `batch_runs` row (`kind="unregister_deleted_images"`) via
that function's own existing `tracked_run`/`finish_existing_run` machinery — unchanged, reused
as-is. It inherits the same `trigger` value move_flagged itself was invoked with (manual,
scheduled, or admin-triggered), not hardcoded to `"manual"`. If `unregister_deleted_images.main()`
raises, its own `tracked_run` marks that row `failed` and re-raises; this propagates out of
`move_flagged.main()` normally (standard, matching how every batch script signals failure to its
caller today) — `move_flagged`'s own row is already correctly `completed` by that point regardless.

Both branches (`run_id is not None` — the admin/wrapper-driven path — and the plain `tracked_run`
path) bind `metrics` the same way, so the single `metrics.print()` call after the `if`/`else`
covers either path.

### Testing

`batch/tests/test_move_flagged.py` (new file, mocked session/filesystem — no real DB or I/O,
matching this project's existing `batch/tests/` convention):

- A `FileNotFoundError` on one file's `shutil.move()` doesn't abort the loop — remaining files
  still get moved, and `metrics.counters_dict()` shows both `moved` and `error.file_not_found`
  with the right counts.
- A different exception type on one file increments `error.move_failed` instead, same
  non-aborting behavior.
- `BatchRunRepository.update_stats` is called with the run's `run_id` and the accumulated counter
  dict.
- `unregister_deleted_images.main()` is called once, after move_flagged's own tracked-run block
  exits, with the same `trigger` value `move_flagged.main()` was called with.

## Rollout

1. Add `SimpleMetricsListener.counters_dict()`.
2. Update `move_flagged.py`: per-file try/except, metrics, `update_stats` call, chained
   `unregister_deleted_images.main()` call in both `main()` branches.
3. Add `batch/tests/test_move_flagged.py`.
