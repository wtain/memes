# [P2] build_image_descriptions and build_image_description_embeddings are not run-tracked or admin-triggerable

- **Priority:** P2
- **Area:** Batch / Backend admin
- **Source:** docs/superpowers/specs/drafts/2026-10-01-description-tagging-audit-and-pipeline-notes.md
- **Tracker:** board/description-tagging-tracker.md

## Problem

Neither job uses `tracked_run`/`finish_existing_run`, and neither has an entry in `environments/batch_registry.yaml`. They
run from the shell only: no `/admin/batches` trigger, no history, no scheduler eligibility (the scheduler requires a `batch_run_kind`).

## Scope

Add run tracking (`main(trigger=, run_id=)` contract like `build_tags_from_descriptions`) and registry entries. Respect the Windows
~10-minute background kill for the Ollama job (use a `--limit` cap and resumable incremental runs).

## Pointers

`batch/build_image_descriptions.py`, `batch/build_image_description_embeddings.py`, `batch/run_tracking.py`,
`environments/batch_registry.yaml`, `batch/build_description_note_embeddings.py` (reference for the pattern).
