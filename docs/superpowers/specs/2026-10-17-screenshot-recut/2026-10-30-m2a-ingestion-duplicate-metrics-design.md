# M2a: Business metrics for the ingestion duplicate review - design

Status: done
Plan: docs/superpowers/plans/2026-10-30-m2a-ingestion-duplicate-metrics.md
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md (project M2, split into M2a and M2b)
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
ADR: docs/superpowers/specs/2026-10-17-screenshot-recut/adr/ADR-002-business-metrics.md
Follows: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-30-r6-box-adjustment-design.md

M1 built the business-metrics machinery and recut was its first user. M2 gives the duplicate detector the same: counts and rates
per batch of what the owner decides about its proposals. The duplicate decisions are made in two flows with different units and
different batches, so M2 is split. **M2a** (this document) measures the ingestion review (Tier A and Tier B). **M2b**, written later,
measures the library `/duplicates` page, where the open question is which batch a decision belongs to. Requirements covered: FR-14.

## What already exists

- `process_events`, `BusinessMetricsService.record` (idempotent per `(process, event, subject)`, exclusive groups), the registry of
  processes, `GET /api/admin/batches/metrics?process=...`, and `BusinessMetricsPanel` with a `process` prop (M1).
- The ingestion review: `IngestionService.resolve` applies per-image `reject` / `keep` decisions for a tier; a `reject` flips the image
  to `rejected` and credits a winner (popularity); a `keep` stamps the tier's `reviewed_at` on every candidate pair of the image
  ("not a duplicate"); `undo_reject` returns a rejected image to `pending`. `ingest_find_duplicates` fills `tmp_duplicates` per
  tier; `ingest_promote` promotes pending images with no unresolved candidate.
- `images.ingestion_batch_id`: the ingestion run (`batch_runs`, kind `ingestion`) an image was registered under.

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | Two processes in the registry: `ingest_tier_a` and `ingest_tier_b`; events `proposed`, `rejected`, `kept`; `rejected` and `kept` are an exclusive group | The tiers have different thresholds and meanings and are tuned separately |
| 2 | Subject = the pending image; batch = its ingestion run (`images.ingestion_batch_id`) | The owner chose "per image". A pair-level unit needs a "confirmed wins" rule and pair ids that survive rebuilds |
| 3 | `proposed` is recorded by `ingest_find_duplicates`, from the tier's candidate rows, for each pending image of the run in the tier's band (A: below 0.05; B: 0.05 up to `duplicates.threshold`) | A rate needs a denominator that exists before anyone reviews |
| 4 | `rejected` and `kept` are recorded by `IngestionService.resolve`, only for an image that has `proposed` in that tier. A `reject` also closes the image's open proposal in the other tier as `rejected` (not where the owner kept it), through `record_rejection` (added in the final review: an image freed in one tier and rejected in the other would otherwise stay pending for ever) | A stale or out-of-queue decision must not count; a rejected image leaves every queue |
| 5 | Every metrics write is in a savepoint that logs and swallows a failure | A metrics problem must never fail or roll back a review decision (ingestion commits per decision) |
| 6 | `undo_reject` retracts the image's `rejected` event in both tiers (new `BusinessMetricsService.retract`) | Otherwise undo then keep hits the exclusive group. This amends ADR-002's "append-only": rows are removed only when the decision they describe is undone |
| 7 | `ingest_promote` records `kept` for promoted images that were proposed in a tier and never decided there | A reject of an image's partner can release it with no decision of its own; without this it stays pending forever |
| 8 | Metrics: `reject_share` = rejected / (rejected + kept), `decided_share` = (rejected + kept) / proposed, `pending`. Reported and labelled as a reject share, not a precision | A kept winner of a true duplicate group is counted as `kept` (the per-image caveat) |
| 9 | An aborted run keeps its events; its images show as pending | M1: events are kept forever. Documented, not special-cased |
| 10 | No migration, no new endpoint, no schema | `process_events` and the metrics endpoint are generic |

## 1. Registry and service

`business_metrics_service.py`: two `ProcessDefinition`s in `PROCESSES`.

| Process | Events | Exclusive | Metrics |
|---|---|---|---|
| `ingest_tier_a`, `ingest_tier_b` | `proposed`, `rejected`, `kept` | `("rejected", "kept")` | `reject_share` = rejected / (rejected + kept); `decided_share` = (rejected + kept) / proposed; `pending` = proposed - rejected - kept |

A zero denominator gives `None` (the existing registry guard test covers every process). New service methods:

- `retract(process, event, subject_id) -> bool`: deletes that one row; `False` if there was none. Never commits.
- `undecided(process, proposed_event, decided_events, subject_ids) -> set[UUID]`: the subjects that have `proposed_event` and none of `decided_events` (used by decision 7).
- `with_event(process, event, subject_ids) -> set[UUID]`: which subjects have that event (used by decision 4).

`repository/process_events.py` gets the matching `delete_event` and a grouped lookup `subjects_with_event`. All three never commit.

## 2. `ingestion_metrics` module

New `Backend/app/services/ingestion_metrics.py` is the only place the ingestion code touches metrics (imported by the service and by
the two batch jobs, as `batch/create_recut_cuts.py` already imports backend services). Each function runs its writes in a savepoint
(`session.begin_nested()`), logs a warning with the function name on any `Exception` and returns, so the caller's transaction stays usable.
`BaseException` (cancellation) is not caught. A local helper is used rather than `repository.popularity.guarded`, whose log text names popularity.

| Function | Does |
|---|---|
| `record_proposed(session, tier, batch_id, image_ids)` | `proposed` under the tier's process for each image, attributed to `batch_id` |
| `record_decision(session, tier, event, image_id, batch_id)` | `rejected` or `kept`; skipped unless the image has `proposed` in that tier |
| `retract_rejection(session, image_id)` | retracts `rejected` in both tiers (one is a no-op) |
| `record_implicit_keeps(session, batch_id, promoted_ids)` | for each tier, `kept` for promoted images that are proposed and undecided there |

## 3. Data flow

1. **Propose.** In `ingest_find_duplicates.main`, after `find_batch_duplicates` and before the commit, read the tier's rows with
   `IngestionRepository.get_tier_candidate_rows(batch_id, tier, low, high)` (the same filters as the review queue: in band, not yet
   reviewed in this tier, neither side rejected, one side a pending member of the batch), take the pending members of the run and
   call `record_proposed`. Tier A's call records tier A, Tier B's call records tier B; a re-run, a late-dropped file or a pair found by
   the other tier's call only adds what is new. The band comes from `_tier_band` in `ingestion_service.py` (moved to a shared place if
   importing it from the batch job would create a cycle).
2. **Decide.** In `IngestionService.resolve`, a `reject` calls `record_decision(..., "rejected", ...)` right after `_record_reject`
   and before `repo.commit()`; a `keep` calls it with `"kept"` after `mark_reviewed`, before the commit. The batch id is
   `repo.get_batch_id(image_id)`. A stale `reject` already returns before this point.
3. **Undo.** `IngestionService.undo_reject` calls `retract_rejection` after the status flip, next to the popularity reversal.
4. **Promote.** In `batch/ingest_promote.py` after `promote()` returns the promoted ids and before the commit, call
   `record_implicit_keeps(session, batch_id, promoted_ids)`.

## 4. Errors and edge cases

- A metrics failure never changes the review result; the count under-reports by one and a warning is logged.
- A `kept` after a `rejected` for the same image and tier (a stale request) raises the exclusive-group error inside the savepoint and is swallowed.
- An image can be proposed in both tiers; each tier counts its own decision.
- After `undo_reject` the image can be rejected or kept again.
- `ingest_abort` leaves its events; the images show as pending.
- The `it` environment has the same flow; nothing is environment-specific.

## 5. Panels and API

- `AdminBatchesPage` mounts two more `BusinessMetricsPanel`s under the recut one: "Ingestion duplicates: Tier A" (`ingest_tier_a`) and
  "Ingestion duplicates: Tier B" (`ingest_tier_b`), with empty texts "No tier A duplicate candidates yet" / "... tier B ...".
- `BusinessMetricsPanel` gets an optional `note` prop; its default stays "Pending proposals are not counted as rejections." The tier
  panels pass: "Reject share, not precision: a kept image can be the winner of a true duplicate group. Pending images are not counted as kept."
- Columns, labels and percentage formatting come from the response (`Proposed`, `Rejected`, `Kept`, `Reject share`, `Decided share`,
  `Pending`; `*_share` formats as a percentage).
- `GET /api/admin/batches/metrics?process=ingest_tier_a|ingest_tier_b|recut`; any other `process` is still `404`. `backend_api.md`
  lists the valid values and the new events and metrics.

## 6. Tests

- **Registry and service (integration):** the new formulas incl. zero denominators; `rejected`/`kept` exclusive; `retract` removes only
  that event and is a no-op the second time; after a retract the image can be `kept` or `rejected`; `undecided` and `with_event`.
- **`ingestion_metrics` (integration):** `record_proposed` records only pending members of the run with a row in the tier's band and is
  idempotent, including the band split (a 0.07 pair is a tier B proposal only); `record_decision` ignores an image with no `proposed`;
  a metrics write that raises (monkeypatched) leaves the decision and its image status intact; `retract_rejection` clears either tier;
  `record_implicit_keeps` covers an undecided promoted image and skips decided and never-proposed ones.
- **Flows (integration, real services):** `resolve` reject records `rejected`, keep records `kept`, a stale reject records nothing;
  `undo_reject` then keep leaves `kept` counted once and no `rejected`; `ingest_find_duplicates` records `proposed` for both tiers and a
  re-run adds nothing; `ingest_promote` records the implicit keeps so a finished run has pending 0.
- **Endpoint:** `process=ingest_tier_a` returns the shape; an unknown process is `404`.
- **Frontend (vitest):** tier panels render with their note and empty text; the recut panel keeps its default note; `AdminBatchesPage`
  mounts all three panels.
- **Verification:** each test root run separately (`tests/integration/`, `Backend/`, `batch/tests/`, `tests/evaluation/`, `tests/rules/`,
  `tests/docs/`, vitest, `tsc`, `eslint`).

## 7. Docs

ADR-002 amendment (append-only except `retract`, only for an undone decision); `backend_api.md`; `docs/batches/use-cases/ingestion.md`
("Review metrics": what is counted, the kept-winner and aborted-run caveats); the `ingest_find_duplicates` and `ingest_promote` entries
in `CLAUDE.md` and `docs/batches/inventory.md`; this epic's tracker (M2a and M2b rows).

## 8. Out of scope

The library `/duplicates` page and `agent_review` flows (M2b); backfill of past runs; charts over time; a pair-level or three-outcome
precision (a different unit, decided against); any change to the detector, its thresholds or the review UI.

## 9. Acceptance

- The two processes exist and the panels show per-run counts and rates for both tiers.
- A review decision, an undo, a find run and a promote record exactly the events described, and a metrics failure changes no review result.
- Docs, `backend_api.md` and the ADR amendment agree with the code.
- No live run is part of M2a. The first real ingestion run after merge is the real check, and is the owner's call.
