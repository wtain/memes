# [P1] Implement the description_pipeline driver

- **Priority:** P1
- **Area:** Batch / Admin
- **Source:** board/design/153-description-tagging-pipeline-orchestration.md
- **Design:** docs/superpowers/specs/2026-10-01-description-tagging-pipeline-driver-design.md (approved), sections 1-4 and 6
- **Depends on:** 147
- **Tracker:** board/description-tagging-tracker.md

## Scope

- `batch/description_pipeline.py` with `main(trigger="manual", run_id=None)`, own kind `description_pipeline`, and a registry entry
  `description_pipeline` in `environments/batch_registry.yaml`.
- Steps as data (`Step(name, run, needs)`), declared order: `ocr_tags`, `note_lemmas`, `note_embeddings`, `describe`,
  `description_embeddings` (needs `describe`), `description_tags` (needs `describe`). Children are called in process with
  `run_id=None` so each self-tracks under its own kind.
- Outcomes `ok` / `failed` / `skipped_dependency` / `skipped_busy` (`BatchAlreadyRunningError`). A failure only skips dependents. Driver run is
  `failed` if any step failed, after all runnable steps ran. Summary in run `stats`.
- `image_descriptions.max_per_run` setting, passed as `limit` to `describe`; measure real Ollama throughput on each environment and set a value that
  finishes a slice well under an hour. Log and record processed/remaining counts.
- Unit tests with fake steps (ordering, `needs` skipping, busy mapping, final status, stats, independence); registry test.
- Docs in the same change: `CLAUDE.md` batch list entry, `docs/data-flow.md` section 4 and 5, `backend_api.md` if it enumerates batch names;
  verify the `/admin/batches` frontend lists the new entry with no change.
- Before merge run `batch/tests/` and the whole `tests/integration/` root (separate pytest invocations, see CLAUDE.md).

## Acceptance

Triggering `description_pipeline` from `/admin/batches` runs the six steps on active images, shows per-step runs in history, survives a failing
step without blocking independent ones, and a second trigger while running returns 409. Do not bind the always-on environment ports for testing.

## Implementation (2026-10-01)

Committed on branch `worktree-155-description-pipeline-driver` (f2f26da, on top of 147's 486af82; worktree `.claude/worktrees/155-description-pipeline-driver`), not merged. Merge 147 first.
Done: driver, registry entry, steps/skip/busy/failed semantics, per-step stats, docs (CLAUDE.md, data-flow.md). Verified: `batch/tests` (190), Backend admin-batch and scheduler tests (40), whole `tests/integration/` root (374).
**Still open:** `image_descriptions.max_per_run` is documented but unset (commented example in `environments/settings.yaml`). The value must be measured from real Ollama throughput per environment (not done: it would load the always-on GPU), so until then one pipeline trigger describes the whole backlog.
Also not done: a live end-to-end trigger from `/admin/batches` against a real database.

Merged to main 2026-10-01 (486af82 for 147, f2f26da for 155). Open item carried over: image_descriptions.max_per_run is unset and still to be measured.
