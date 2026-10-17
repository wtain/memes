# ADR-002: Business metrics

Status: proposed
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md (D8)

## Context

The owner wants to judge the quality of processes such as frame cropping and, later, duplicate detection. Existing metrics are
technical: `BatchRun.stats` (per-run counters), `image_metrics` (timings) and `statistics_snapshots` (corpus counts). None of them
records the outcome of a human decision that arrives days after the run, and none lets two processes be compared the same way.

## Decisions

1. **An append-only event table**, `process_events`: `id`, `process` (for example `recut`, later `duplicates`), `event`
   (for example `proposed`, `approved`, `rejected`), `batch_run_id` (FK to `batch_runs`, the run that produced the subject),
   `subject_id` (UUID of the proposal), `occurred_at`. One row per outcome.
2. **A service class**, `BusinessMetricsService`, with two sides: `record(process, event, batch_run_id, subject_id)` called by the
   process code, and read methods that return counts per batch and rates per batch.
3. **Rates are computed at read time**, never stored. Definitions for recut: approve rate = approved / (approved + rejected);
   decided share = (approved + rejected) / proposed. Pending proposals are reported separately and are not counted as rejections.
4. **Generic by design**: nothing in the table or service is specific to recut. Duplicate detection becomes a second user (M2)
   by recording its own events.
5. **The batch is the detection run** that produced the proposal, so a late approval is attributed to the run that made it.
   `image_cuts.detect_run_id` carries this.

## Alternatives rejected

- Deriving everything from `image_cuts.state`: works for recut but gives no shared mechanism for other processes and loses the
  history if a row is deleted.
- Storing rates: goes wrong when decisions arrive late.
- Putting counts into `BatchRun.stats`: the run is complete long before the decisions exist.

## Consequences

- One table, one service, one reporting query shape, reusable by M2.
- Recording an event is a call at each decision point (R5 for approve and reject, R3 for proposed), inside the same transaction as the state change, so counts and states cannot disagree.
- Open: retention of events (OQ-6) and how the numbers are shown (statistics page or admin batches), decided in the M1 design.
