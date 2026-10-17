# Screenshot recut: requirements

Status: draft
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md

## Functional requirements

| ID | Requirement |
|---|---|
| FR-1 | A batch job detects active images whose content is surrounded by extra frames, UI chrome or a phone screen, and stores a proposal (source, box in source pixels, detector name and version, confidence) |
| FR-2 | The box is always one axis-aligned rectangle inside the source image |
| FR-3 | An image that is itself a cut result is never proposed again. A source that was rejected is not proposed again by the same detector version |
| FR-4 | For each proposal a cut image is created as a new file and a new `images` row with status `cut_proposed`. The source stays `active` and unchanged |
| FR-5 | The relation between source and cut is stored in `image_cuts` and survives the removal of either image |
| FR-6 | The cut inherits derived data in three groups (see `decomposition.md` section 3): copied (human-authored), recomputed (pixel-dependent), copied then refreshed (model-derived) |
| FR-7 | Copied model-derived rows are marked `stale`. Each affected batch job has a "refresh stale" mode that deletes and rewrites an image's stale rows with its normal logic |
| FR-8 | A review page lists proposals and shows the source and the cut side by side with the box drawn on the source |
| FR-9 | Approve: the cut becomes `active`, the source is flagged for the existing move and remove path, the proposal state becomes `approved` |
| FR-10 | Reject: the cut is flagged for the existing move and remove path, the source is untouched, the proposal state becomes `rejected` |
| FR-11 | Cut images are invisible to search, browse and similarity until approved. Source/cut pairs do not appear in each other's duplicate candidates |
| FR-12 | `docs/image-status.md` defines every image status (meaning, who sets it, who reads it, allowed transitions). Specs, `CLAUDE.md` and `docs/batches/` link to it instead of redefining statuses |
| FR-13 | Every proposal outcome (proposed, approved, rejected) is recorded as a business metric event. The service reports counts and rates per batch (detection run) |
| FR-14 | Business metrics are a generic capability: process name, event name, batch run, subject id, timestamp. The recut process is its first user |
| FR-15 | The review UI lets the owner adjust the box before approving (project R6, after first use) |
| FR-16 | Detection quality is measured on a labelled sample (precision of proposals, and recall where measurable) before the detection job is scheduled |

## Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Detection is conservative: a missed cut is cheaper than a wrong proposal, because every proposal costs review time |
| NFR-2 | Creating a cut and copying its inherited data happen in one transaction. A failure leaves no half-populated cut |
| NFR-3 | Jobs and endpoints follow the existing conventions: run-tracked batch jobs, registry entries, `backend_api.md` updated, shared schemas and generated types, three environment overlays |
| NFR-4 | The `stale` column migration must not rewrite large tables (a `NOT NULL DEFAULT false` column is a metadata-only change on current PostgreSQL; R1 confirms the server version) and must be rolled out to the metal, general and IT databases |
| NFR-5 | Rates are computed at read time from events, never stored, so a late decision cannot leave a stored rate wrong |
| NFR-6 | Nothing is destroyed silently: the source is only flagged after an explicit approve, and every removal goes through the existing flag, move and remove path |

## Open questions

| ID | Question | Decided in |
|---|---|---|
| OQ-1 | Detection approach: classical border and edge detection, a vision model through Ollama, or YOLOv8 | R2 spike, judged on the labelled sample |
| OQ-2 | Output format of the cut file (keep the source format, or PNG for screenshots with text) | R3 design |
| OQ-3 | Size of the labelled sample and who labels it (proposal: about 100 images, the owner, once) | R2 design / R7 |
| OQ-4 | Minimum crop (area ratio, absolute size) below which a proposal is dropped | R2 design |
| OQ-5 | The exact list of tables in groups 1, 2 and 3 | R1 inventory |
| OQ-6 | Retention of business metric events | M1 design |
| OQ-7 | Whether `move_flagged` and `unregister_deleted_images` handle non-active images correctly, and whether `ingest_hash_dedup` must skip files written by the cut step | R5 audit |
