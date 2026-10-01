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

---

## Analysis (2026-10-01, session 12ec6cf0-9a05-40be-ad18-6802263c7ac5)

Status: findings below are from reading code and specs; requirement questions are pending the user's answers (see "Questions").

### What exists today (verified)

- `batch/ingest_auto_prep.py` is the template: one driver, `main(trigger, run_id)`, own `kind` (`ingestion_auto_prep`), calls each step's
  `main()` **in process**, in order, stops on the first exception (the run is marked failed), manual-trigger only via `/admin/batches`.
- Already registered and run-tracked: `build_tags_from_ocr`, `build_tags_from_descriptions`, `build_description_note_lemmas`,
  `build_description_note_embeddings`, `tag_images_from_concepts`, `detect_entities_and_tag`, `build_concept_embeddings`, `build_bow`,
  `build_statistics` (the wrapper also refreshes statistics after every wrapped batch, so it needs no slot in the chain).
- Not registered, not tracked (task 147): `build_image_descriptions` (`--limit`, `--retry-failed`, `--reset`, no `--status`),
  `build_image_description_embeddings` (`--reset` only, no `--status`).
- Scheduler: only `trends_batch` and `build_statistics` are scheduled. The 2026-08-14 rollout deliberately made enrichment manual.
  The scheduler force-fails an active run older than `max_runtime_minutes` as orphaned, so a long Ollama step needs either a large
  `max_runtime_minutes` or a `--limit` cap per run.
- Task 150 (verification) made `build_tags_from_descriptions --incremental` retag re-described images, so the "retag on re-describe"
  gotcha from the audit is solved and the chain can rely on incremental runs.

### Real data dependencies (what actually forces an order)

| Step | Needs | Notes |
|---|---|---|
| `build_image_descriptions` | image files | GPU/Ollama, slow, the only expensive step |
| `build_image_description_embeddings` | descriptions | CPU/GPU SBERT, incremental per description |
| `build_tags_from_descriptions` | descriptions | independent of the embeddings step |
| description lemmas (151, future) | descriptions | independent of embeddings and tagging |
| `build_tags_from_ocr` | OCR text | independent of descriptions entirely |
| `build_description_note_lemmas` / `_embeddings` | human notes | event-driven by note edits, not by chain position |
| `tag_images_from_concepts` | CLIP embeddings + concept embeddings | full rebuild of source `CONCEPT`, not incremental |
| `detect_entities_and_tag` | image files | full rebuild of source `YOLO`, heavy |

So the "chain" is really **one dependent pair/triple** (descriptions -> embeddings, tags, [lemmas]) plus **independent branches** that
only share the concern "cheap incremental refresh". A strict linear chain over-couples them: a failed Ollama step would block OCR
tagging, which does not need it.

### Human gates: which are real

| Candidate gate | Blocks the chain? | Analysis |
|---|---|---|
| Ingestion Tier A/B review | Yes, for promotion | Only real blocking gate; decides whether new images enter the chain before or after `ingest_promote` |
| Description feedback review (149) | No | Unreviewed stays included; a reject excludes later. It is an asynchronous correction loop, not a stop sign. Needs cleanup of already-derived data when a reject arrives |
| Concept drafting (`/draft-lemma-concepts`) | No | Out of band; drafts pile up for review, deliberately not scheduled (rollout spec). Keep it outside the chain |
| Note editing | No | Event, not gate. Needs a refresh trigger (152), not a place in the chain |

### Constraints and risks

- **GPU contention:** metal, general and IT backends are always on, one workstation. The scheduler is per-environment with no
  cross-environment lock, so three environments could start Ollama runs at once. Needs a stagger or a machine-level mutex.
- **Windows ~10 minute kill** (CLAUDE.md gotcha) is documented for agent tool background calls. Whether it applies to
  scheduler/admin-spawned subprocesses is **unverified**; either way a `--limit` cap with resumable incremental runs is the safe design.
- **Permanently failed pairs** are skipped without `--retry-failed`; the driver must not silently retry them every tick.
- **Describing pending images** wastes GPU time on images that Tier A/B may reject as duplicates.

### Observations that affect other tasks

- If the chain only processes `active` images, task 148 (`--status pending` for description jobs) is not needed for the chain. It would
  only matter if descriptions must exist during ingestion review, and the review UI and duplicate matching do not use them.
- 147 is a hard prerequisite (tracking + registry); 150 is done; 151/152/149 add or change steps but not the driver's shape if the
  driver takes its step list as data.

### Questions (for the user)

1. Trigger model: manual driver only, scheduled, or manual driver that can also be scheduled later?
2. Do new images join the chain before review (pending) or after `ingest_promote` (active only)? Decides the fate of 148.
3. Scope: description-focused chain only, or also the heavy full-rebuild CLIP/YOLO tagging jobs (a separate lower-cadence driver)?
4. GPU contention: stagger per environment, a machine-wide lock, or accept manual serial running?

### Recommendation (pending answers)

One driver, `description_auto_prep` (own `kind`), manual-trigger first via `/admin/batches`, steps defined as a list so 151/152 can add
steps later; run on `active` images only (drop or demote 148); independent branches continue past a failure while dependent steps
are skipped, with per-step outcome recorded in the run's stats; each Ollama run capped by `--limit`; heavy CLIP/YOLO tagging stays a
separate, lower-cadence manual job outside this driver.

### Decisions (user, 2026-10-01)

1. Trigger: manual driver first (via `/admin/batches`); scheduling deferred until GPU contention is handled.
2. New images join the chain **after `ingest_promote`**, active images only. Task 148 (`--status pending`) is not needed by this chain.
3. Scope: description-focused chain. `tag_images_from_concepts` / `detect_entities_and_tag` stay separate manual jobs outside the driver.
4. GPU/Ollama contention: manual serial running across environments for now; revisit a lock when scheduling is added.

### Acceptance criteria for the design

- A single registered driver (own `kind`, `main(trigger, run_id)` contract), step list defined as data so 151/152 steps can be added.
- Steps: descriptions (capped by `--limit`) -> description embeddings, description tagging (independent of each other) -> OCR tagging.
  Independent branches continue past a failure; dependent steps are skipped; per-step outcome recorded in run stats.
- Resumable by rerun (incremental steps); permanently failed description pairs are not retried unless asked.
- Prerequisite: 147 (tracking + registry for the two description jobs).
- Output: design spec in docs/superpowers/specs/, then implementation tasks.

Analysis complete; moving to design.

---

## Design (2026-10-01)

Spec: docs/superpowers/specs/2026-10-01-description-tagging-pipeline-driver-design.md (status `draft`, awaiting user review).
On approval: spec -> `approved`, task -> implementation tasks created in `board/todo` (driver + tests; 147 is the prerequisite).

Design approved 2026-10-01 (driver name `description_pipeline`). Implementation tasks: 147 (prerequisite), 155 (driver). This task closes when 155 is done.

Merged to main 2026-10-01 (486af82 for 147, f2f26da for 155). Open item carried over: image_descriptions.max_per_run is unset and still to be measured.
