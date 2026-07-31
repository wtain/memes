# move_flagged Resilience, Stats, and Chaining — Review

Spec: `docs/superpowers/specs/2026-07-31-move-flagged-resilience-and-chaining-design.md`
Plan: `docs/superpowers/plans/2026-07-31-move-flagged-resilience-and-chaining.md`

Implemented via subagent-driven-development: 2 tasks, each with its own implementer + task
reviewer, a pre-flight scan that caught a conflict before any code was written, followed by a
final whole-branch review and one fix round.

## Pre-flight finding (before any implementer was dispatched)

Scanning the approved plan against the existing codebase found that
`tests/integration/test_move_flagged_tracking.py` (predates this branch) mocks `move_flagged.run`
as a bare `AsyncMock()` and never mocks `unregister_deleted_images`. Under the new design, that
would have both crashed (`**metrics.counters_dict()` unpacking a `MagicMock`) and — worse — let
the real, destructive `unregister_deleted_images.main()` fire against the integration test
database. Confirmed with the user and folded the fix into the plan's Task 2 before dispatching
Task 1.

## Per-task reviews

Both tasks passed their scoped review clean (spec compliant, no Critical/Important findings) —
after one process incident, described below.

1. **`SimpleMetricsListener.counters_dict()`** — approved. First attempt's implementer subagent
   committed this change directly to `main` instead of the assigned worktree (a known recurring
   failure mode this project has flagged before). Caught by the controller's post-dispatch
   verification before any review even started; `main` was reset back to its prior commit with the
   user's explicit confirmation (a single, unpushed, nothing-built-on-it commit — safe to discard).
   Re-dispatched with an explicit working-directory self-check; the retry landed correctly and
   passed review.
2. **`move_flagged.py` resilience/stats/chaining + integration test fix** — approved, zero
   findings. The implementer self-reported and self-corrected a second, smaller instance of the
   same directory-slip (a read-only pytest invocation briefly run from the main checkout, no
   commit); the controller independently found and discarded one resulting stray *uncommitted*
   edit left in `main`'s working tree, confirmed identical to Task 1's already-reviewed content,
   and confirmed `main`'s history and working tree were clean afterward. `batch/tests/` 42/42 and
   the full `tests/integration/` root 212/212 (real `ocrdb_test` DB) both passed.

## Final whole-branch review

Independently verified by the controller before acting (the review's own completion notification
flagged that its safety classifier was unavailable, so its key claims were re-derived from the
code directly rather than taken on trust):

- **Important — `batch/cleanup_flagged.py`** (pre-existing script, not touched by either task) was
  never updated: it called `move_flagged.run()` directly and discarded the return value, and is
  now fully redundant with `move_flagged.main()`'s new automatic chaining. Confirmed by reading the
  file. **User decision: delete it.**
- **Important — automating a destructive full-corpus reconcile.** `unregister_deleted_images`
  hard-deletes (self-committing, no outer transaction — confirmed in `repository/images.py`) any
  DB row whose file is missing on disk; it used to require a deliberate, separate operator trigger,
  and this branch made it fire automatically after every `move_flagged` run, including from the
  admin UI. **User decision: add a `--no-chain` CLI opt-out**, keeping automatic chaining as the
  default (the approved, shipped behavior) while giving an operator with terminal access an escape
  hatch.
- **Important — an uncaught `BatchAlreadyRunningError`** from the chained call (if
  `unregister_deleted_images` were triggered concurrently elsewhere) would propagate and crash the
  process with no durable record — confirmed by tracing `tracked_run`'s exception handling and
  `Backend/app/batch_subprocess.py`'s warning-only logging on non-zero exit. Fixed: caught and
  logged as a clear skip message.
- Minor findings (test coverage for dotted-key counter names through the real `update_stats`,
  that method's `**kwargs` signature being fragile against a counter literally named `run_id`, log-file
  correlation gap for the chained run, `trigger="manual"` on an admin-triggered chain being
  semantically imprecise) — recorded, not fixed; none are load-bearing and all are pre-existing or
  cosmetic.

## Fix round (1 of 1, clean)

- Deleted `batch/cleanup_flagged.py` (no dedicated test existed for it; only remaining references
  are two historical spec/review docs from 2026-06-29, left untouched).
- Added `chain: bool = True` to `main()` (default preserves existing scheduler/admin-controller
  behavior, since `run_wrapper.py` never passes this argument) and a `--no-chain` CLI flag.
- Folded the `BatchAlreadyRunningError` catch into the same `if chain:` block.
- Two new tests: `chain=False` skips the chained call; a `BatchAlreadyRunningError` from the
  chained call doesn't propagate.
- Doc sync: `CLAUDE.md`'s Maintenance line and `backend_api.md`'s admin `move_flagged` example both
  updated.
- `batch/tests/` 44/44 (42 prior + 2 new); `tests/integration/test_move_flagged_tracking.py` 4/4
  (real DB reachable).

Scoped re-review confirmed all three findings ADDRESSED with no new breakage, nothing out of the
approved scope touched (`update_stats`'s signature, `batch_subprocess.py`, and the spec's status
line were all confirmed untouched, as instructed).

## Outcome

**Ready to merge.** All Critical/Important findings from both per-task and whole-branch review are
fixed and re-verified. No residual findings.
