# Ingestion Hash-Dedup Incremental Re-Runs — Design

Status: planned
Plan: docs/superpowers/plans/2026-08-08-ingestion-hash-dedup-incremental.md

**Date:** 2026-08-08.

Allows `batch/ingest_hash_dedup.py` (ingestion Stage 1) to be re-run against an already-active
ingestion run, adding newly-dropped files to the same batch instead of refusing to run at all.
This is the second of two specs from the "ingestion abort or incremental first step" ask; the
abort half (`batch/ingest_abort.py`) is already implemented and merged.

---

## Motivation

`ingest_hash_dedup.py`'s `main()` unconditionally refuses to run whenever any ingestion run is
active, regardless of stage — its own error message already hints at the gap: "finish or abandon
it before starting a new one." Since a run can sit at any stage for an arbitrary amount of time
(human review has no timeout), there's currently no way to add newly-dropped files to an
in-progress batch — the operator has to wait for the whole batch to finish, or abandon it.

The rest of the pipeline already tolerates this naturally: `ingest_find_duplicates.py` re-probes
*all* pending images in the batch every time it's called (candidate-pair inserts are `ON CONFLICT
DO NOTHING`), and `extract_text_from_memes.py --status pending` only processes images that don't
have OCR yet. Newly-added pending images would be picked up correctly by a re-run of either,
without any code change to those scripts. `run()` itself (this script's Stage 1 core logic) also
already takes `batch_id` as a plain parameter with no assumption about freshness — it's already
reuse-safe. The only actual blocker is `main()`'s guard.

## Scope

**In scope:** `batch/ingest_hash_dedup.py`'s `main()` — reusing an active run instead of refusing,
accumulating stats across re-runs, not failing an already-in-progress run over a re-join error,
and a Postgres advisory lock serializing concurrent invocations of this script against each other.

**Out of scope:**
- No changes to `run()`, `hash_incoming_files()`, `dedupe_in_batch()`, `dedupe_cross_corpus()`, or
  `register_and_move_to_base_path()` — all already correct for this use case as-is.
- No changes to `ingest_find_duplicates.py`, `ingest_promote.py`, or `ingest_abort.py` — the
  operator is expected to re-run `build_image_embeddings --status pending --incremental`,
  `extract_text_from_memes --status pending`, and `ingest_find_duplicates.py` (both tiers, as
  applicable) after adding files mid-review, to get review coverage for the newly-added images.
  This is a documentation callout, not a code change. Skipping the embeddings step specifically is
  not just incomplete — `ingest_find_duplicates.py`'s probe is an inner join against `embeddings`,
  so an image with none is silently excluded from review entirely and could reach
  `ingest_promote.py` unreviewed.
- **Concurrent invocation of the *other* ingestion scripts against each other**
  (`ingest_promote.py`, `ingest_find_duplicates.py`, `ingest_abort.py` racing one another) is a
  pre-existing property those scripts already had before this change and is unaffected by it — not
  addressed here. One pairing is *not* pre-existing, though: `ingest_hash_dedup.py` re-joining an
  active run isn't itself serialized against a concurrent `ingest_promote.py` or `ingest_abort.py`
  run (the advisory lock only covers concurrent invocations of `ingest_hash_dedup.py` against
  itself) — before this change, `ingest_hash_dedup.py` simply refused to run at all whenever a
  batch was active, so this specific race couldn't happen. Judged low-probability on a
  single-operator manual workflow and addressed with a runbook callout rather than a second lock,
  not a code fix — see `docs/runbooks/ingestion-pipeline.md`'s Concurrency section.
- No new CLI flags — the new reuse-instead-of-refuse behavior is the script's new default, not
  opt-in (see rationale below).
- No API endpoint or frontend change — this script has never been wired into the admin
  controller or frontend, and stays that way.

## Design

### Why no opt-in flag

There is one `PATH_INGESTION_SOURCE` per environment and at most one active ingestion run at a
time (enforced by the existing partial unique index on `batch_runs`). Any new files sitting in
that directory while a run is active almost certainly belong to the same batch — there's no
mechanism in this codebase for expressing "these files are logically a separate batch, don't mix
them with the active one." The old unconditional refusal was a missing feature, not an
intentional safety rail worth preserving behind a flag.

### The concurrency gap this introduces, and its fix

Today, two operators racing to start a *fresh* run are already safe: `create_run()`'s
`INSERT` collides with the DB's partial unique index (`ix_batch_runs_one_active_per_kind`,
`WHERE status = 'started'`), so the loser gets a clean `BatchAlreadyRunningError` before ever
touching the filesystem — `run()` (which does the actual file listing/hashing/moving) is never
called until *after* a run is secured.

Allowing re-joins breaks that guarantee for the "already active" case: `get_active_run()` is a
plain `SELECT`, not an atomic claim. Two operators both re-joining the same active run at the same
moment could both pass that check and both proceed into `run()` concurrently — a genuine race on
`PATH_INGESTION_SOURCE`'s filesystem state (both processes could see and try to move the same
file before either claims it).

Fix: a Postgres advisory lock (`pg_try_advisory_xact_lock`), acquired unconditionally at the very
top of `main()`, before any other work — including before the old create-vs-reuse decision, so it
covers both cases uniformly. Transaction-scoped, so it releases automatically when `main()`'s
session commits or rolls back; no explicit unlock needed. A second concurrent invocation fails to
acquire it and raises immediately, before touching the filesystem or the DB beyond the lock
attempt itself:

```python
from sqlalchemy import text

async def acquire_run_lock(session) -> bool:
    """Postgres advisory lock scoped to the current transaction, released automatically at
    commit/rollback. Serializes concurrent ingest_hash_dedup.py invocations against each
    other for this environment (each environment is a separate Postgres instance, so one
    fixed key needs no environment-scoping) -- without it, two operators re-joining the
    same active run at once could race on PATH_INGESTION_SOURCE's filesystem state."""
    return (await session.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtext('ingest_hash_dedup')::bigint)")
    )).scalar_one()
```

`hashtext(...)::bigint` avoids hand-picking and documenting an arbitrary magic integer — Postgres's
`hashtext()` is deterministic given the same input string, unlike Python's salted `hash()`.

### Reusing an active run, and not failing it on a re-join error

```python
async def resolve_batch(runs_repo: BatchRunRepository) -> tuple:
    """Reuse the currently active ingestion run if one exists (letting newly-dropped files
    join the same batch instead of being blocked), or start a new one. Returns
    (batch_id, existing_stats, is_new_run)."""
    active_run = await runs_repo.get_active_run(kind="ingestion")
    if active_run is not None:
        print(f"Joining active ingestion run {active_run.run_id} (stage={active_run.stage})")
        return active_run.run_id, (active_run.stats or {}), False
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    return batch_id, {}, True


def accumulate_stats(existing: dict, new: dict) -> dict:
    """Add this invocation's counts on top of whatever the batch has accumulated so far, so
    re-running Stage 1 against an already-active run reports running totals instead of
    silently overwriting earlier numbers -- BatchRunRepository.update_stats() itself merges
    by overwrite, not by sum, so this has to happen before calling it."""
    return {key: existing.get(key, 0) + value for key, value in new.items()}
```

`main()`'s exception handling changes to only mark the run `failed` when this invocation created
it. A re-join failure leaves the run `started` and resumable instead of destroying a possibly
partially-reviewed or partially-promoted batch:

```python
async def main(env: str | None) -> None:
    load_env(env)
    source_path = settings.get("PATH_INGESTION_SOURCE")
    if not source_path:
        raise RuntimeError("PATH_INGESTION_SOURCE is required but not set")
    base_path = settings.BASE_PATH

    async with AsyncSessionLocal() as session:
        if not await acquire_run_lock(session):
            raise RuntimeError(
                "Another ingest_hash_dedup.py run is already in progress for this "
                "environment -- try again shortly."
            )

        runs_repo = BatchRunRepository(session)
        batch_id, existing_stats, is_new_run = await resolve_batch(runs_repo)

        stats = None
        try:
            stats = await run(session, source_path, base_path, batch_id)
            await runs_repo.update_stats(batch_id, **accumulate_stats(existing_stats, stats))
        except Exception as e:
            if is_new_run:
                await runs_repo.fail(batch_id, error=str(e))
            raise
        finally:
            await session.commit()

    print(f"Ingestion run {batch_id}: {stats}")
```

`resolve_batch`/`accumulate_stats` are plain, independently testable functions with no `main()`
wiring needed in their own tests — matching the precedent already established for
`ingest_abort.py`/`ingest_promote.py` (neither has a `main()`-level test either).

## Testing

Add to `tests/integration/test_ingest_hash_dedup.py`:

- `resolve_batch`: reuses an existing active run's `run_id`/`stats` when one exists; creates a
  fresh run (`stage="hash_dedup"`, empty existing stats) when none does.
- `accumulate_stats`: sums matching keys across two dicts; a key present only in `new` is added
  fresh (no `KeyError` on a missing `existing` key).
- `acquire_run_lock`: using two independent real sessions bound directly to the session-scoped
  `db_engine` fixture (not the savepoint-wrapped `db_session` fixture — advisory xact locks are
  scoped to the real transaction, which the savepoint wrapping doesn't end), matching the
  established pattern in `tests/integration/test_batch_runs_repository.py`'s
  `test_pool_usable_after_rollback_following_already_running_error`:
  1. One session acquires the lock and leaves its transaction open (uncommitted).
  2. A second, independent session's attempt returns `False`.
  3. After the first session's transaction ends (rollback), a fresh attempt succeeds (`True`).
- A `main()`-level failure test is not added, matching the sibling-script precedent already
  established for `ingest_abort.py`/`ingest_promote.py` — but confirm via reading (not a new test)
  that the `is_new_run` branch in the updated `main()` correctly gates the `fail()` call, since
  this is the one piece of new logic inside `main()` itself.

## Rollout

1. Add `acquire_run_lock`, `resolve_batch`, `accumulate_stats`, and update `main()` in
   `batch/ingest_hash_dedup.py`, plus the module docstring (describe the new re-run/join
   behavior and the advisory-lock serialization).
2. Update `CLAUDE.md`'s ingestion pipeline documentation to note `ingest_hash_dedup.py` is now
   safe and expected to be re-run at any point to add newly-dropped files to the active batch, and
   that doing so requires re-running `build_image_embeddings --status pending --incremental`,
   `extract_text_from_memes --status pending`, and `ingest_find_duplicates.py` (both tiers, as
   applicable) afterward for the new images to get review coverage.
3. Update `docs/runbooks/ingestion-pipeline.md` (the operator-facing runbook) with the same
   callout, matching the diligence the `ingest_abort.py` branch's final review required for that
   file specifically (a `CLAUDE.md`-only update was found insufficient there).
