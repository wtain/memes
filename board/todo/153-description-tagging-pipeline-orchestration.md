# [P1] Pipeline: scheduled chain of description and tagging jobs, with human gates

- **Priority:** P1
- **Area:** Batch / Scheduler / Design
- **Source:** user request 2026-10-01
- **Depends on:** 147, 148 (150, 151, 152 shape the job list)

## Goal

Replace "run ~8 scripts by hand" with a pipeline of scheduled tasks, keeping humans in the loop where needed (like
`ingest_auto_prep`, which chains jobs under its own `kind`, is manual-trigger and leaves review to people).

## Candidate chain

`build_image_descriptions` -> `build_image_description_embeddings` -> description tagging -> `build_tags_from_ocr` ->
`tag_images_from_concepts` / `detect_entities_and_tag` (lower cadence) -> note lemma/embedding jobs -> `build_statistics`.

## Open questions

Scheduled vs manual trigger (all enrichment jobs are deliberately manual today, see
2026-08-14-batch-scheduling-rollout-design.md); GPU/Ollama contention with the always-on environments; resumability under the
~10 minute Windows limit; human gates (description feedback review, concept drafting via `/draft-lemma-concepts`, note editing);
failure and resume semantics; whether newly ingested images join the chain before or after `ingest_promote`.

Output: a design spec (architectural path), then implementation tasks.
