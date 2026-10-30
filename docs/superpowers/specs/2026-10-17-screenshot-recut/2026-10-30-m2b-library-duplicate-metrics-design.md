# M2b: Business metrics for the library duplicate review - design

Status: done
Plan: docs/superpowers/plans/2026-10-30-m2b-library-duplicate-metrics.md
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md (project M2, split into M2a and M2b)
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
ADR: docs/superpowers/specs/2026-10-17-screenshot-recut/adr/ADR-002-business-metrics.md
Follows: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-30-m2a-ingestion-duplicate-metrics-design.md

M2a measured the ingestion duplicate review. M2b measures the other human duplicate review: the owner's decisions on the library
`/duplicates` page (Explore, Duplicates). Unlike ingestion it has no queue to finish and no run to resolve: the owner browses the
clusters `clusterize` produced and flags copies or dismisses groups whenever they like, so M2b counts decisions only, with no
`proposed` denominator. Requirements covered: FR-14.

## What already exists

- M1 and M2a: `process_events`, `BusinessMetricsService` (`record`, `retract`, `with_event`), the metrics endpoint
  `GET /api/admin/batches/metrics?process=...`, `BusinessMetricsPanel` with `process`, `title`, `emptyText` and `note` props, and the
  savepoint-and-swallow pattern of `Backend/app/services/ingestion_metrics.py`.
- The page's decisions, all in `ImageService`: `mark_flagged(image_id, reason, duplicate_of)` (the page flags each loser with
  reason `duplicate_review` and the keeper as `duplicateOf`; a popularity edge of source `cluster_review` is recorded from loser to
  keeper), `unmark_flagged` (reverses the edge), `dismiss_cluster(cluster_id, member_ids)` (writes one `duplicate_decisions` row per
  pair of the target set) and `undo_dismiss(pairs)`.
- Decisions that are NOT the owner on the page: the `review-duplicates` agent command (reason `agent_review`) and
  `detect_file_duplicates` (reason `file_duplicates`, automatic).
- `clusterize` is run-tracked (`kind="clusterize"`); `tmp_clusters` is rewritten by it on every run. Candidate pairs and cluster ids
  carry no run id.

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | One process, `library_duplicates`; events `confirmed` and `dismissed`; no exclusive group | No queue, so no `proposed` or `pending` (owner's choice: decisions only). An image can be confirmed in one group and dismissed in another over time |
| 2 | Subject = the image. `confirmed` = every image of a confirmed duplicate group: each flagged loser and its keeper. `dismissed` = every member of a dismissed set | Both sides count group members, so a true and a false group of equal size weigh the same (owner's choice) |
| 3 | Only the owner on the page counts: `confirmed` is recorded for `mark_flagged` with reason `duplicate_review`. `agent_review` and `file_duplicates` flags record nothing | The number stays a measure of the owner's judgement of the near-duplicate detector |
| 4 | `dismissed` is recorded for every id in `dismiss_cluster`'s target set | It is exactly the set written to `duplicate_decisions` |
| 5 | Batch = the latest **completed** `clusterize` run at decision time; none yet means unattributed | `tmp_clusters` is the output of that job, and the page shows it |
| 6 | Undo retracts. `unmark_flagged` retracts the loser's `confirmed`, and the keeper's too once no open `cluster_review` edge points at it. `undo_dismiss` retracts an image's `dismissed` only when no `duplicate_decisions` pair involving it remains | An undone decision stops counting; nothing is retracted while another decision still holds |
| 7 | Metric `confirm_share` = confirmed / (confirmed + dismissed), `None` on a zero denominator | Same reading as M2a's reject share, and the panel says it is not a precision |
| 8 | Every write is in a savepoint that logs and swallows a failure | A metrics problem must never fail or roll back a flag or a dismissal |
| 9 | No migration, no new endpoint, no schema | `process_events` and the metrics endpoint are generic |

Known limits (documented, not fixed): an image counts once per event for as long as events are kept, so a later re-decision of the
same image in another cluster adds nothing and stays attributed to the run of its first decision; if the popularity edge of a flag
failed (swallowed by `guarded`), the unflag cannot find the keeper and the keeper stays counted; `clusterize` runs that never
completed receive no decisions.

## 1. Registry and service

`business_metrics_service.py`: one more entry in `PROCESSES`.

| Process | Events | Exclusive | Metrics |
|---|---|---|---|
| `library_duplicates` | `confirmed`, `dismissed` | none | `confirm_share` = confirmed / (confirmed + dismissed) |

The existing registry guard test (every metric survives all-zero counts) covers the new entry. `record`, `retract` and `with_event`
from M2a are sufficient; the service gets no new method.

## 2. `library_metrics` module and read helpers

New `Backend/app/services/library_metrics.py` is the only place the library review touches metrics. Each function runs its writes in
a savepoint (`session.begin_nested()`), logs a warning with the function name on any `Exception` and returns; `BaseException` is not
caught; none commits.

| Function | Does |
|---|---|
| `record_confirmed(session, image_ids)` | `confirmed` for each image, attributed to the latest completed `clusterize` run |
| `record_dismissed(session, image_ids)` | `dismissed` for each image, same attribution |
| `retract_confirmed(session, loser_id, keeper_id)` | retracts the loser's `confirmed`; retracts the keeper's too when `PopularityRepository.has_open_edge_to(keeper_id, {"cluster_review"})` is false |
| `retract_dismissed(session, image_ids)` | retracts `dismissed` for the images with no remaining `duplicate_decisions` row (`DuplicateDecisionsRepository.images_with_decisions`) |

Run resolution happens inside each function's savepoint: `BatchRunRepository.get_latest_completed_run("clusterize")` returns the
newest `batch_runs` row of that kind with status `completed`, or `None` (then `batch_run_id` is `None`). It is resolved once per call.

Read helpers added next to existing code:

- `PopularityRepository.open_winner(loser_id, sources) -> UUID | None`: the winner of the loser's open absorb edge, only for those sources.
- `PopularityRepository.has_open_edge_to(winner_id, sources) -> bool`: whether an open absorb edge of those sources still points at the winner.
- `DuplicateDecisionsRepository.images_with_decisions(image_ids) -> set[UUID]`.
- `BatchRunRepository.get_latest_completed_run(kind) -> BatchRun | None`.

## 3. Data flow

All in `ImageService`, inside the request's transaction (the repository session):

1. **Flag.** `mark_flagged`, only when `reason == "duplicate_review"`, after `set_flagged` and the popularity call: `record_confirmed` for the
   loser and, when `duplicateOf` is given, the keeper. Validation errors raise before anything is recorded, as today.
2. **Unflag.** `unmark_flagged`: before the popularity reversal read `open_winner(loser, {"cluster_review"})`; after the reversal call
   `retract_confirmed(session, loser, keeper_or_None)`. An image that was not flagged by the page has no event, so the retract is a no-op.
3. **Dismiss.** `dismiss_cluster`, after `record_decisions_bulk`: `record_dismissed` for every id of `target_ids`.
4. **Undo dismiss.** `undo_dismiss(pairs)`, after `delete_decisions`: `retract_dismissed` for the distinct images of those pairs.

## 4. Errors and edge cases

- A metrics failure never changes the review result; the count is one short and a warning is logged.
- A flag with another reason (`agent_review`, `file_duplicates`) or none records nothing.
- Flag, unflag, flag again: the retract clears `confirmed`, so the second flag records again.
- Several losers of one keeper: the keeper is recorded once; unflagging one loser keeps it while another open `cluster_review` edge points
  at it, and unflagging the last retracts it.
- A flag then a dismissal of the same image leaves both events.
- No completed `clusterize` run: events go to the unattributed bucket, which the panel shows when it has counts.
- Concurrent opposite actions are not serialised; the worst case is a count off by one (as in M2a).

## 5. Panel and API

- `AdminBatchesPage` mounts one more `BusinessMetricsPanel` under the ingestion ones: title "Library duplicates", process
  `library_duplicates`, empty text "No library duplicate decisions yet", and the note "Counts images in decided groups, not detector
  precision: a confirmed group is its flagged copies plus the keeper. Each image counts once per outcome, under the clusterize run
  that was current at its first decision."
- Columns and labels come from the response (`Confirmed`, `Dismissed`, `Confirm share`); `*_share` formats as a percentage. Rows are
  per `clusterize` run; decisions taken while no run existed are in the Unattributed row.
- `GET /api/admin/batches/metrics?process=library_duplicates` works; any other unknown `process` is still `404`. `backend_api.md` lists
  the value, its events and the formula.

## 6. Tests

- **Registry and module (integration):** the formula and its zero denominator; both events can coexist on one image; `record_*` are
  idempotent, attribute to the latest completed `clusterize` run, and use `None` when there is none (a started or failed run is ignored,
  the newest completed one wins); `retract_confirmed` retracts the loser, keeps the keeper while another open `cluster_review` edge
  points at it and retracts it once none does; `retract_dismissed` skips images that still have a decision pair; a metrics write that
  raises is swallowed.
- **Flows (integration, real `ImageService`):** `mark_flagged` with `duplicate_review` records loser and keeper; `agent_review`, another
  reason or none records nothing; a bad `duplicateOf` raises before anything is recorded; several losers of one keeper, unflagged one by
  one; `dismiss_cluster` over a whole cluster and over a subset; `undo_dismiss` of part of a cluster; flag then dismiss of one image; a
  metrics failure leaves the flag and the dismissal applied with the response unchanged.
- **Read helpers (integration):** `open_winner`, `has_open_edge_to` (including a reversed edge and an edge of another source),
  `images_with_decisions`, `get_latest_completed_run`.
- **Endpoint:** `process=library_duplicates` returns the shape; an unknown process is `404`.
- **Frontend (vitest):** the panel renders with its title, empty text and note; `AdminBatchesPage` mounts four panels.
- **Verification:** each test root run separately (`tests/integration/`, `Backend/`, `batch/tests/`, `tests/evaluation/`, `tests/rules/`,
  `tests/docs/`, vitest, `tsc`, `eslint`).

## 7. Docs

`backend_api.md` (the value, events, formula, caveats); `docs/batches/use-cases/duplicates-and-clusters.md` ("Review metrics": what
counts, the symmetric group rule, the attribution, the once-per-image limit, the keeper over-count); the `clusterize` entry in
`CLAUDE.md` and `docs/batches/inventory.md` (library review metrics are attributed to its latest completed run); an ADR-002 amendment
line (M2b is the second user of `retract`; a failed metrics write is swallowed, so counts and states can disagree by one); this epic's
tracker.

## 8. Out of scope

The agent command's decisions; exact-copy flags; a `proposed` or `pending` denominator; backfill of past decisions; any change to the page,
the detector or `clusterize`; charts over time.

## 9. Acceptance

- The `library_duplicates` process exists and the panel shows per-run counts and the confirm share.
- A flag, an unflag, a dismissal and an undo record and retract exactly the events described, and a metrics failure changes no review result.
- Docs, `backend_api.md` and the ADR amendment agree with the code.
- No live run is part of M2b. The first real session on `/duplicates` after merge is the real check, and is the owner's call.
