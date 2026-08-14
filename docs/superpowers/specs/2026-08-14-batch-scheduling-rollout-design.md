# Batch Scheduling Rollout — Design

Status: done
Plan: docs/superpowers/plans/2026-08-14-batch-scheduling-rollout.md

**Date:** 2026-08-14.

**Amendment (2026-08-14, post-implementation):** the `scheduler.jobs` entries for
`ingest_auto_prep` and the 6 refactored downstream scripts described below were removed after
review — all 7 are manual-trigger-only via `/admin/batches` (each still has its
`batch_registry.yaml` entry), not on an automatic schedule. Only `trends_batch` (pre-existing,
unrelated to this spec) remains scheduled. The design and code below are otherwise unchanged;
read `interval_minutes`/`max_runtime_minutes` mentions as the values used *if* a job is
scheduled, not as currently-active schedules.

---

## Motivation

The ingestion pipeline (`docs/runbooks/ingestion-pipeline.md`) and the downstream enrichment
pipeline (`build_tags_from_ocr`, `build_ocr_lemmas`, ...) are both entirely manual today — an
operator runs each `python -m batch.<script>` command by hand, in order, per environment. A
generic recurring-job scheduler already exists
(`docs/superpowers/specs/2026-07-27-batch-job-scheduler-design.md`, embedded in each
environment's backend `lifespan`) and has since grown a registry-driven trigger mechanism shared
with the `/admin/batches` manual-trigger UI: `environments/batch_registry.yaml` maps a public
script name to `{module, kind}`, and `batch/run_wrapper.py` is the uniform subprocess entrypoint
both the scheduler and the admin endpoint invoke (`python -m batch.run_wrapper --script <name>
--env <env> --trigger {scheduled,manual} [--run-id <uuid>]`). Only three scripts
(`trends_batch`, `move_flagged`, `unregister_deleted_images`) currently implement the
`main(trigger, run_id)` self-tracking contract this mechanism requires. This spec brings the
ingestion prep stages and a chosen set of downstream enrichment scripts onto the same mechanism.

## Scope

**In scope:**
- A new driver script, `batch/ingest_auto_prep.py`, that automates the ingestion pipeline's
  fully-automatable prep stages (hash dedup through Tier A duplicate-finding).
- A stage-ordering fix to `batch/ingest_find_duplicates.py`, needed for the driver to be safe to
  run on a timer (see "A pre-existing bug this surfaces" below).
- Refactoring 7 downstream enrichment scripts to the existing self-tracking contract and
  registering them for scheduling: `build_tags_from_ocr`, `build_ocr_lemmas`,
  `build_tags_from_descriptions`, `build_concept_embeddings`, `detect_entities_and_tag`,
  `tag_images_from_concepts`, `build_bow`.
- `scheduler.jobs` config entries for all 8 new jobs in `environments/settings.yaml`, applied to
  all three environments (metal/general/IT), matching the existing `trends_batch` entry's scope.

**Out of scope (deliberately not scheduled):**
- **Tier A/B human review itself** — stays a manual step in the `/ingestion` UI. Nothing here
  changes that.
- **`ingest_find_duplicates --tier tier_b` and `ingest_promote`** — not run automatically. Both
  depend on a human having finished the prior tier's review; auto-running them risks promoting
  images (or advancing the review queue) out from under a reviewer. An operator runs these two
  from the runbook once review is done, same as today. (A future "full auto-advance driver" that
  detects tier completion and runs these automatically was considered and explicitly rejected for
  this iteration — see "Rejected alternative" below.)
- **`build_image_descriptions`** — expensive (multi-prompt Ollama LLM calls), explicitly excluded
  from this rollout; can be added later with its own interval if wanted.
- **`build_lemma_clusters` / `draft_concepts_from_clusters`** — these draft new concept/tag YAML
  entries for a human to review, the same way Tier A/B produce candidates for a human to decide.
  The existing `/draft-lemma-concepts` command already runs them deliberately, on demand.
  Auto-running them on a timer would silently pile up unreviewed drafts with no review UI for
  them (unlike Tier A/B, which has one).
- Any change to the scheduler's own core loop (`Backend/app/scheduler.py`, `_should_run`,
  `_initial_delay`, `_spawn`) — that mechanism is unchanged; this spec only adds jobs to it and
  brings more scripts onto its existing contract.

## Design

### 1. `batch/ingest_auto_prep.py` — the ingestion prep driver

A new script, following the existing self-tracking contract exactly (see `trends_batch.py` /
`move_flagged.py` for the established pattern):

```python
async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _run_prep_chain()
    else:
        async with tracked_run(kind="ingestion_auto_prep", trigger=trigger):
            await _run_prep_chain()
```

`_run_prep_chain()` awaits, in process, in order:

1. `ingest_hash_dedup.main(env=None)`
2. `ingest_validate_formats.main(env=None)`
3. `build_image_embeddings.main(incremental=True, target_status="pending")`
4. `extract_text_from_memes.main(path=settings.BASE_PATH, target_status="pending")`
5. `ingest_find_duplicates.main(env=None, tier="tier_a", k=None)`

Each call is wrapped so that a `RuntimeError("No ingestion run is currently in progress...")` from
steps 2-5 (raised when there's genuinely nothing pending — the common case on most ticks, since
the inbox is usually empty) is caught and treated as "nothing to do this tick," not a failure.
`ingest_hash_dedup` itself never raises this — it's the step that creates or joins the run — so
after it runs, either a run now exists (in which case downstream steps proceed) or it didn't find
anything (in which case downstream steps' `RuntimeError` is the expected, swallowed signal).

**Why a separate `kind="ingestion_auto_prep"` and not `kind="ingestion"`:** the scheduler's
concurrency guard (`_should_run` in `Backend/app/scheduler.py`) treats an "active" `BatchRun` of a
job's `batch_run_kind` older than `max_runtime_minutes` as an orphaned crash, and force-fails it
to unblock scheduling. A real ingestion run (`kind="ingestion"`) can legitimately stay `started`
for days while a human works through Tier A/B review — that is not orphaned, and must never be
force-failed by the scheduler. Using a distinct kind for the driver's own scheduler-tick
bookkeeping (bounded to one prep-chain execution, a few minutes at most) keeps the scheduler's
timeout semantics meaningful without touching the real ingestion run's lifecycle at all. This
mirrors the existing per-environment isolation principle already used elsewhere in this codebase:
separate concerns get separate tracking rows, not overloaded shared state.

This also means `ingest_auto_prep`'s own `BatchRun` rows carry no useful `stats` beyond "ran" /
"failed" — the real numbers (files moved, embeddings created, candidates found) already land in
the `kind="ingestion"` run's `stats` via each existing step's own `update_stats` calls. No
duplication needed.

### 2. A pre-existing bug this surfaces — `ingest_find_duplicates.py` stage ordering

`ingest_validate_formats.py` already guards its own stage transition:

```python
def should_advance_stage(current_stage: str | None) -> bool:
    """The stage must only ever advance, never rewind..."""
    return current_stage == "hash_dedup"
```

`ingest_find_duplicates.py` has no equivalent guard — it unconditionally runs
`await runs_repo.set_stage(active_run.run_id, TIER_STAGE[tier])` every time it's called. This is
already a latent bug reachable today: the runbook's own "Concurrency" section tells an operator to
re-run `ingest_find_duplicates.py` for "both tiers, as applicable" after a mid-review re-join —
if the run has already reached `tier_b_review` and someone re-runs `--tier tier_a` per that
instruction, it silently rewinds `stage` back to `tier_a_review`, which makes the `/ingestion`
frontend (`tierForStage()` in `IngestionReviewPage.tsx`) switch a reviewer back to the Tier A
queue mid-Tier-B-review.

`ingest_auto_prep.py` would hit this on every single tick once a run reaches `tier_b_review`, so
fixing it is required for the driver to be safe — not optional polish. Fix, mirroring the existing
pattern:

```python
_STAGE_ORDER = ["hash_dedup", "format_validation", "tier_a_review", "tier_b_review"]

def should_advance_stage(current_stage: str | None, target_stage: str) -> bool:
    current_index = _STAGE_ORDER.index(current_stage) if current_stage in _STAGE_ORDER else -1
    return _STAGE_ORDER.index(target_stage) > current_index
```

applied before the existing `set_stage` call in `ingest_find_duplicates.py`'s `main()`. This also
fixes the existing manual re-join workflow, independent of scheduling.

### 3. Downstream enrichment scripts — self-tracking refactor

Each of the 7 chosen scripts gets the same mechanical change `trends_batch.py` already
demonstrates:

- `main()` signature becomes `main(trigger: str = "manual", run_id: uuid.UUID | None = None)`.
- Body wrapped in `finish_existing_run(run_id)` (when the caller pre-created a run — the admin
  endpoint's case) or `tracked_run(kind=<script_name>, trigger=trigger)` (self-created — the
  scheduler's case, `run_id=None`), matching `move_flagged.py`'s exact shape.
- The `incremental: bool` parameter (present on `build_tags_from_ocr`, `build_ocr_lemmas`,
  `build_tags_from_descriptions`) is fixed to `True` inside `main()` for both trigger paths — a
  scheduled or admin-triggered run must never silently kick off a full rebuild. The existing
  `--incremental` CLI flag in each script's own `__main__` block is unchanged for direct manual
  invocation.
- `__main__` blocks are unchanged apart from calling the new `main()` signature
  (`asyncio.run(main())`, trigger defaults to `"manual"` — same pattern as every existing
  self-tracked script).
- `kind` per script (also the `batch_registry.yaml` key and `scheduler.jobs.batch_run_kind`):
  `build_tags_from_ocr`, `build_ocr_lemmas`, `build_tags_from_descriptions`,
  `build_concept_embeddings`, `detect_entities_and_tag`, `tag_images_from_concepts`, `build_bow` —
  one new kind per script, no sharing.

None of these scripts have any inter-dependency enforcement needed at the scheduling layer: each
is independently idempotent/incremental, so if e.g. `build_tags_from_descriptions` ticks before
any descriptions exist, it's simply a no-op that tick and picks up more next time. No ordering
coordination between jobs is introduced beyond what already exists (each job's own independent
`_job_loop`).

### 4. Registry and scheduler config

`environments/batch_registry.yaml` gains 8 entries (`ingest_auto_prep` + the 7 downstream
scripts), each `{module: batch.<script>, kind: <script_name>}`, following the existing
`trends_batch` / `move_flagged` entries exactly.

`environments/settings.yaml`'s `scheduler.jobs` list gains 8 entries:

```yaml
scheduler:
  jobs:
    - name: trends_batch          # existing, unchanged
      script: trends_batch
      batch_run_kind: trends
      interval_minutes: 360
      max_runtime_minutes: 60
      enabled: true
    - name: ingest_auto_prep
      script: ingest_auto_prep
      batch_run_kind: ingestion_auto_prep
      interval_minutes: 15
      max_runtime_minutes: 30
      enabled: true
    - name: build_tags_from_ocr
      script: build_tags_from_ocr
      batch_run_kind: build_tags_from_ocr
      interval_minutes: 60
      max_runtime_minutes: 30
      enabled: true
    - name: build_ocr_lemmas
      script: build_ocr_lemmas
      batch_run_kind: build_ocr_lemmas
      interval_minutes: 60
      max_runtime_minutes: 30
      enabled: true
    - name: build_tags_from_descriptions
      script: build_tags_from_descriptions
      batch_run_kind: build_tags_from_descriptions
      interval_minutes: 60
      max_runtime_minutes: 30
      enabled: true
    - name: build_concept_embeddings
      script: build_concept_embeddings
      batch_run_kind: build_concept_embeddings
      interval_minutes: 120
      max_runtime_minutes: 30
      enabled: true
    - name: detect_entities_and_tag
      script: detect_entities_and_tag
      batch_run_kind: detect_entities_and_tag
      interval_minutes: 120
      max_runtime_minutes: 30
      enabled: true
    - name: tag_images_from_concepts
      script: tag_images_from_concepts
      batch_run_kind: tag_images_from_concepts
      interval_minutes: 120
      max_runtime_minutes: 30
      enabled: true
    - name: build_bow
      script: build_bow
      batch_run_kind: build_bow
      interval_minutes: 120
      max_runtime_minutes: 30
      enabled: true
```

`ingest_auto_prep` gets a short interval (15 min) since it's cheap when the inbox is empty (each
step's own early-exit/no-op path) and the point is to drain newly-dropped files promptly. The
downstream enrichment jobs get longer intervals (60-120 min) since they scan the active corpus for
unprocessed rows each time — still incremental, but with more per-tick overhead (model loading for
tagging/embedding) than a quick "anything pending?" check. `max_runtime_minutes: 30` across the
board is a generous ceiling for orphan detection, not an expected runtime — actual runs should
finish in well under that on the current corpus sizes.

Applied to all three environments via the common `environments/settings.yaml` file (no
per-environment override), matching how `trends_batch` is already configured — each environment's
backend runs its own independent scheduler instance against its own database, so this is 3
separate schedules, not shared state.

### Rejected alternative: full auto-advance driver

Considered: a stage-aware job that also detects when Tier A is fully resolved (no unreviewed
candidates left — `IngestionRepository.get_tier_candidate_rows`/`get_blocked_pending_ids` already
have the query building blocks) and then runs Tier B automatically, and similarly runs `promote`
once Tier B is resolved — fully hands-off. Rejected for this iteration: it needs new "is this tier
fully reviewed" polling logic and a materially different loop shape than the existing per-script
interval scheduler (state-machine-driven rather than fixed-interval), which is a larger and
riskier change for a first rollout. The chosen design (auto-drain the inbox into the Tier A review
queue, leave Tier B/promote manual) covers the highest-value part — an operator no longer needs to
manually run 5 commands just to get new files into the review queue — while keeping every
promotion decision explicitly human-gated. Revisit as a follow-up if manual Tier B/promote proves
to be the actual bottleneck in practice.

### Known limitation (inherited, not introduced)

If new files land in the inbox after an active run has already progressed past `tier_a_review`
(i.e. into `tier_b_review`), `should_advance_stage` only guards the `set_stage` call in
`ingest_find_duplicates.py` — not the candidate-pair search itself (`find_batch_duplicates`). So
the driver's Tier A step still runs its probe for those new images on every tick and still writes
fresh candidate pairs into `tmp_duplicates`; the stage just doesn't rewind back to
`tier_a_review`. The actual gap is narrower: the `/ingestion` frontend's queue selection is driven
entirely by the run's `stage` field (via `tierForStage()`), so those newly-found Tier A candidates
exist in the database but never surface in the review UI until either the current run completes
and a fresh one starts, or an operator manually intervenes. This is the same gap the manual
workflow already has today (the runbook's "Concurrency" section already documents needing to
manually re-run both tiers after a mid-review re-join) — this spec doesn't introduce it, just
inherits it into the automated path.

## Testing

- `batch/tests/test_ingest_auto_prep.py`: `_run_prep_chain` calls each of the 5 steps in order
  (mocked), and that a `RuntimeError` from steps 2-5 is swallowed as a no-op tick while a
  `RuntimeError` from step 1 (if `PATH_INGESTION_SOURCE` is misconfigured) propagates and fails
  the run.
- `batch/tests/test_ingest_find_duplicates.py` (new or extended): `should_advance_stage` unit
  tests for every `(current_stage, target_stage)` pair across `_STAGE_ORDER`, including the
  `None`/unrecognized-current-stage case.
- For each refactored downstream script: existing test coverage (`batch/tests/` /
  `tests/integration/` as applicable per script) continues to pass unchanged; add a small test
  confirming `main(run_id=<uuid>)` and `main(trigger=...)` both route through the correct
  `run_tracking` helper, matching the existing `trends_batch`/`move_flagged` test pattern if one
  exists, or establishing it if not.
- `Backend/tests/test_scheduler.py`: no new tests needed — `_load_job_configs` is already
  generically tested against arbitrary `scheduler.jobs` entries; the new config entries need no
  scheduler-code changes to be picked up.
- Manual verification (mirrors the original scheduler spec's rollout step): start a backend with
  `ingest_auto_prep`'s `interval_minutes` temporarily set to `1`, drop a test image into the
  configured inbox, confirm within ~1 minute it reaches `pending` status with embeddings/OCR and
  appears in the Tier A review queue at `/ingestion` with no manual command run. Separately,
  confirm a `kind="ingestion_auto_prep"` `BatchRun` row appears on schedule and does **not**
  interfere with a concurrently open `kind="ingestion"` review run (create one manually first,
  leave it mid-Tier-B-review, confirm ticks don't touch its `stage`).

## Rollout

1. Fix `ingest_find_duplicates.py`'s stage-ordering guard (section 2) — independently valuable,
   land first.
2. Refactor the 7 downstream scripts to the self-tracking contract (section 3), one at a time,
   each its own commit; add `batch_registry.yaml` entries as each lands.
3. Add `batch/ingest_auto_prep.py` (section 1) plus its `batch_registry.yaml` entry.
4. Add all 8 `scheduler.jobs` entries to `environments/settings.yaml` (section 4).
5. Manual verification per the Testing section above, on one environment first (metal), before
   relying on it across all three.
