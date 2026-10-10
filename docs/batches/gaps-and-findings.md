# Gaps and findings

Status: draft (2026-10-10, B14 and the `general` schedule note added 2026-10-11). Observations made while reading the code against the existing documents.
Nothing here was changed in code or in the older documents.

## Resolved on 2026-10-10

| Item | Resolution |
|---|---|
| A1, A2 | `CLAUDE.md` now states the `build_concept_embeddings` chain and that `fix_image_formats` is admin-triggerable |
| A3 | Amendment in the rollout spec now mentions `build_statistics` |
| A4 | Comment in `environments/settings.yaml` now points at the registry and `docs/batches/inventory.md` instead of a partial list |
| A5 | Warning added to the Concurrency section of `docs/runbooks/ingestion-pipeline.md` |
| B3 (partly) | `fix_image_formats` is now registered, so its runs show in `/admin/batches` (test: `batch/tests/test_fix_image_formats_main.py`). `ingestion` runs are still not listed |
| C1, C2, C3 | Done (registry entry, `CLAUDE.md` pointer, comment fixes) |
| A7 | Module docstrings added to the 27 job scripts that had none (derived from reading each script) |
| B5 | `extract_text_from_memes` and `build_image_embeddings` now follow the `main(trigger, run_id)` contract, are registered, and tracked under their own kinds. Admin and scheduler runs use the defaults: status `active`, and for embeddings always incremental (`batch/tests/test_tracked_embeddings_and_ocr_main.py`) |
| B6 | `ingest_auto_prep` ends the tick quietly when a child is already running instead of failing (`batch/tests/test_ingest_auto_prep.py`) |
| Docs fix | `move_reference_duplicates` was described backwards; corrected (it moves unregistered `BASE_PATH` files identical to a reference-dir file into `possible_duplicates/`) |
| Test hygiene | `test_ingest_auto_prep.py` replaced `.main` on real modules and never restored it, which broke later test files; now restored by a fixture |

Everything not listed here is still open; see the sections below. The original text of resolved rows is kept for history.

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
| B11 | `batch_registry.<env>.yaml` overlays are supported by `BatchRegistry` but none exist; all three environments expose the same admin job list (including `export_ratings` and `import_judgments`, which only do work on `general`; there they finish as a `skipped` no-op) | Per-environment hiding of unsuitable jobs (for example on `it`) is possible without code changes |
| B12 | The first `ingest_auto_prep` tick in an environment with no ingestion history creates an empty `ingestion` run that stays open at `tier_a_review` | Documented in the script; harmless |
| B13 | Tier A candidates found after the run reached `tier_b_review` never surface in the review UI | Inherited limitation, documented in the rollout spec |
| B14 | The scheduled `import_judgments` (weekly, `general` only) changes a committed file, `evaluation/gold/judgments.json`, in the working tree of the machine that runs the backend. Baselines recorded under `evaluation/gold/` carry the `judgmentsSha` of the file they were measured against, so after an import they no longer match it, and `python -m evaluation compare` of an old baseline against a new run mixes two judgment sets | Mitigation built in: the job never commits and never overwrites an existing judgment, and its stats carry a note and the changed file. Process: review `git diff evaluation/gold/judgments.json`, commit it, and record a fresh baseline (`python -m evaluation run ... --label L`) before comparing against older ones. The repo also now has an environment-dependent scheduler list (`general` runs four jobs, the others two): do not assume the schedule is identical across environments |

## C. Still open (need an owner decision or information)

1. B2, stale runs: a per-kind maximum runtime is needed for the 28 unscheduled kinds, or another policy.
2. B8, B9, B11, environment `it`: which jobs should be exposed there (an `environments/batch_registry.it.yaml` overlay
   is supported by the loader), and whether `trends_batch` should run there.
3. B3: `ingestion` runs are still not visible in `/admin/batches`.
4. B4: no link between a `run_id` and its log file.
