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

## Update 2026-10-01 (design of 153)

This is the prerequisite for task 155 (`description_pipeline` driver, spec docs/superpowers/specs/2026-10-01-description-tagging-pipeline-driver-design.md, section 5).
Requirements from the spec: kinds `build_image_descriptions` and `build_image_description_embeddings`; `main(trigger="manual", run_id=None, ...)`
keeping `reset`/`limit`/`retry_failed` as keyword parameters with CLI flags unchanged; registry entries; tests that `main` works with and
without `run_id`. `build_image_descriptions` already covers `active` images only. It should record processed and remaining counts in its run stats.


## Implementation (2026-10-01)

Committed on branch `worktree-147-description-jobs-run-tracking` (486af82, worktree `.claude/worktrees/147-description-jobs-run-tracking`), not merged.
Both jobs follow `main(trigger, run_id, ...)`, record run stats, and are in `batch_registry.yaml`. Verified: `batch/tests` (178), Backend admin-batch and scheduler tests (40),
`tests/integration/test_build_image_descriptions.py` (2). Not done: whole `tests/integration/` root, live run against a real DB/Ollama.

Merged to main 2026-10-01 (486af82 for 147, f2f26da for 155). Open item carried over: image_descriptions.max_per_run is unset and still to be measured.
