# Ingestion Abort — Final Whole-Branch Review

**Branch:** `worktree-ingestion-abort`
**Spec:** `docs/superpowers/specs/2026-08-07-ingestion-abort-design.md`
**Plan:** `docs/superpowers/plans/2026-08-07-ingestion-abort.md`
**Reviewed range:** `8d5a483..f034dd6` (5 commits)
**Reviewers:** Sonnet (Task 1 + Task 2 reviews, scoped re-review) then Opus (final whole-branch review), subagent-driven-development skill

## Summary

Two-task feature adding `batch/ingest_abort.py`, a CLI script that abandons the currently active
ingestion run: undoes every `pending`/`rejected` image it registered (moves files back to
`PATH_INGESTION_SOURCE`, deletes rows — FK cascades clean up embeddings/OCR/duplicate-candidate
rows automatically) and marks the run `aborted`, freeing the one-active-run-per-kind lock. Both
per-task reviews were clean (no Critical/Important findings). The final whole-branch review found
no Critical issues but surfaced two genuine Important findings only visible at the whole-branch
level — neither task's diff alone touched the file involved in Finding 1, and Finding 2 required
checking a doc file neither task's diff modified.

## Process note: implementer left stray edits in the main checkout (again)

The Task 1 implementer subagent's actual commit landed correctly on the worktree branch, but it
also left incomplete, uncommitted duplicate scratch edits directly in the main checkout — a repeat
of the exact failure mode already documented on the `remove_singletons` and
`build_image_embeddings` branches earlier this session, despite the dispatch's explicit
"re-verify immediately before you commit" instruction. Caught via the controller's independent
post-dispatch verification (`git branch --contains`, `git status` on both checkouts) before
proceeding to review; confirmed via diff that the stray edits contained no content beyond what was
already in the worktree's commit, then discarded from `main` with explicit user confirmation.
Task 2's implementer, dispatched with the same warning, did not repeat this — its commit was
clean on the first try. Not yet a solved problem, but not worsening either.

## Findings and Resolution

### Task-level reviews: both Approved, no Critical/Important findings

Task 1 (`RunStatus.aborted`, `BatchRunRepository.abort()`, `IngestionRepository.list_abortable_images()`):
one Minor (double blank line), fixed directly by the controller (commit `bcc2b47`).

Task 2 (`batch/ingest_abort.py` + tests + `CLAUDE.md` entry): reviewer independently verified —
against the live repository, not just the report — that the delete-then-abort-then-commit ordering
is genuinely atomic, per-image move failures are non-fatal, the `rejected`-subdir source path
matches the real convention in `image_store.py`, the no-active-run guard fires before any work, and
the cascade-delete test performs genuine row-level verification. Two Minor notes left unfixed as
non-blocking (a print statement reading a detached-but-safe object attribute; a pre-existing
codebase-wide broad-except convention).

### Final whole-branch review (Opus): two Important, six Minor — one fix round, now clean

**Important — fixed:**
1. `Backend/app/services/admin_batch_service.py`'s `_STATUS_MAP[run.status]` was a bare dict
   lookup with no fallback for the new shared `RunStatus.aborted` value — a latent `KeyError` risk
   (unreachable today, since only non-ingestion kinds reach this code, but real the moment that
   changes). Fixed: `.get(run.status, run.status)`.
2. `docs/runbooks/ingestion-pipeline.md` (the human-facing runbook, distinct from `CLAUDE.md`'s
   agent-facing index which this branch already updated) had no mention of the new abort
   capability — its Concurrency section still read as if no such mechanism existed. Fixed: both the
   Concurrency section and the "Handling a mistaken decision" section now name `ingest_abort.py`.

**Minor — fixed:**
- Unbounded `IN()` list in the delete (asyncpg's ~32767 bind-parameter cap) — replaced with the
  same predicate `list_abortable_images()` already uses, driven by `result.rowcount`. The
  implementer caught and correctly worked around a real conflict with the literal suggested code
  (moving the metric increment inside the `if rows:` guard would have broken a pre-existing test
  expecting `{"unregistered": 0}` when there's nothing to abort) — verified directly by both the
  controller and the scoped re-reviewer.
- Docstring's "Known limitation" paragraph extended to cover a file that exists but fails to move
  (previously only covered a missing file).
- A test whose name promised "does not abort remaining" but only ever set up one image — now
  exercises both a failing and a succeeding image in the same batch.
- Two cosmetic blank-line inconsistencies in a test file.
- `shared/schemas/ingestionrunstatus.schema.json`'s free-form description string updated to
  include `aborted` (no `enum` present, so no type regeneration needed).

**Minor — deliberately deferred, not fixed on this branch:**
- `ingest_abort.py` doesn't chain `remove_singletons` the way `unregister_deleted_images` does,
  despite performing the structurally identical cascade-delete operation that can reduce
  `tmp_clusters` clusters to singletons. This is a real, scope-expanding design decision (a new
  dependency plus another DB operation), not a bug — deferred as a follow-up rather than folded
  into this fix round, matching how the `remove_singletons` branch's own final review deferred an
  analogous cross-cutting finding (the status-filtered UI singleton case).

The scoped re-review confirmed all seven addressed findings, verified the one disclosed deviation
directly against the pre-existing test it was designed to preserve, and found no new breakage.

## Assessment

**Merged:** yes, after the fix round above (commit `f034dd6`) plus this bookkeeping. No Critical
findings at any review stage. The one recurring risk worth carrying forward: the
implementer-commits-to-main failure mode is not yet reliably prevented by dispatch-prompt wording
alone — worth further mitigation in a future session (e.g. a harness-level check) rather than
relying solely on stronger instructions.
