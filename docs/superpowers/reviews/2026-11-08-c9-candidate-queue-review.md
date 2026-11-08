# C9 candidate queue: review

Spec: `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-08-c9-candidate-queue-design.md`
Plan: `docs/superpowers/plans/2026-11-08-c9-candidate-queue.md`
Range reviewed: `aad33c13..8022012a` (four tasks plus docs), then one fix wave (`03153280`).

Process: a builder check on the design (seven questions, all ruled in the spec), a task review after each of tasks 1 to 3 (task 2 on
the most capable model), task 4 and the docs checked by the controller, one final whole-branch review on the most capable model,
one fix wave. Suites at the end: Backend 795, `batch/tests/` 411, `tests/docs/` 15, the whole `tests/integration/` root 1526,
frontend `tsc -b`, `eslint src/ --max-warnings 0` and vitest 1102.

## What was delivered

- Migration `a9d3e6b1c847` (`comics_candidates` with a stored `size`, `comics_candidate_images`), repository, preflight and schema docs.
- Batch job `detect_comics_candidates`: filename timestamps (two shapes) within seconds plus a CLIP distance window, no size or text
  signal, groups of 2 to 6, strongest first under a per-run cap, suppression of groups overlapping a decided one.
- API under `/api/comics/candidates` (list, accept with an optional ordered subset, dismiss), accept atomic in one savepoint with a row lock.
- Suggestions page `/admin/comics/candidates`.

## Fixed after review

- Task 1: groups that lost an image to deletion were still listed; a stored `size` now hides them. 
- Final review: bare accept of an incomplete group (`candidate_incomplete`), live recut cuts excluded in the detector and the list, a
  decided superset hides open subsets, candidate-specific error texts naming the offending image, rows dropped locally after a refusal
  so paging stays aligned, doc nits.

## Accepted or deferred

- Overlapping open groups (A-B, later A-B-C) may coexist until one is decided; documented.
- Test polish left: a unit test that pins the time-window limit, an unanchored datetime regex edge case, a per-environment settings check.
- Precision of the detector is unknown until suggestions are decided (tracker follow-up F1).

## Not done by design

- The migration is not applied to any environment: it ships with the owner's go-ahead per environment, following `comics-rollout.md`.
- No text-continuity signal, no scheduling, no automatic drafts.
