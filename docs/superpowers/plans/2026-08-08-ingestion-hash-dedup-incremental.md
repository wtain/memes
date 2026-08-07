# Ingestion Hash-Dedup Incremental Re-Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `batch/ingest_hash_dedup.py` be re-run against an already-active ingestion run — adding newly-dropped files to the same batch instead of refusing to run at all — while closing the concurrency gap this introduces.

**Architecture:** Single task. Three small, testable functions (`acquire_run_lock`, `resolve_batch`, `accumulate_stats`) plus an updated `main()`, all in the one file this feature is about. Doc updates (module docstring, `CLAUDE.md`, the operator runbook) are folded into the same task since they all describe this one change — no independent reviewable unit exists to split them into.

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, Postgres advisory locks (`pg_try_advisory_xact_lock`), pytest + pytest-asyncio against a real disposable test DB (`ocrdb_test`).

## Global Constraints

- No changes to `run()`, `hash_incoming_files()`, `dedupe_in_batch()`, `dedupe_cross_corpus()`, or `register_and_move_to_base_path()` in `batch/ingest_hash_dedup.py` — all already correct for this use case.
- No new CLI flags — reusing an active run is the script's new default behavior, not opt-in.
- No changes to `ingest_find_duplicates.py`, `ingest_promote.py`, or `ingest_abort.py`.
- Concurrent invocation of the *other* ingestion scripts is explicitly out of scope — do not add locking or guards to them.
- The advisory lock must be acquired unconditionally at the very top of `main()`, before `resolve_batch()` — it protects both the fresh-run and the reuse path uniformly.
- `main()` must only call `runs_repo.fail(batch_id, ...)` on exception when this invocation created the run (`is_new_run` is `True`) — a re-join failure must leave the run `started` and resumable, not `failed`.
- No API endpoint or frontend change.

---

### Task 1: Incremental re-runs, advisory lock, and doc updates

**Files:**
- Modify: `batch/ingest_hash_dedup.py` (module docstring + `main()`, plus three new functions)
- Test: `tests/integration/test_ingest_hash_dedup.py`
- Modify: `CLAUDE.md` (ingestion pipeline documentation)
- Modify: `docs/runbooks/ingestion-pipeline.md`

**Interfaces:**
- Produces: `acquire_run_lock(session) -> bool`, `resolve_batch(runs_repo: BatchRunRepository) -> tuple[uuid.UUID, dict, bool]` (returns `(batch_id, existing_stats, is_new_run)`), `accumulate_stats(existing: dict, new: dict) -> dict`. None of these are consumed by any other task — this is the only task in this plan.
- Consumes: `BatchRunRepository.get_active_run(kind="ingestion")` / `.create_run(kind, trigger, stage)` / `.fail(run_id, error)` / `.update_stats(run_id, **kwargs)` (all pre-existing, unchanged), `run(session, source_path, base_path, batch_id) -> dict` (pre-existing, unchanged).

- [ ] **Step 1: Read the current file**

Run: `grep -n "^async def\|^def" batch/ingest_hash_dedup.py`
Expected output confirms the current function list: `hash_incoming_files`, `dedupe_in_batch`, `dedupe_cross_corpus`, `register_and_move_to_base_path`, `run`, `main`. You'll be adding three new functions before `main` and rewriting `main` itself; everything before `run` stays untouched.

- [ ] **Step 2: Write the failing tests**

Add to `tests/integration/test_ingest_hash_dedup.py`. It already imports `pytest`, `BatchRunRepository`, and `Image` — extend the import from `batch.ingest_hash_dedup` to include the three new names, and add a new `from sqlalchemy.ext.asyncio import AsyncSession` import (needed for the lock test's independent second session — the test calls `acquire_run_lock()` itself rather than constructing raw SQL, so no `sqlalchemy.text` import is needed here):

```python
from batch.ingest_hash_dedup import (
    accumulate_stats,
    acquire_run_lock,
    dedupe_cross_corpus,
    dedupe_in_batch,
    hash_incoming_files,
    register_and_move_to_base_path,
    resolve_batch,
    run,
)
```

Then add these new test sections at the end of the file:

```python
# --------------------------------------------------------------------------
# resolve_batch
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_batch_creates_new_run_when_none_active(db_session):
    runs_repo = BatchRunRepository(db_session)

    batch_id, existing_stats, is_new_run = await resolve_batch(runs_repo)

    assert is_new_run is True
    assert existing_stats == {}
    run_row = await runs_repo.get_run(batch_id)
    assert run_row.status == "started"
    assert run_row.stage == "hash_dedup"


@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_batch_reuses_active_run(db_session):
    runs_repo = BatchRunRepository(db_session)
    existing_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="tier_a_review")
    await runs_repo.update_stats(existing_id, intake=3, registered=2)

    batch_id, existing_stats, is_new_run = await resolve_batch(runs_repo)

    assert is_new_run is False
    assert batch_id == existing_id
    assert existing_stats == {"intake": 3, "registered": 2}


# --------------------------------------------------------------------------
# accumulate_stats
# --------------------------------------------------------------------------

def test_accumulate_stats_sums_matching_keys():
    result = accumulate_stats({"intake": 3, "registered": 2}, {"intake": 5, "registered": 1})

    assert result == {"intake": 8, "registered": 3}


def test_accumulate_stats_adds_new_keys_not_in_existing():
    result = accumulate_stats({}, {"intake": 4, "registered": 4})

    assert result == {"intake": 4, "registered": 4}


# --------------------------------------------------------------------------
# acquire_run_lock
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_acquire_run_lock_blocks_while_another_session_holds_it(db_engine, db_session):
    async with AsyncSession(db_engine, expire_on_commit=False) as other_session:
        held = await acquire_run_lock(other_session)
        assert held is True  # sanity check: the other session actually got it

        blocked = await acquire_run_lock(db_session)
        assert blocked is False

        await other_session.rollback()  # release

    acquired_after_release = await acquire_run_lock(db_session)
    assert acquired_after_release is True
```

Note: `test_resolve_batch_reuses_active_run` calls `create_run` only once in its test body — this is safe from the one-active-run-per-kind unique index (no second `create_run` call in the same test), unlike some other tests in this file's neighbors that need the "commit the first run before creating a second" workaround.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingest_hash_dedup.py -k "resolve_batch or accumulate_stats or acquire_run_lock" -v`
Expected: FAIL with `ImportError: cannot import name 'accumulate_stats' from 'batch.ingest_hash_dedup'` (or similar for the other two names)

- [ ] **Step 4: Update `batch/ingest_hash_dedup.py`**

First, update the module docstring. Replace:

```python
"""
Ingestion stage 1: hash-based dedup of a new image batch, before any embeddings exist.

See docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md. This is Stage 0
(intake) + Stage 1 (hash dedup) only -- Tier A/B near-duplicate review (embeddings, OCR,
human review) are later phases, not implemented here.

Flow, for every regular file directly in PATH_INGESTION_SOURCE:
  1. In-batch: files with identical content hashes are deduped, keeping one (lexicographic
     order -- they're byte-identical, so it doesn't matter which); the rest move to
     PATH_INGESTION_SOURCE/duplicates/.
  2. Cross-corpus: survivors whose hash matches an existing *active* image's content_hash
     also move to duplicates/ -- same tier as in-batch matches, both are exact-hash
     decisions, just compared against a different set.
  3. Remaining survivors are registered as `pending` Image rows (content_hash stored at
     registration time, so this check never needs to re-hash the existing corpus on a
     future run) and their files move into BASE_PATH, same filename, ready for Tier A.

Known limitation: matches trends_batch.py's crash-safety posture, not a stricter one --
the batch_runs row and whatever registrations/moves happened before a failure are
committed regardless (via a `finally: await session.commit()`), so the run is marked
`failed` but any already-registered pending images survive as-is rather than being rolled
back. Not addressed further here -- not worth over-engineering before this pipeline has
run against real data.
"""
```

with:

```python
"""
Ingestion stage 1: hash-based dedup of a new image batch, before any embeddings exist.

See docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md and
docs/superpowers/specs/2026-08-08-ingestion-hash-dedup-incremental-design.md. This is
Stage 0 (intake) + Stage 1 (hash dedup) only -- Tier A/B near-duplicate review
(embeddings, OCR, human review) are later phases, not implemented here.

Flow, for every regular file directly in PATH_INGESTION_SOURCE:
  1. In-batch: files with identical content hashes are deduped, keeping one (lexicographic
     order -- they're byte-identical, so it doesn't matter which); the rest move to
     PATH_INGESTION_SOURCE/duplicates/.
  2. Cross-corpus: survivors whose hash matches an existing *active* image's content_hash
     also move to duplicates/ -- same tier as in-batch matches, both are exact-hash
     decisions, just compared against a different set.
  3. Remaining survivors are registered as `pending` Image rows (content_hash stored at
     registration time, so this check never needs to re-hash the existing corpus on a
     future run) and their files move into BASE_PATH, same filename, ready for Tier A.

Safe to re-run at any point while an ingestion run is active -- rather than refusing, this
script joins the active run (reusing its batch_id, accumulating stats across invocations)
so newly-dropped files can be added to an in-progress batch. Newly-added pending images
need the rest of the pipeline re-run to get review coverage: extract_text_from_memes.py
--status pending, then ingest_find_duplicates.py for whichever tier(s) are relevant --
both are already safe to re-run against the same batch (see CLAUDE.md's ingestion pipeline
section). A Postgres advisory lock (acquire_run_lock) serializes concurrent invocations of
this script against each other, so two operators re-joining the same run at once don't race
on PATH_INGESTION_SOURCE's filesystem state.

Known limitation: matches trends_batch.py's crash-safety posture, not a stricter one --
the batch_runs row and whatever registrations/moves happened before a failure are
committed regardless (via a `finally: await session.commit()`). A failure while creating a
brand-new run marks it `failed`; a failure while joining an already-active run leaves it
`started` (not `failed`) so a possibly-partially-reviewed-or-promoted batch isn't destroyed
by a Stage-1 re-run error -- see resolve_batch's `is_new_run` return value. Either way,
already-registered pending images survive as-is rather than being rolled back. Not
addressed further here -- not worth over-engineering before this pipeline has run against
real data at volume.
"""
```

Next, add `text` to the existing `sqlalchemy` import. Change:

```python
from sqlalchemy import select
```

to:

```python
from sqlalchemy import select, text
```

Next, add the three new functions immediately before `async def main` (i.e., right after `run()` ends, currently the blank lines before `async def main(env: str | None) -> None:`):

```python
async def acquire_run_lock(session) -> bool:
    """Postgres advisory lock scoped to the current transaction, released automatically at
    commit/rollback. Serializes concurrent ingest_hash_dedup.py invocations against each
    other for this environment (each environment is a separate Postgres instance, so one
    fixed key needs no environment-scoping) -- without it, two operators re-joining the
    same active run at once could race on PATH_INGESTION_SOURCE's filesystem state."""
    return (await session.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtext('ingest_hash_dedup')::bigint)")
    )).scalar_one()


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

Note: `resolve_batch` takes a `BatchRunRepository` as its type hint — this file doesn't currently import that name as a type (only uses it as a value via `BatchRunRepository(session)`), but it's already imported at module level (`from repository.batch_runs import BatchRunRepository`), so no new import is needed for the type hint itself.

Finally, replace the existing `main()`:

```python
async def main(env: str | None) -> None:
    load_env(env)
    source_path = settings.get("PATH_INGESTION_SOURCE")
    if not source_path:
        raise RuntimeError("PATH_INGESTION_SOURCE is required but not set")
    base_path = settings.BASE_PATH

    async with AsyncSessionLocal() as session:
        runs_repo = BatchRunRepository(session)

        active_run = await runs_repo.get_active_run(kind="ingestion")
        if active_run is not None:
            raise RuntimeError(
                f"An ingestion run is already in progress (run_id={active_run.run_id}, "
                f"stage={active_run.stage}) -- finish or abandon it before starting a new one."
            )

        batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
        stats = None
        try:
            stats = await run(session, source_path, base_path, batch_id)
            # Not runs_repo.commit() -- that would mark the *whole* ingestion run
            # completed, but Stage 1 is only the first of several stages spanning
            # multiple later script invocations and human review. The run stays
            # `started` (and so still blocks a second concurrent run, correctly) until
            # promotion -- not yet implemented -- finishes it.
            await runs_repo.update_stats(batch_id, **stats)
        except Exception as e:
            await runs_repo.fail(batch_id, error=str(e))
            raise
        finally:
            await session.commit()

    print(f"Ingestion run {batch_id}: {stats}")
```

with:

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
            # Not runs_repo.commit() -- that would mark the *whole* ingestion run
            # completed, but Stage 1 is only the first of several stages spanning
            # multiple later script invocations and human review. The run stays
            # `started` (and so still blocks a second concurrent run, correctly) until
            # promotion -- not yet implemented -- finishes it.
            await runs_repo.update_stats(batch_id, **accumulate_stats(existing_stats, stats))
        except Exception as e:
            if is_new_run:
                await runs_repo.fail(batch_id, error=str(e))
            raise
        finally:
            await session.commit()

    print(f"Ingestion run {batch_id}: {stats}")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingest_hash_dedup.py -v`
Expected: all pass (16 passed — the 11 pre-existing tests plus the 5 new ones)

- [ ] **Step 6: Run the full `tests/integration/` root**

This task touches a script other ingestion scripts and the review UI depend on transitively (via `batch_runs`/`Image` state) — run the full root as a regression check.

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
Expected: all pass, no new failures.

- [ ] **Step 7: Also run `batch/tests/`**

Run: `pytest batch/tests/`
Expected: all pass (sanity check; this task doesn't touch anything under `batch/tests/`'s own scope, but this repo's `CLAUDE.md` gotcha about never combining `Backend/tests/`/`tests/integration/`/`batch/tests/` in one pytest invocation means running it as its own separate command).

- [ ] **Step 8: Update `CLAUDE.md`'s ingestion pipeline documentation**

Read `CLAUDE.md`'s "Batch pipeline (execution order)" section, the `# Ingestion` block. Find the line describing `ingest_hash_dedup`:

```
ingest_hash_dedup           → Stage 1: hashes every file in PATH_INGESTION_SOURCE, dedupes
                               in-batch and against the active corpus's content_hash, registers
                               survivors as `pending` images (content_hash + ingestion_batch_id
                               set at registration) and moves them into BASE_PATH. Refuses to
                               start if another ingestion run (batch_runs, kind="ingestion") is
                               already in progress.
```

Replace the last sentence ("Refuses to start if another ingestion run...") with a description of
the new behavior:

```
ingest_hash_dedup           → Stage 1: hashes every file in PATH_INGESTION_SOURCE, dedupes
                               in-batch and against the active corpus's content_hash, registers
                               survivors as `pending` images (content_hash + ingestion_batch_id
                               set at registration) and moves them into BASE_PATH. Safe to re-run
                               at any point while an ingestion run is active -- joins the active
                               run (same batch_id, stats accumulate across invocations) instead of
                               refusing, so newly-dropped files can be added to an in-progress
                               batch. A Postgres advisory lock serializes concurrent invocations of
                               this script against each other (not the other ingestion scripts).
                               Newly-added images need extract_text_from_memes --status pending and
                               ingest_find_duplicates.py (both tiers, as applicable) re-run
                               afterward to get review coverage -- both are already safe to re-run
                               against the same batch.
```

Match the exact indentation/wrapping style of the surrounding block rather than the snippet's own
line breaks verbatim.

- [ ] **Step 9: Update `docs/runbooks/ingestion-pipeline.md`**

Read the file's `## Concurrency` section (currently ends with a sentence about starting a second
batch requiring finishing or resolving the current one first) and add a short paragraph or bullet
describing the new re-run behavior: `ingest_hash_dedup.py` can now be re-run at any point to add
newly-dropped files to the active run instead of being blocked, and doing so requires re-running
`extract_text_from_memes --status pending` and `ingest_find_duplicates.py` (both tiers, as
applicable) afterward for the new images to get review coverage. Also note, for completeness
alongside the existing `ingest_abort.py` mention in this same section (added on the ingestion-abort
branch), that concurrent *invocations of the same* `ingest_hash_dedup.py` script are now
serialized via an advisory lock, so a second operator's simultaneous re-run attempt gets a clear
"try again shortly" error rather than racing.

- [ ] **Step 10: Commit**

```bash
git add batch/ingest_hash_dedup.py tests/integration/test_ingest_hash_dedup.py CLAUDE.md docs/runbooks/ingestion-pipeline.md
git commit -m "feat: allow ingest_hash_dedup.py to re-run against an active ingestion run"
```
