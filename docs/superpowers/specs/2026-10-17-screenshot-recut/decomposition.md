# Screenshot recut: decomposition

Status: draft
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md

Checked against `Storage/models.py` and `repository/` on 2026-10-17. Items marked "audit" were not verified against the code and
are scoped into the named project.

## 1. Data model

New table `image_cuts`, one row per proposal:

| Column | Purpose |
|---|---|
| `id` | Primary key |
| `source_image_id` | Nullable FK to `images`, `ON DELETE SET NULL`, so the record outlives a removed source |
| `cut_image_id` | Nullable unique FK to `images`, `ON DELETE SET NULL`, so a rejected and removed cut keeps its history |
| `source_content_hash` | Snapshot of the source hash, for audit after the source is gone |
| `crop_x`, `crop_y`, `crop_w`, `crop_h` | Box in source pixels, stored from day one so R6 needs no migration |
| `detector`, `detector_version`, `confidence` | Which detector proposed it and how sure it was |
| `detect_run_id` | FK to `batch_runs.run_id`: the detection run that produced the proposal. This is the "batch" in the business metrics |
| `state` | `detected`, `proposed`, `approved`, `rejected`, `failed` |
| `created_at`, `decided_at` | Timestamps |

A partial unique index allows at most one live row (`detected`, `proposed` or `approved`) per source. Rejected rows stay so the
detector does not re-propose the same source until its version changes.

Other model changes: `images.status` gains `cut_proposed`; a `StaleMixin` adds `stale boolean not null default false` to the
derived tables in group 3; `process_events` for business metrics (ADR-002).

## 2. Lifecycle

```
detect (R2)   image_cuts.state = detected          box only, no file
cut (R3)      new file in BASE_PATH; images row status = cut_proposed; groups 1 and 3 copied
              (group 3 rows stale = true); state = proposed; event "proposed" recorded
derive (R3)   group 2 jobs run with --status cut_proposed; "refresh stale" rewrites group 3
review (R4)   before/after screen
approve (R5)  cut_proposed -> active; source flagged for move_flagged; state = approved; event "approved"
reject (R5)   cut flagged and removed by the same path; source untouched; state = rejected; event "rejected"
```

The source stays `active` throughout, so search is unaffected until approval. After approval the source leaves through
`move_flagged` then `unregister_deleted_images`; its derived rows cascade away, which is safe because group 1 was copied.

## 3. Inheritance groups

| Group | Rule | Examples (final list from the R1 inventory) |
|---|---|---|
| 1. Copy as is | Human-authored or not pixel-dependent | description notes and their lemmas and embeddings, human-verified meme profile, `Note` tags, `image_extras.remarks`. Ratings are group 1 but move on approve instead of being copied (FR-18). The authoritative list is section 7 of the R1 design |
| 2. Recompute | Depends on the pixels; copying would be wrong | CLIP embedding, `width`, `height`, `content_hash`, text-heavy classification, OCR text embedding, duplicate candidates |
| 3. Copy, then refresh | Model-derived and cheap to copy, but the frame may have contributed junk | OCR text, OCR lemmas, Ollama descriptions and their embeddings and lemmas, tags derived from OCR and descriptions, model-generated meme profile and its embedding |

Copying the source CLIP vector is explicitly forbidden: the cut would be a perfect duplicate of its source.

Group 3 jobs gain the "refresh stale" mode: select images with stale rows through the shared helper, delete those rows, rewrite
them with the job's normal logic. Rewritten rows take the default `stale = false`, so nothing needs clearing, and a job that now
produces no rows (for example the cut has no OCR text) leaves no stale copy behind.

## 4. Mapping to what exists

| Need | What exists | Verdict |
|---|---|---|
| Hide cut images until approved | Repository list methods default to `status="active"`; several repositories hard-code `"active"` | **Reuse**: a new status value is hidden for free. Audit: jobs that need non-active images take an explicit `--status`, extend those |
| Pixel-dependent recompute | `--status pending` modes in the embedding, OCR and classification jobs | **Extend**: allow `cut_proposed` |
| Flag, move, remove | `ImageExtras.flagged`, `move_flagged`, `unregister_deleted_images` (chained) | **Reuse** (R5 audit, OQ-7) |
| Duplicate exclusion | `tmp_duplicates`, `rebuild_duplicates`, `ingest_find_duplicates` | **Extend**: skip source/cut pairs |
| Run tracking and admin trigger | `batch_runs`, `run_wrapper`, `/admin/batches`, batch registry | **Reuse** |
| Review UI | Ingestion review page, `MemePage` | **Extend** (new page, reuse components) |
| Per-run counters | `BatchRun.stats` JSON | Not enough: no outcome events that arrive after the run, no cross-process view |
| Business metrics | none | **New** (ADR-002) |
| Status meanings | scattered across `2026-07-25-image-visibility-status-design.md`, ingestion specs, batch docs | **New**: `docs/image-status.md` |

## 5. Risks

- **False positives** are the main risk: some memes have an intentional border or frame. Human review contains the damage; R7 measures precision before scheduling and the detector stays conservative (NFR-1).
- **Detection approach is open** (OQ-1). R2 starts with a spike on the labelled sample.
- **Migrations on three always-on environments.** Each new column and table needs a rollout plan (NFR-4).
- **Chains and loops.** A cut of a cut must not be proposed (FR-3).
- **A cut identical to an existing meme.** If the cut matches an image already in the corpus, duplicate detection should surface it; R5 decides how that interacts with review.
- **Shared-code changes.** Touching shared selection helpers needs the whole integration root run, as `CLAUDE.md` requires.
