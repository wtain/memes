# remove_singletons Batch — Design

Status: approved

**Date:** 2026-08-05.

A new batch script, `batch/remove_singletons.py`, cleans up duplicate "clusters" that have been
reduced to a single member — meaningless as a duplicate group, but currently displayed as one by
the backend's duplicates review UI. Chained onto `unregister_deleted_images.main()`, since that's
what triggers the cascade-deletes that create singletons.

---

## Motivation

`tmp_clusters` rows cascade-delete when their `image_id` is deleted (`ondelete="CASCADE"` in
`Storage/models.py`). `unregister_deleted_images` deletes rows for images whose files are gone —
including flagged duplicates that `move_flagged` already moved out of `BASE_PATH`. A cluster that
had 2 members before that delete can be left with only 1 remaining `tmp_clusters` row afterward;
nothing currently cleans that up until the next full `clusterize.py` rebuild (a separate, more
expensive, not-automatically-chained step).

`Backend/app/repositories/image_repository.py`'s `get_duplicates_clustered()` — the query behind
the duplicates review page — has no singleton filtering; it just returns whatever rows exist for a
`cluster_id`, grouped. A leftover singleton shows up in that UI as a "duplicate cluster" containing
one image, which is meaningless to review. It also inflates
`Backend/app/repositories/diagnostics_repository.py`'s `duplicate_clusters` count.

## Scope

**In scope:** `batch/remove_singletons.py` (new), and chaining it onto
`unregister_deleted_images.main()`.

**Out of scope:**
- No run tracking (`tracked_run`/`batch_runs`) for `remove_singletons` itself — matching
  `clusterize.py`/`detect_file_duplicates.py`'s own precedent of untracked, simple maintenance
  scripts, not every batch script's tier. Not admin-triggerable via the registry for the same
  reason.
- No change to `clusterize.py`, `rebuild_duplicates.py`, or the threshold values either script
  uses — that's a separate, deferred investigation, not part of this change.
- No change to `move_flagged.py` itself — it already chains `unregister_deleted_images.main()`
  with `chain=True` by default, so this cascades through automatically
  (`move_flagged` → `unregister_deleted_images` → `remove_singletons`) without `move_flagged`
  needing any awareness of the new third step.

## Design

### `batch/remove_singletons.py`

```python
import argparse
import asyncio

from sqlalchemy import delete, func, select

from config.settings import load_env
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from Storage.models import TmpImageClusters


async def run(session) -> SimpleMetricsListener:
    metrics = SimpleMetricsListener()

    singleton_cluster_ids = (
        select(TmpImageClusters.cluster_id)
        .group_by(TmpImageClusters.cluster_id)
        .having(func.count() == 1)
    )
    result = await session.execute(
        delete(TmpImageClusters).where(TmpImageClusters.cluster_id.in_(singleton_cluster_ids))
    )
    metrics.add("removed", result.rowcount)
    return metrics


async def main() -> None:
    async with AsyncSessionLocal() as session:
        metrics = await run(session)
        await session.commit()
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
```

`run()` returns the `SimpleMetricsListener` (matching `move_flagged.py`'s established pattern) so
the chained caller can print it without a second round-trip.

### Chaining onto `unregister_deleted_images.py`

Add a `chain: bool = True` parameter to `main()` (default preserves current behavior for every
existing caller — the scheduler and admin controller both call `main(trigger=..., run_id=...)` via
`batch/run_wrapper.py` with no `chain` argument, same as `move_flagged.py`'s precedent), and a
`--no-chain` CLI flag:

```python
async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, chain: bool = True) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(os.getenv('BASE_PATH'))
                await run(session, base_path)
    else:
        async with tracked_run(kind="unregister_deleted_images", trigger=trigger):
            async with AsyncSessionLocal() as session:
                base_path = os.path.abspath(os.getenv('BASE_PATH'))
                await run(session, base_path)

    if chain:
        async with AsyncSessionLocal() as session:
            metrics = await remove_singletons.run(session)
            await session.commit()
        metrics.print()
```

(`import batch.remove_singletons as remove_singletons` added to this file's imports.) No
`BatchAlreadyRunningError` handling is needed here, unlike `move_flagged.py`'s chained call —
`remove_singletons` has no run tracking, so no such exception can occur from it.

`main()`'s `if __name__ == "__main__":` block gains a `--no-chain` flag, matching `move_flagged.py`
exactly:

```python
    parser.add_argument("--no-chain", action="store_true",
                         help="Skip the automatic remove_singletons cleanup after unregistering deleted images.")
    ...
    asyncio.run(main(chain=not args.no_chain))
```

### Existing test file this change breaks

`tests/integration/test_unregister_deleted_images_tracking.py` predates this change. Its 4 existing
tests monkeypatch `unregister_deleted_images.run` (the file-unregistering logic) but don't mock
anything for a chained call, since none exists yet. Once `main()` unconditionally calls the real
`remove_singletons.run(session)` after the tracked block, these tests would execute a real
(harmless, but unmocked and out of this file's stated scope — "run()'s actual... logic is unchanged
and untested here... these tests focus purely on whether main() creates/finishes the right BatchRun
row") DELETE against the test database. All 4 existing tests need `remove_singletons.run` mocked to
return a real `SimpleMetricsListener()`, matching the pattern `tests/integration/test_move_flagged_tracking.py`
already established for its own equivalent case. Two new tests should be added covering the
chaining itself: `chain=True` (default) calls `remove_singletons.run`; `chain=False` doesn't.

### Testing

`tests/integration/test_remove_singletons.py` (new — needs a real DB, since this is a real
`GROUP BY`/`HAVING`/`DELETE` query against real `tmp_clusters` rows with FK-constrained `image_id`
values):

- A cluster with exactly 1 member is removed.
- A cluster with 2+ members is left untouched.
- A mix of singleton and multi-member clusters in the same table: only the singleton is removed.

## Rollout

1. Add `batch/remove_singletons.py` + its integration test.
2. Wire the chain into `unregister_deleted_images.py`; fix the 4 existing tests in
   `tests/integration/test_unregister_deleted_images_tracking.py` that the chain call would
   otherwise break, and add the 2 new chain-behavior tests.
