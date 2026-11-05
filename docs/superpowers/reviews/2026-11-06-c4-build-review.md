# C4 build: review

Spec: `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-05-c4-build-design.md`
Plan: `docs/superpowers/plans/2026-11-05-c4-build.md`
Range reviewed: `75c99c0..16f30b4` (ten tasks, one final fix wave).

Process: a task review after each of tasks 1 to 9, the build service (task 6) on the most capable model; task 10 (documentation and
full-suite runs) was covered by the final whole-branch review instead of its own. One final whole-branch review on the most capable
model, one fix wave, one scoped re-review (all findings addressed, no new breakage). Suites at the end of task 10: Backend 767,
the whole `tests/integration/` root 1471, `batch/tests/` 383, `tests/docs/` 15, `tests/docker/` 6 (a daemon was available), frontend
`tsc -b`, `eslint src/ --max-warnings 0` and vitest 1018 (1019 after the fix wave), generated types without diff.

## What was fixed

- **Shared helper bug found by the build tests.** SQLAlchemy fires `after_commit` when a SAVEPOINT is released. `file_effects`
  (`Backend/app/services/recut_files.py`) took that as the request's commit and cancelled its rollback action, so a failure after
  `mark_built` would have left an orphan built file. It now ignores the event while `session.in_nested_transaction()`. This also
  repairs a latent early firing in the recut adjust flow (the old cut file could be removed before the commit). Two regression
  tests, the first fails on the old code.
- The flag endpoints' documentation lacked the new 409 `comics_source_flag` (final review, Important).
- The Build dialog stayed closable while the irreversible request ran, so a user pressing Cancel was navigated away anyway
  (final review, Important). Cancel is disabled and Escape, backdrop and the X are inert while busy.
- Flagging an image with the reserved reason `comics_source` through the API is refused with 422 `comics_source_reserved`, so a
  free-text request cannot make an image unflaggable or route a draft's panel to `comics-sources/`.
- Documentation: one wrong sentence, the docs index row, the missing `not_buildable` reasons, the leftover-file note, the runbook
  "ten" to "eleven".

## Accepted or deferred (tracker D12 for C5)

- A build cancelled during compose can leave a hidden `.comics-build-*` file; `extract_text_from_memes` would register it as a
  meme. Documented in `docs/batches/use-cases/comics-build.md`; the code fix (skip dot-files, or shield the thread) is C5.
- Two different comics with byte-identical output built at the same moment can both pass `same_as_existing` (`images.content_hash` is
  not unique); a single admin, practically impossible.
- `mark_flagged`/`unmark_flagged` check-then-write is not atomic against the build; worst case the file lands in `excluded/`.
- `mark_built` leaves the in-memory comics dirty after a unique violation; the request always rolls back.
- Private helper imports (`_statuses`, `_flagged_ids`, `_live_cut_ids`, `_detail`); a kept panel's thumbnail breaks if its image is
  deleted later.

## Test-process notes

- Two tasks wrote tests together with the component, so they have no separate RED run (BuildDialog, the fix-wave dialog test); the
  behavior is covered by assertions that fail without it.
- One unrelated vitest test (`RecutReviewPage`, R key) failed once in a full run and passed on rerun.

## Not done by design

No migration was applied to any live database: `d7a3b9c5e214` and `f3a8c1d6b952` ship together at rollout (C5), with the go-ahead
of the owner. The `comics_pipeline` driver and statistics are C5.
