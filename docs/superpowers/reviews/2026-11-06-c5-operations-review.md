# C5 operations: review

Spec: `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-06-c5-operations-design.md`
Plan: `docs/superpowers/plans/2026-11-06-c5-operations.md`
Range reviewed: `0aa9e2b..f39607f` (eight tasks), then one fix wave (`f669ba1`).

Process: tasks 2, 3, 5 and 7 had a task review (task 3, the trigger and the lock, on the most capable model); tasks 1, 4 and 6 were
small or mechanical and were checked by the controller against their diffs, with the final review covering them; task 8 (documents)
was covered by the final review. One final whole-branch review on the most capable model, one fix wave. Suites at the end: Backend
775, `batch/tests/` 394, `tests/docs/` 15, the whole `tests/integration/` root 1478, frontend `tsc -b`, `eslint src/ --max-warnings 0`
and vitest 1026 (one unrelated `RecutReviewPage` keyboard test is occasionally flaky and passes on rerun).

## What was delivered

- `comics_pipeline` driver (OCR, embeddings, lemmas, tags, text-heavy, OCR-text embeddings; incremental, active images), registered
  and admin-triggerable; the tag step is skipped as `skipped_not_configured` where the tagging vocabulary is missing (`it`).
- The build endpoint commits, then starts the pipeline best effort through the `/admin/batches` spawn path; a refused or rolled-back
  build never starts it, and a busy or failing trigger never fails the build.
- An advisory lock (two-int key, comics namespace) serialises builds with byte-identical output.
- Statistics block `comics` (draft, built, kept, retired, `builtUnprocessed`).
- The OCR job skips hidden files; helpers made public; preview Retry; kept-thumbnail placeholder; named schemas for addability and
  by-image (the Kotlin generator learned nullable refs).
- Documents: `comics-rollout.md`, inventory, CLAUDE.md batch list, execution model, comics-build, tracker.

## What was fixed after the final review

- The residual-race advice in `comics-build.md` ("rebuild it") was wrong; it now says what recovers the file. The uniqueness wording
  was corrected (no unique constraint on `content_hash`).
- The advisory lock key moved to a two-int namespaced form so it cannot share a key space with `ingest_hash_dedup`.
- A drift-guard test ties the backend's copy of the OCR pipeline marker to the batch constant.
- Rollout notes: `comics_compose.py` in the image file list, a stuck `started` row of kind `comics_pipeline` blocks later builds'
  triggers, a concurrency-table row, the unmeasured OCR walk cost (new tracker item D13), the accepted double-registration window.

## Accepted or deferred

- A late failure of one of two concurrent identical-output builds can remove the other's file (the rollback releases the lock before
  the first build's file cleanup); documented, practically impossible for a single admin.
- An OCR walk running between the build's file replace and its commit could register a second row for the file; documented.
- The OCR step walks all of `BASE_PATH` on every pipeline run; the cost is unmeasured (tracker D13), to be measured with the first
  smoke build and narrowed only if slow.
- `run_steps` log lines carry the `description_pipeline:` prefix for every driver (cosmetic, predates C5).
- The inline trigger adds a few milliseconds to the build response.
- `builtUnprocessed` does not count tags (a text that yields no tags is legitimate).

## Not done by design

- Nothing was applied to a live database: `d7a3b9c5e214` and `f3a8c1d6b952` ship together at rollout, with the owner's go-ahead,
  following `docs/batches/use-cases/comics-rollout.md`.
- "A build leaves a searchable image after the pipeline runs" is covered with mocked children in the suites; the real end to end check
  is the owner's smoke build described in the rollout document.
