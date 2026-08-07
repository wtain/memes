# Ingestion Hash-Dedup Incremental Re-Runs — Final Whole-Branch Review

**Branch:** `worktree-ingestion-hash-dedup-incremental`
**Spec:** `docs/superpowers/specs/2026-08-08-ingestion-hash-dedup-incremental-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-ingestion-hash-dedup-incremental.md`
**Reviewed range:** `d7604c0..a422392` (4 commits)
**Reviewers:** Sonnet (task review) then Opus (final whole-branch review), subagent-driven-development skill

## Summary

Single-task feature letting `batch/ingest_hash_dedup.py` (ingestion Stage 1) be re-run against an
already-active ingestion run — joining it (same `batch_id`, stats accumulate across invocations)
instead of refusing to run at all, so newly-dropped files can be added to an in-progress batch at
any point before promotion. A Postgres advisory lock (`pg_try_advisory_xact_lock`) closes a
concurrency gap this reuse behavior would otherwise introduce, and `main()`'s exception handling
now only marks the run `failed` when this invocation created it — a re-join failure leaves the run
`started` and resumable instead of destroying a possibly-partially-reviewed-or-promoted batch.

## Findings and Resolution

### Task-level review (Sonnet): Approved, no Critical findings

Independently verified — against the diff and `BatchRunRepository`'s actual source, not the
implementer's report — the lock-acquisition ordering, the `is_new_run`-gated `fail()` call (with
the `raise` confirmed unconditional on both branches), that none of the four pre-existing helper
functions or `run()` itself were touched, that no CLI flag was added, and that the advisory-lock
test genuinely proves cross-connection exclusivity (built on two independent `AsyncSession`
instances bound to `db_engine`, not two calls sharing the savepoint-wrapped `db_session` fixture).

One Important finding — a stale "refuses to start" sentence left in the runbook's "Running a
batch" walkthrough, outside the brief's literally-scoped edit location, contradicting the new
Concurrency section added in the same commit — and one Minor (a bare `tuple` return annotation
where the brief's own Interfaces line specified `tuple[uuid.UUID, dict, bool]`) were both fixed
directly by the controller, commit `fdcc5d6`, re-confirmed against the full 16-test suite.

### Final whole-branch review (Opus): With fixes, now clean

Traced the advisory lock's full window end to end (not just call ordering) and confirmed it
genuinely covers the entire filesystem-mutating `run()` call with no early-release path, no
stale-state window on release, and correct failure-path behavior (the lock-rejection error is
raised before the `try/finally`, so no spurious commit is attempted). Also traced every writer of
`batch_runs.stats` for `kind="ingestion"` across the whole pipeline to confirm `accumulate_stats`
has no reachable `TypeError`, and confirmed `resolve_batch()` never regresses a run's `stage`.

**Important — fixed:**
1. The documented re-run sequence for newly-added mid-batch images (`CLAUDE.md`, the runbook, the
   module docstring, and the spec's own Scope/Rollout text) omitted
   `build_image_embeddings --status pending --incremental`. This was not merely incomplete:
   `ingest_find_duplicates.py`'s probe is an inner join against `embeddings`, so an image with none
   is silently excluded from duplicate review entirely and could reach `ingest_promote.py`
   unreviewed — the exact failure class the two-tier review process exists to prevent. Fixed in all
   four locations.
2. `dedupe_cross_corpus()` doesn't catch an exact duplicate across two invocations of the same
   batch (it only checks `active`-status images, not this batch's own already-registered `pending`
   ones) — a genuinely new gap this branch introduces, since pre-change every batch came from
   exactly one invocation. Bounded, not silent: Tier A's corpus filter does include same-batch
   pending images, so the pair still surfaces there at distance 0, just as a human review item
   instead of an automatic Stage-1 exclusion. Widening `dedupe_cross_corpus()` would violate this
   branch's own scope constraint (no changes to that function) — documented as a known limitation
   in the module docstring instead, with a note that a fix would need its own follow-up spec.
3. Spec/plan lifecycle bookkeeping (this document, the spec's status line, the plan's checkboxes).

**Minor — fixed:**
- A runbook sentence attributing the one-active-run-per-kind lock to `ingest_hash_dedup.py` itself
  — stale as of this branch; the invariant is now held solely by the DB's partial unique index.
  Corrected in the same Concurrency-section pass as the Important fixes above.
- The spec's claim that concurrent invocation of the *other* ingestion scripts is "a pre-existing
  property... unaffected by" this change was slightly overstated: `ingest_hash_dedup.py`
  re-joining isn't itself serialized against a concurrent `ingest_promote.py`/`ingest_abort.py` run
  (the advisory lock only covers concurrent invocations of `ingest_hash_dedup.py` against itself),
  and this specific pairing *couldn't* race before this branch, since the script used to refuse
  outright whenever a run was active. Corrected the spec's text and added a one-sentence runbook
  callout (not a second lock — judged low-probability on a single-operator manual workflow).

**Minor — not fixed, explained:** stats under-report after a failed re-join (when `run()` raises
mid-batch, `update_stats` is skipped but the `finally: commit()` still persists whatever
registrations completed) — the reviewer's own assessment was "not worth code; worth knowing,"
since `stats` is advisory display data and this is the natural, correct consequence of the
deliberate decision not to fail a joined run.

## Assessment

**Merged:** yes, after the fix round above (commit `a422392`) plus this bookkeeping. No Critical
findings at either review stage. The one behaviorally real gap (Important #2, the cross-invocation
exact-duplicate case) is intentionally left as a documented limitation rather than fixed on this
branch, consistent with the plan's own scope boundary — a future spec would need to widen
`dedupe_cross_corpus()`'s corpus filter to close it.
