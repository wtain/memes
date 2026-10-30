# Epic: Screenshot recut

Status: draft
Originates from: user request, 2026-10-17 (brainstorming session)
Follow-ups: project design specs are listed in `tracker.md` and written when each project is picked up.

Some memes in the library are poorly cut from screenshots: they keep extra frames, UI chrome, or the whole phone screen around the
actual meme. This epic detects those images, proposes a cleaner cut as a new image, lets a human review "before" and "after", and
retires whichever version loses. It also introduces business metrics (outcome counts and rates per process, per batch), first
for this process and later for duplicate detection.

This folder is the epic: charter, requirements, decomposition, decisions (ADRs) and a project tracker. Design specs for individual
projects are written later, one per project, and linked from `tracker.md`.

## Files

| File | Purpose |
|---|---|
| `README.md` | This charter: vision, scope, decisions made, glossary |
| `requirements.md` | Functional and non-functional requirements (`FR-*`, `NFR-*`) and open questions (`OQ-*`) |
| `decomposition.md` | Data model, lifecycle, inheritance groups, mapping to what exists, risks |
| `adr/ADR-001-status-registry-and-staleness.md` | The `cut_proposed` status, the status registry doc, the `stale` column and the "refresh stale" batch mode |
| `adr/ADR-002-business-metrics.md` | Business metrics: event table, service class, rates computed at read time |
| `tracker.md` | Projects, dependencies, order, status |

## Vision

A screenshot-sourced meme should show only the meme. The system finds images where it does not, proposes the tight rectangle, and
shows the owner the original next to the cut. One decision per image keeps the library clean without silently destroying anything,
and the approve and reject numbers tell the owner whether the detector deserves more trust.

## Decisions already made (user, 2026-10-17)

| # | Decision | Consequence |
|---|---|---|
| D1 | The cut is **one axis-aligned rectangle**: no rotation, no perspective, no several memes per screenshot | A proposal is four numbers. Detection and the cut operation stay simple |
| D2 | Review is **approve or reject only** for the first release. Adjusting the box is a follow-up project inside this epic (R6) | The box is stored from day one, so R6 only overwrites it. (R6 did add four nullable columns, `detected_crop_*`, to keep the detector's original box for "reset to detected box" and the adjusted statistics: `2026-10-30-r6-box-adjustment-design.md`) |
| D3 | The cut inherits derived data in three groups: human-authored data is copied, pixel-dependent data is recomputed, model-derived data is copied first and refreshed later | See `decomposition.md` section 3. The exact table list comes from the R1 inventory |
| D4 | Staleness is a boolean `stale` column on each refreshable derived table (a shared mixin). Batch jobs get a third mode, "refresh stale", next to full and incremental | See ADR-001. The staleness knowledge reaches the batches through one shared helper |
| D5 | The cut image has its own status, **`cut_proposed`**, not ingestion's `pending` | No entanglement with `ingest_promote` or `ingest_abort`. See ADR-001 |
| D6 | A **status registry** (`docs/image-status.md`) is the single source for what each image status means. Other docs link to it | Removes the current situation where meanings are scattered across specs |
| D7 | **Approve**: the source is flagged and leaves through the existing `move_flagged` and `unregister_deleted_images` path, the cut becomes `active`. **Reject**: the cut is flagged and removed the same way, the source is untouched | The existing flag, move and remove machinery is reused, not duplicated |
| D8 | **Business metrics** are a cross-cutting capability, introduced here: an append-only event table and a service class. Approve and reject counts and rates per batch for this process. Duplicate detection gets the same later (follow-up M2) | See ADR-002 |

## Scope

In scope:
- The `image_cuts` relation, the `cut_proposed` status and the status registry.
- Crop detection (approach chosen by a spike) and a batch job that stores proposals.
- Creating the cut image and populating it with inherited data, including the "refresh stale" mode in the affected jobs.
- A review API and a before/after UI with approve and reject.
- Wiring approve and reject into flagging and removal; keeping source and cut out of each other's duplicate candidates.
- Box adjustment in the review UI (R6, after the first release is in use).
- Business metrics infrastructure and the recut metrics; statistics and evaluation of detection quality.

Out of scope (candidates for later):
- Rotation, perspective correction, masking rounded phone corners, splitting one screenshot into several memes.
- Fully automatic approval without a human.
- Business metrics for duplicate detection (M2, decided after this epic's metrics have been used).
- Android review client.

## Glossary

| Term | Meaning |
|---|---|
| Source | The existing image that has a bad cut |
| Cut | The new image produced from the source by cropping to the proposed box |
| Proposal | An `image_cuts` row: source, box, detector, confidence, state |
| Inherited data | Derived data copied from the source to the cut (groups 1 and 3 in `decomposition.md`) |
| Stale | A copied model-derived row that the next "refresh stale" run must recompute |
| Status registry | `docs/image-status.md`: meaning, setter, readers and allowed transitions per image status |
| Business metric | A count or rate describing the outcome of a process (for example the approve rate of a detection batch), as opposed to a technical metric such as run time |
