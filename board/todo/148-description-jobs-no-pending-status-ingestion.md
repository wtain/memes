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
