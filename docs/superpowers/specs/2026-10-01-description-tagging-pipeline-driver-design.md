# Description and Tagging Pipeline Driver — Design

Status: approved
Originates from: board/design/153-description-tagging-pipeline-orchestration.md
Tasks: board/todo/147-description-jobs-not-run-tracked.md, board/todo/155-description-pipeline-driver.md
Related: docs/data-flow.md, docs/superpowers/specs/2026-08-14-batch-scheduling-rollout-design.md (the `ingest_auto_prep` pattern this follows)

**Date:** 2026-10-01.

## Motivation

Refreshing the description and tagging data is about eight hand-run scripts per environment, in an order only the operator knows.
`ingest_auto_prep` solved the same problem for ingestion with one admin-triggerable driver. This spec adds the equivalent for
descriptions and tagging. Decisions already taken with the user (analysis of task 153, 2026-10-01):

1. Manual trigger from `/admin/batches` first. No `scheduler.jobs` entry.
2. New images join after `ingest_promote`; the driver processes `active` images only. No `--status pending` work (task 148 is not required).
3. Scope is description-focused. `tag_images_from_concepts` and `detect_entities_and_tag` (full rebuilds, GPU/CPU heavy) stay separate manual jobs.
4. GPU/Ollama contention across the three always-on environments is handled by the operator running one environment at a time.

## Non-goals

- Scheduling (revisit once contention is handled; see "Later").
- Human-review automation. Of the candidate gates, none block this driver: ingestion review happens before `ingest_promote` (before images
  are `active`); description feedback is an asynchronous correction loop (task 149); concept drafting (`/draft-lemma-concepts`) and note
  editing are out of band. The driver never waits for a human.
- Changing what the individual jobs compute.

## Design

### 1. The driver

New script `batch/description_pipeline.py`, registered in `environments/batch_registry.yaml` as

```yaml
description_pipeline:
  module: batch.description_pipeline
  kind: description_pipeline
```

It follows the existing self-tracking contract (`main(trigger="manual", run_id=None)` with `finish_existing_run` / `tracked_run`), under its
own `kind`, like `ingest_auto_prep`. Because of the existing one-active-run-per-kind index, two concurrent triggers of the driver are
rejected (409 from `/admin/batches`) with no extra locking.

Trigger path is unchanged: `/admin/batches` -> `batch.run_wrapper --script description_pipeline` -> `main(trigger, run_id)`. The wrapper's
post-run `build_statistics` refresh applies for free.

### 2. Steps as data

```python
@dataclass(frozen=True)
class Step:
    name: str
    run: Callable[[str], Awaitable[None]]   # takes the trigger string
    needs: tuple[str, ...] = ()             # names of steps whose FAILURE skips this one
```

Declared order (cheap and GPU-independent first, so an interrupted run loses the least):

| # | Step | Calls | Needs |
|---|---|---|---|
| 1 | `ocr_tags` | `build_tags_from_ocr.main(trigger, incremental=True)` | - |
| 2 | `note_lemmas` | `build_description_note_lemmas.main(trigger)` | - |
| 3 | `note_embeddings` | `build_description_note_embeddings.main(trigger)` | - |
| 4 | `describe` | `build_image_descriptions.main(trigger, limit=<cap>)` | - |
| 5 | `description_embeddings` | `build_image_description_embeddings.main(trigger)` | `describe` |
| 6 | `description_tags` | `build_tags_from_descriptions.main(trigger, incremental=True)` | `describe` |

Rationale for the shape:
- OCR tagging and the note jobs share nothing with descriptions, so they have no `needs`. A failed Ollama step must not block them.
- Steps 5 and 6 depend on `describe` only for freshness. If `describe` **raises** (config error, DB failure), they are skipped, because the
  system is evidently unhealthy and spending effort is pointless; the next trigger catches up since every step is incremental. If `describe`
  is skipped because it is already running elsewhere, 5 and 6 still run on whatever exists.
- 5 and 6 are independent of each other.
- Future steps (task 151 description lemmas, task 152 note tagging, task 149 feedback cleanup) are new `Step` entries with their own `needs`;
  the driver loop does not change. Entries 2-3 are included now because both jobs are incremental (`get_notes_needing_*`) and cheap.

### 3. Execution and failure semantics

The driver awaits each step in process, in declared order:

- Step outcomes: `ok`, `failed` (exception, message captured), `skipped_dependency` (a `needs` step failed), `skipped_busy` (the step's own
  kind already has an active run, i.e. `BatchAlreadyRunningError`, for example an operator running it by hand).
- A failure never aborts the loop; it only skips dependents. The driver run is marked `completed` if every step is `ok` or `skipped_busy`,
  and `failed` (error lists the failed steps) if any step failed, after all runnable steps have run.
- Each child is called with `run_id=None`, so it self-tracks under **its own kind** (nested runs). This gives per-step history in
  `/admin/batches` without extending the API, and reuses each child's one-active-per-kind guard. The driver additionally writes a summary
  to its run `stats`: `{"steps": {"<name>": {"status": "...", "error": "..."}}}`.
- Resume = trigger again. Every step is incremental and idempotent; permanently failed description pairs stay skipped (no `--retry-failed`
  from the driver; retrying is a deliberate manual action).
- No in-driver retry or backoff.

### 4. Bounding the Ollama step

`describe` is the only long step. The driver passes a per-run cap so one trigger does a bounded slice and the operator re-triggers until the
backlog is empty:

- New tracked-config key `image_descriptions.max_per_run` (per environment in `settings.<env>.yaml`; absent means unlimited).
  Initial value is chosen from measured throughput so a slice finishes in well under an hour; the number is an implementation-time measurement, not
  fixed here.
- The driver logs, and `describe`'s own run stats record, how many images were processed and how many remain, so the operator can see
  whether another trigger is needed.
- Whether the Windows ~10 minute kill (a documented limit for agent `run_in_background` tool calls) also affects subprocesses spawned by the
  backend is unverified. The cap makes the design safe either way, and a killed run is just a resumable incremental one. The orphan guard
  (`max_runtime_minutes`) only applies to scheduler-driven kinds, which this is not.

### 5. Required changes to existing jobs (task 147, prerequisite)

Task 147 is the only prerequisite. For `build_image_descriptions` and `build_image_description_embeddings`:

- Add `main(trigger="manual", run_id=None, ...)` with `tracked_run` / `finish_existing_run` under kinds `build_image_descriptions` and
  `build_image_description_embeddings`; keep the CLI flags (`--reset`, `--limit`, `--retry-failed`) working.
- Add both to `environments/batch_registry.yaml`, which also makes them individually admin-triggerable.
- `build_image_descriptions` already restricts to `active` images (`get_all_images(status="active")`), so no status work is needed.

Other jobs already have the contract: `build_tags_from_ocr`, `build_tags_from_descriptions` (`incremental` parameter, defaults true),
`build_description_note_lemmas`, `build_description_note_embeddings`.

### 6. Surfaces

- `environments/batch_registry.yaml`: the driver entry.
- `backend_api.md`: `/api/admin/batches` lists names dynamically from the registry; verify whether its description enumerates names and update if so.
- `CLAUDE.md` batch list: add `description_pipeline` (order, `kind`, manual-trigger only, what it deliberately excludes) per the file's own rule.
- `docs/data-flow.md` section 4 (scheduling and admin triggering): replace "Not in the registry" and "no pipeline" with the driver.
- Frontend `/admin/batches` needs no change if it renders the registry list; the implementation task verifies this.

## Alternatives considered

- **Independent scheduled jobs, ordered by time offsets.** Rejected: ordering becomes implicit, slow Ollama runs overlap the next job, and the
  2026-08-14 policy keeps enrichment manual.
- **Strict linear chain like `ingest_auto_prep`.** Rejected: that chain is a real dependency sequence; this one is one dependent group
  plus independent branches, and stopping at the first error would let an Ollama failure block OCR tagging.
- **Extend `ingest_auto_prep` with description steps.** Rejected: descriptions are GPU-heavy and not needed for duplicate review; they
  would also run for images that review may reject. `--status pending` would be needed on three more jobs.
- **Driver spawns children as subprocesses via `run_wrapper`.** Rejected for now: heavier, and in-process matches `ingest_auto_prep`.
  Revisit if memory pressure from loading Ollama-side and SBERT models in one process shows up.

## Testing

- Unit tests for the driver with fake steps: ordering, `needs` skipping on failure, `skipped_busy` mapping, final status and the stats
  summary, and that a failure does not stop independent steps.
- Registry test: the new entry resolves and its module exposes `main(trigger, run_id)`.
- For task 147: tests that each description job's `main` works with and without `run_id` and that the CLI is unchanged.
- Per CLAUDE.md: shared-code changes are not expected here, but the whole `tests/integration/` root and `batch/tests/` run before merge.

## Later (explicitly out of this spec)

- Scheduling the driver, which needs a machine-wide Ollama lock across environments (file or advisory lock) and an interval/`max_runtime_minutes` chosen against the cap.
- Chaining the driver after `ingest_promote`.
- Steps for tasks 149, 151, 152 as they land.

## Resolved review points (user, 2026-10-01)

1. Driver name is `description_pipeline` (module, registry name and kind).
2. Note steps 2-3 are included in the driver.
3. The initial `image_descriptions.max_per_run` value is still measured during implementation (task 155).
