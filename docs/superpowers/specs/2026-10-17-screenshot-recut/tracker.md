# Screenshot recut: project tracker

Status: draft
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md

Created 2026-10-17. Each project has its own design spec (written when the project is picked up, named
`YYYY-MM-DD-<name>-design.md` and linked below), then an implementation plan, then implementation. Project status uses the spec
lifecycle values (`draft`, `approved`, `planned`, `implementation`, `done`), with `not started` before a spec exists.

Epic-level ADRs (all `proposed`): ADR-001 status registry and staleness, ADR-002 business metrics.

## Projects

| ID | Project | Delivers | Depends on | Reqs | Status | Spec / plan |
|---|---|---|---|---|---|---|
| R1 | Data model and status registry | `image_cuts` table, `cut_proposed` status, `StaleMixin` and shared stale helper, `docs/image-status.md` with links from the existing docs, inventory of every table off `images` classified into the three groups with the job that refreshes it | none | FR-5, FR-6, FR-12, OQ-5 | implementation | `2026-10-17-r1-data-model-and-status-registry-design.md`, plan `docs/superpowers/plans/2026-10-17-r1-data-model-and-status-registry.md` |
| M1 | Business metrics infrastructure | `process_events` table, `BusinessMetricsService` (record, counts and rates per batch), reporting endpoint or statistics panel, tests | R1 (`detect_run_id`) | FR-13, FR-14, NFR-5, OQ-6 | implementation | `2026-10-18-m1-business-metrics-design.md`, plan `docs/superpowers/plans/2026-10-18-m1-business-metrics.md` |
| R2 | Crop detection | Spike comparing approaches on the labelled sample, then the detection batch job storing `detected` proposals, run-tracked and admin-triggerable | R1 | FR-1 to FR-3, FR-16, NFR-1, OQ-1, OQ-3, OQ-4 | implementation | `2026-10-24-r2-crop-detection-design.md`, plan `docs/superpowers/plans/2026-10-24-r2-crop-detection.md`, spike `2026-10-24-r2-spike-findings.md` |
| R3 | Cut and derive | Cut file creation, `cut_proposed` row, group 1 and 3 copy in one transaction, `--status cut_proposed` for group 2 jobs, "refresh stale" mode in group 3 jobs, `proposed` event | R1, R2, M1 | FR-4, FR-6, FR-7, NFR-2, OQ-2 | not started | - |
| R4 | Review API and UI | List and detail endpoints, before/after page with the box drawn, approve and reject actions (calling R5), `backend_api.md`, shared schemas and generated types | R3 | FR-8 | not started | - |
| R5 | Approve and reject lifecycle | State transitions, flagging through the existing path, duplicate exclusion for source/cut pairs, `approved` and `rejected` events, audit of `move_flagged`, `unregister_deleted_images` and `ingest_hash_dedup` | R3, M1 | FR-9 to FR-11, NFR-6, OQ-7 | not started | - |
| R6 | Box adjustment | Drag-to-adjust in the review UI, cut regenerated from the corrected box | R4, R5 | FR-15 | not started | - |
| R7 | Operations and evaluation | Batch registry and inventory entries, statistics, detection precision measured on the labelled sample, recut metrics shown, decision on scheduling | R2 to R5 | FR-16, NFR-3 | not started | - |
| M2 | Business metrics for duplicate detection (follow-up) | Duplicate review decisions recorded as `process_events`, same counts and rates per batch | M1 | FR-14 | deferred: decide after M1 has been used on recut, in or out of this epic | - |

## Order

```
R1 ──► M1 ──────────────┐
 └──► R2 ──► R3 ──► R4 ┴──► R5 ──► R6
                              └──► R7  ──► (M2?)
```

Suggested sequence: R1 first (the status registry and the inventory unblock everything), M1 next because it is small and R3 and R5
record events, then R2 (the spike can start in parallel with R1 and M1, as it needs only the labelled sample), R3, R4, R5, R7, and
R6 once the first release has shown how often proposals are slightly off.
