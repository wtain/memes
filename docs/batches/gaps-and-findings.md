# Gaps and findings

Status: draft (2026-10-10). Observations made while reading the code against the existing documents.
Nothing here was changed in code or in the older documents.

## A. Documents that are stale or silent

| # | Where | What | Evidence |
|---|---|---|---|
| A1 | `CLAUDE.md` batch pipeline | Does not say that `build_concept_embeddings` chains into `tag_images_from_concepts` (and has `--no-chain`). Easy to trigger a full CLIP re-tagging by accident | `batch/build_concept_embeddings.py:286` |
| A2 | `CLAUDE.md` | `fix_image_formats` is described as a maintenance script but nothing says it is run-tracked yet not admin-triggerable | `batch/fix_image_formats.py`, registry |
| A3 | `docs/superpowers/specs/2026-08-14-batch-scheduling-rollout-design.md` amendment | Says only `trends_batch` remains scheduled; `build_statistics` is scheduled too (added later by the statistics spec) | `environments/settings.yaml` `scheduler.jobs` |
| A4 | `environments/settings.yaml` comment above the jobs | The "deliberately NOT scheduled" list names 11 jobs but the registry now holds 25 manual-only jobs. The comment reads as complete when it is not | registry vs comment |
| A5 | `docs/runbooks/ingestion-pipeline.md` | Correct, but does not state that `ingest_auto_prep` fails (instead of skipping) when one of its tracked children is already running | `batch/ingest_auto_prep.py` catches only `RuntimeError` |
| A6 | Whole repo | No single document covered the execution model (wrapper, registry, scheduler, admin, chaining), the per-environment schedule, or the use-case flows. Now in `docs/batches/` | this folder |
| A7 | 32 modules in `batch/` (over 20 lines) | No module docstring (for example `build_bow`, `build_tags_from_ocr`, `clusterize`, `rebuild_duplicates`, `move_flagged`, `trends_batch`). Behaviour is only documented in CLAUDE.md and specs | AST scan |

## B. Behaviour worth knowing (potentially surprising)

| # | Finding | Impact |
|---|---|---|
| B1 | Admin and scheduler start jobs through `run_wrapper`, which calls `main(trigger, run_id)` only. No flags can be passed | `--full`, `--limit`, `--status`, `--retry-failed`, `--reset` are shell-only |
| B2 | Orphan recovery (`started` older than `max_runtime_minutes` is failed) exists only inside the scheduler tick. A CLI or admin-started run that is killed leaves a `started` row that blocks its kind (HTTP 409 / `BatchAlreadyRunningError`) | For the 25 unscheduled kinds, the fix is manual: fail the row in `batch_runs`. No other recovery path was found |
| B3 | `GET /api/admin/batches/runs` lists only registry kinds | Runs of kind `ingestion` and `fix_image_formats` are not visible in the admin UI |
| B4 | Run logs (`logs/<env>/<script>_<ts>.log`) are not linked to a `run_id` | Correlation is by script name and time |
| B5 | `extract_text_from_memes` and `build_image_embeddings` are neither tracked nor in the registry | The two most expensive core steps have no run history, no admin trigger and no concurrency guard (two copies can run at once) |
| B6 | `ingest_auto_prep` runs tracked children with `run_id=None`, so they create their own rows; if the same kind is already active the driver run fails | Do not trigger `build_ocr_lemmas`, `classify_text_heavy` or `build_ocr_text_embeddings` while the driver is in flight |
| B7 | Registry names and kinds differ for three entries: `build_statistics` (`statistics`), `trends_batch` (`trends`), `ingest_auto_prep` (`ingestion_auto_prep`). `scheduler.jobs[].batch_run_kind` must use the kind | Easy to misconfigure a new job |
| B8 | `it` runs `trends_batch` every 6 h with no environment-level `trends` defaults | Result is only meaningful if each source carries its own `extraction` config. Not checked against the `it` database |
| B9 | `description_pipeline` has no environment guard. On `it` the tagging steps (1, 4, 8) have no tagging YAML | Expect failures reported in `stats.steps` there |
| B10 | `build_ocr_lemmas` full run deletes the entire table even for `--status pending`; `build_tags_from_notes` CLI default is a full run while the admin path is incremental | Footguns on the CLI; the code already requires `--incremental` for pending lemmas |
| B11 | `batch_registry.<env>.yaml` overlays are supported by `BatchRegistry` but none exist; all three environments expose the same admin job list | Per-environment hiding of unsuitable jobs (for example on `it`) is possible without code changes |
| B12 | The first `ingest_auto_prep` tick in an environment with no ingestion history creates an empty `ingestion` run that stays open at `tier_a_review` | Documented in the script; harmless |
| B13 | Tier A candidates found after the run reached `tier_b_review` never surface in the review UI | Inherited limitation, documented in the rollout spec |

## C. Candidate follow-ups (not done, for the owner to decide)

1. Register `fix_image_formats` (it already satisfies the contract) so it appears in `/admin/batches`.
2. Add a one-line pointer to `docs/batches/` in `CLAUDE.md` (batch pipeline section) and fix A1.
3. Update the stale comment in `environments/settings.yaml` (A4) and the amendment in the rollout spec (A3).
4. Consider making `ingest_auto_prep` treat `BatchAlreadyRunningError` from a child as "busy, skip" as
   `description_pipeline` does (B6).
5. Consider a generic stale-run reaper for kinds not on the schedule (B2), for example in the admin trigger path.
6. Consider tracking `extract_text_from_memes` and `build_image_embeddings` (B5), which would also allow admin triggers.
7. Add module docstrings for the undocumented scripts (A7).
