# R7: Operations and evaluation - design

Status: done
Plan: docs/superpowers/plans/2026-10-29-r7-operations-and-evaluation.md
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md (project R7)
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
Follows: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-29-r5b-duplicate-exclusion-and-batch-audit-design.md

R2 to R5 built the recut path as separate pieces: detect, cut and copy, derive, refresh, review, decide. R7 makes it operable and
records the evidence and the decisions the epic left open: a driver job for the middle of the path, a reproducible measurement of
detection precision on the labelled sample, the live approve rate by confidence band, the runbook, and the scheduling decision.
Requirements covered: FR-16, NFR-3.

## What already exists

- Registry and inventory entries for `detect_recut_crops`, `create_recut_cuts` and `refresh_recut_cuts` (R2, R3a, R3c).
- The recut metrics panel on `/admin/batches` (M1): proposed, approved, rejected and the approve rate per detection run.
- The labelled sample, 197 owner judgments in `evaluation/gold/recut/` (R2 spike, two rounds).
- `refresh_recut_cuts` already runs the text-heavy classifier, the OCR-text embeddings and the nine group 3 steps.

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | A `recut_pipeline` driver runs `create_recut_cuts`, the cut embeddings and `refresh_recut_cuts`; detection stays a separate manual step | The operator otherwise runs these by hand, one of them shell-only. Detection decides how large the review queue gets, so a click must not add thousands of proposals |
| 2 | Precision is measured by a pure report over the gold files (`python -m evaluation recut-report`), with no database or images | It is reproducible, needs no live access, and a later labelled round only adds files |
| 3 | Recall is reported as not measurable | The sample holds 11 judged no-proposal items, none needing a cut, and 9 skipped: too few for a number |
| 4 | Live precision is the approve rate by the same confidence bands, from `image_cuts`, computed at read time | Gold and live numbers line up band for band (NFR-5: rates never stored) |
| 5 | No recut job is scheduled now | Review, not detection, is the bottleneck; scheduling is a settings change once the first pass is reviewed (below) |
| 6 | No change to the detector, the OCR clipping guard (stays off) or the statistics snapshot | Nothing in the evidence calls for it; the guard waits for live evidence of clipped text |
| 7 | R7 runs nothing against a live database | The rollout is the owner's decision, step by step (below) |

## 1. The `recut_pipeline` driver

New `batch/recut_pipeline.py`, same model as `description_pipeline` and `refresh_recut_cuts`: it reuses `Step` and `run_steps`,
calls each child in process with `run_id=None` so it self-tracks under its own kind, a child that is already running is skipped
(`skipped_busy`), and the driver's own stats carry the per-step outcome. The driver raises (and so fails) when any step failed.

| Step | Call | Notes |
|---|---|---|
| `cuts` | `create_recut_cuts.main(trigger=t, limit=settings.get("recut.max_cuts_per_run"))` | new optional setting; unset means unlimited, like the admin trigger today |
| `cut_embeddings` | `build_image_embeddings.main(trigger=t, status="cut_proposed", incremental=True)` | the shell-only step, now reachable from the driver |
| `refresh` | `refresh_recut_cuts.main(trigger=t)` | the existing 11-step driver, as one step |

The steps have no dependencies on each other: every step selects only what is still waiting, so if `cuts` stops after 20
failures in a row, the embeddings and the refresh still serve the cuts that exist. Resuming after a failure is triggering
again. Registered in `environments/batch_registry.yaml` as `recut_pipeline` (module `batch.recut_pipeline`, kind
`recut_pipeline`), admin-triggerable, manual only. `it` has no OCR, description or profile tooling; the refresh step ends the
same way there as `refresh_recut_cuts` does today. `recut.max_cuts_per_run` is added (commented out, unset) to `settings.yaml`
next to the other `recut.*` keys.

## 2. Measuring precision

### The gold report

New `evaluation/recut_report.py`, wired into `evaluation/__main__.py` as `recut-report`. It reads
`evaluation/gold/recut/spike-round{1,2}-{judgments,key}.json` and prints a table.

- **Which items count.** The proposals of the shipped detector: margin trims. Round 1 contributes the items A or C proposed, banded by
  the confidence A recorded for them: only A's own top-40 items are at 1.0, the C items come from further down A's list, so the
  key carries a `confidence` per A/C entry (added 2026-10-29 from the spike output) and an entry without one is counted in the
  total but in no band ("unbanded"), never guessed; round 2 contributes its margin items (the key carries `band` and
  `confidence`). The 14 inner-rectangle items of round 2 are excluded: the shipped detector does not propose
  them. Skipped items (`s`) and items with no judgment are excluded and counted.
- **Bands.** `[0.6,0.7)`, `[0.7,0.8)`, `[0.8,0.9)`, `[0.9,1.0)`, `1.0`, the same edges as the live table below.
- **Numbers per band and overall.** Graded count; "as is" (grade 2); "as is or slightly off" (grade 1 or 2); wrong (grade 0);
  each rate with a Wilson 95% interval.
- **Recall.** Printed as "not measurable" with the evidence (judged no-proposal items and how many needed a cut).
- The report states which detector the sample describes (the spike's detector A, whose margin part is the shipped
  `margin_trim` v1) and that a different detector version needs a new labelled round.

Expected from the current files (pinned by a test): 124 graded margin proposals, 118 as is, 5 slightly off, 1 wrong; 99.2% as
is or slightly off, 95.2% as is.

### The live table

`GET /api/recut/stats` (in `Backend/app/api/recut.py`, queried by a new `RecutRepository.band_stats`, no migration). One query over
`image_cuts` rows in state `proposed`, `approved` and `rejected` (`detected` and `failed` have no cut to review), grouped by the
band of `confidence`:

```json
{ "bands": [ { "label": "0.6-0.7", "from": 0.6, "to": 0.7, "waiting": 12, "approved": 30, "rejected": 1, "approveRate": 0.968 }, ... ],
  "total": { "waiting": 0, "approved": 0, "rejected": 0, "approveRate": null } }
```

Bands are the five above plus `unscored` (null confidence); `approveRate` is `approved / (approved + rejected)` or null while
nothing is decided. New shared schema `recutstats.schema.json` (generated TypeScript, Kotlin and Python types, like R4).
`backend_api.md` documents it.

On the review page a compact "Review so far" table (a `ReviewStats` component, loaded once on open and again after each decision)
sits under the queue strip, with a note that approve rate is stricter than the gold "as is or slightly off" because a slightly-off
box can still be rejected, so the two are not compared one to one. A failed stats request hides the table; it never blocks
reviewing.

## 3. Scheduling decision

No recut job is added to `scheduler.jobs`. Reasons: review is the bottleneck (margin trims are about 27% of the sampled library,
so the first pass is a queue of thousands, NFR-1); and detection is already bounded, since each image is checked once per
detector version (`recut_detect_v1`), so a re-run after the first pass only sees new images. **Revisit** when the first pass has
been reviewed and the queue is empty: scheduling `detect_recut_crops` and then `recut_pipeline` weekly on `general` only is then a
settings change. The decision and its trigger are recorded in `docs/batches/inventory.md` (the `recut_pipeline` and
`detect_recut_crops` rows).

## 4. Runbook and rollout

New `docs/batches/use-cases/screenshot-recut.md`, in the style of the other use-case pages, linked from `docs/batches/README.md`:

- A diagram of the path: `detect_recut_crops --limit` -> `recut_pipeline` (cuts, cut embeddings, refresh) -> review on
  `/admin/recut` -> `move_flagged` -> `unregister_deleted_images` -> `remove_singletons`.
- Which steps are admin-triggerable and which are shell-only, where the decision events, the metrics panel, the stats table and the
  duplicate exclusion live, and what to do after a failed step.
- **First rollout** (text only; R7 runs nothing live, each step needs the owner's explicit go-ahead): the migrations in order
  (R1 `b8d4f1a6c935`, M1 `c5e2a9d7b140`, R3a `d7a3b9e2c451`, checked against the current alembic head), a confirmed backup, one
  environment at a time verified with the read-only role; a first limited `detect_recut_crops --limit 500` on `general`, then
  `recut_pipeline`; a visual check of the box overlay on a tall source image in a real browser; and the R3c refresh on the GPU,
  which has never run for real. The runbook and a read-only preflight are in `docs/batches/use-cases/screenshot-recut-rollout.md` (rollout-prep, 2026-10-31).

## 5. Docs touched

`docs/batches/inventory.md` (new row, the scheduling decision), `CLAUDE.md` (batch entry for `recut_pipeline`), `backend_api.md`
(`GET /api/recut/stats`), `docs/image-status.md` ("Read by" of `cut_proposed` mentions the stats), the epic `README.md` and the
tracker (R7 done, remaining: R6 and the deferred M2), `evaluation/gold/recut/README.md` (the report command).

## 6. Testing

- Driver (`batch/tests/test_recut_pipeline.py`): steps run in order; a failed step does not stop the others and the driver fails;
  a busy child is `skipped_busy`; `recut.max_cuts_per_run` reaches `create_recut_cuts`; the status and `incremental` arguments of
  the embeddings step.
- Registry and inventory: the existing registry and docs guards.
- `recut-report`: a unit test on tiny fixtures (banding, exclusions, Wilson interval, recall "not measurable") and a pinned run on
  the real gold files (124 graded, 118 / 5 / 1).
- Stats: integration tests of `band_stats` (band edges, null confidence, states, no decided rows gives null rate), endpoint
  tests (shape), frontend tests of `ReviewStats` and its place on the review page, including the hidden-on-failure case.
- Verification runs every test root separately, and the type regeneration diff check.

## Not covered (known)

- Recall stays unmeasured until more no-proposal images are judged.
- The gold numbers describe detector A's margin part on `general`; they are not re-derived from the shipped code. A new detector
  version needs a new labelled round (the report says so).
- The pipeline and the stats were verified against the test database only; nothing ran on a live database or a GPU.
- The OCR clipping guard stays off; whether to turn it on waits for live review evidence.
- R6 (box adjustment) and M2 (duplicate-review metrics) are separate projects.
