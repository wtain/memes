# Admin Batch Controller — Review

Covers the full 3-spec sequence, all implemented on `worktree-admin-batch-controller`:

1. `docs/superpowers/specs/2026-07-28-batch-run-trigger-tracking-design.md` (trigger column,
   `BatchAlreadyRunningError`, one-active-per-kind index)
2. `docs/superpowers/specs/2026-07-28-batch-run-wrapper-design.md` (`batch/registry.py`,
   `batch/run_wrapper.py`, `finish_existing_run`)
3. `docs/superpowers/specs/2026-07-28-admin-batch-controller-design.md` (the HTTP surface itself)

**Branch:** `worktree-admin-batch-controller`, 16 commits ahead of `main`, 1 commit behind
(`392ba99` "Claudeignore added" — trivial, non-conflicting).

## Logic correctness against spec

All three specs' requirements are present and match design intent:

- `Backend/app/batch_subprocess.py` — `build_log_path`, `spawn_and_track` (with the `label`
  parameter added mid-plan for log attribution), `fire_and_forget` all present with the exact
  signatures later code depends on. `scheduler.py`'s `_spawn` now delegates to it; scheduler
  behavior (restart-safe timing, orphan recovery, per-tick error isolation, subprocess survival
  across cancellation/shutdown) is unchanged — confirmed by the untouched `test_scheduler.py`
  tests (`_run_tick`/`_safe_tick`/`_safe_initial_delay`/`start_scheduler`/`stop_scheduler`)
  still passing, plus the migrated real-subprocess-survival tests now living in
  `test_batch_subprocess.py`.
- `Backend/app/services/admin_batch_service.py` — all three operations (`trigger_run`, `get_run`,
  `list_runs`) match the spec's behavior: registry-based 404 for unknown batch names,
  `BatchAlreadyRunningError` → 409, explicit `session.commit()` before spawning (not left to
  `get_async_db`), kind-scoping → 404 for out-of-scope `run_id`s, status mapping
  (`started`→`running`, others unchanged).
  - Improvement over the plan's own draft: `AdminBatchService.__init__` takes the `AsyncSession`
    directly (`Backend/app/api/admin.py:39`: `AdminBatchService(BatchRunRepository(db), db)`)
    rather than reaching into `repo._session`, exactly the alternative the plan's "Stop and
    reconsider" note asked the implementer to prefer if it didn't complicate DI — it didn't, and
    the cleaner shape was used.
  - No leftover placeholder code (`__import__("os")` from the plan's draft) — real `import os` is
    used.
- `Backend/app/api/admin.py` — registered as `APIRouter(prefix="/admin/batches", ...)`, correctly
  relying on `main.py`'s `/api` prefix rather than double-prefixing. All three routes match the
  spec's paths/response models.
- `repository/batch_runs.py`'s new `list_runs` matches the service's call signature and its own
  integration test.
- `main.py` registers the router; `backend_api.md` documents all three endpoints with accurate
  request/response shapes and error codes, in the file's existing style.
- `docs/security/admin-permissions-todo.md` exists, tracking the deliberate no-auth gap.

## Code quality

No duplication, hard-coded values, or structural issues found. The extraction of
`batch_subprocess.py` is genuinely behavior-preserving — `scheduler.py` is left smaller and
clearer, and the hard-won docstrings explaining *why* `Popen`/daemon-thread (not
`asyncio.create_subprocess_exec`/`asyncio.to_thread`) were preserved verbatim in the new module,
which matters given two previously-fixed real bugs live in that reasoning.

## Test coverage

Ran every test root CLAUDE.md calls out, each independently per its own gotcha:

| Root | Result |
|---|---|
| `cd Backend && pytest` | 222 passed |
| `DATABASE_URL=... pytest tests/integration/` | 212 passed |
| `pytest tests/rules/` | 133 passed |
| `pytest batch/tests/` | 38 passed |

**One transient false failure during review, not a code defect:** the first `tests/integration/`
run showed 3 failures in `test_batch_runs_repository.py` (`test_get_most_recent_run_*`,
`test_list_runs_filters_by_kind_and_paginates`) caused by stale rows already present in the shared
`ocrdb_test` database's `batch_runs` table before this review's session even started. Root cause:
two tests in that file (`test_pool_usable_after_rollback_following_already_running_error`,
`test_pool_recovers_even_without_any_explicit_rollback`) deliberately bind directly to the
session-scoped `db_engine` fixture and issue real `session.commit()`s (by design — they're proving
connection-pool recovery across separate real sessions, which the per-test savepoint fixture can't
model). If that combination is ever interrupted before the session-scoped `db_engine` fixture's
`drop_all` teardown runs, the committed rows persist in the real test database and pollute the
next invocation. Confirmed by checking the table directly: it didn't exist (already cleaned up by
this review's own run) when queried afterward. Re-running `tests/integration/` immediately after
gave a clean 212/212 pass — not a regression in the code under review, but worth naming as a
fragility: an interrupted run of this one file can leave debris that fails unrelated-looking tests
in a later run, with a misleading error message (wrong-run_id / wrong-count assertions, not an
obvious "stale data" signal).

Test coverage itself matches the specs' own testing sections: unit tests for the service (mocked
repo/registry/subprocess) and router (mocked service via `dependency_overrides`), a focused
integration test for `list_runs`, and the subprocess module's real-child-process survival tests
carried over from the scheduler's own hard-won suite.

## Manual verification

Not performed live — this sandbox has no `environments/.env.*` files (confirmed: `environments/`
contains no `.env.*` entries), matching the exact limitation the plan's Task 3 Step 4 anticipated
and pre-approved a written-explanation fallback for. Everything checkable without a live backend
(imports, router wiring, full automated test suites above) was checked and passes.

## Requirement coverage

Every item in all three specs' "Design" and "Rollout" sections is implemented and exercised by a
test, including the two "Stop and reconsider" placeholder-avoidance notes the plan itself flagged
during its own writing.

## Recommendation

**Fixable and worth doing before merge:** none found — no action points.

**Suggested (not blocking):** consider a comment or short docstring note on
`test_pool_usable_after_rollback_following_already_running_error/test_pool_recovers_even_without_any_explicit_rollback`
flagging that an interrupted run of just this file (e.g. Ctrl-C, timeout) can leave real rows in
the shared test database until the next full-session teardown, so a future "why did an unrelated
test fail" investigation finds the answer faster. Not required — the tests are correct and the
one-time pollution encountered during this review was fully explained and did not recur.

**Merge:** ready. Rebase onto current `main` first (branch is 1 commit behind, no conflict
expected — the `main`-only commit is `.claudeignore` addition, an unrelated file).
