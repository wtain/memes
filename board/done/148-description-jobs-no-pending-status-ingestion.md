# [P2] Description jobs cannot cover an in-flight ingestion batch (no --status pending)

- **Priority:** P2
- **Area:** Batch / Ingestion
- **Source:** audit notes (see tracker)
- **Depends on:** 147

## Problem

`build_image_descriptions`, `build_image_description_embeddings` and the description tagging job have no `--status` flag, so
`ingest_auto_prep` cannot include them. New images are only described after promotion, by a manual run.

## Scope

Add `--status {active,pending}` to the three jobs (same convention as `build_ocr_lemmas`). Decide with the pipeline task (153)
whether `ingest_auto_prep` chains them or whether they run after `ingest_promote`. Descriptions are GPU-heavy, so chaining them
before review may be the wrong call; analysis needed.

## Update 2026-10-01 (analysis of 153)

User decision: new images join the description chain after `ingest_promote`, active only. This task is not needed by the pipeline;
keep only if descriptions are later wanted during ingestion review. Candidate to close as won't-do.

## Analysis (2026-10-01, session 12ec6cf0-9a05-40be-ad18-6802263c7ac5)

Recommendation: close as won't-do. Evidence:

- Nothing in ingestion consumes descriptions. Duplicate matching (Tier A/B) uses CLIP, OCR-text embeddings and OCR lemmas only, and the
  `/ingestion` review UI shows OCR text per member, not descriptions. Description UI exists only on the active-image detail page
  (`MemeDetails`).
- A promoted image needs no special handling: the description jobs and `build_tags_from_ocr` already default to `status="active"`, so the next
  `description_pipeline` run (task 155) picks it up (documented in `batch/ingest_promote.py`).
- Describing pending images costs GPU time on images that review may still reject, which is the main downside of the original idea.
- `--status pending` on three more jobs would also add surface (and a chance of reaching a reviewer with unreviewed state) with no consumer.

Revisit only if descriptions are ever wanted during review (for example to help reviewers or as a duplicate signal, see task 154).

Closed as won't-do 2026-10-01 (user confirmed).
