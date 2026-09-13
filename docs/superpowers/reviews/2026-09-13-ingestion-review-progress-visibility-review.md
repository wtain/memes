# Ingestion Review — Progress Visibility — Review

Spec: `docs/superpowers/specs/2026-09-13-ingestion-review-progress-visibility-design.md`
Plan: `docs/superpowers/plans/2026-09-13-ingestion-review-progress-visibility.md`
Branch: `ingestion-review-progress-visibility` (off `main` @ `be3a318`)
Reviewed range: `be3a318..7cc6fc6` (9 commits — 4 tasks + 1 final-review fix wave).
Method: subagent-driven development — per-task spec+quality review after each of 4 tasks, then a
whole-branch review on the most capable model, one fix wave, one scoped re-review.

## Outcome

All 4 planned tasks implemented and merged onto the branch. Backend suite 309 passed; full
`tests/integration/` sweep 314 passed; frontend `tsc -b` clean, `eslint src/` 0 warnings,
`vitest run` 221/221. No generated-type drift (TS/Kotlin/Python DTO trees all re-verified). No DB
migration required — this is entirely new, read-only count queries plus two nullable response
fields.

`GET /api/ingestion/run`'s `RunStatusResponse` gains two new nullable fields
(`tier_remaining`, `blocked_total`); backward compatibility for existing consumers (Android)
confirmed safe — `NetworkModule.kt` sets `ignoreUnknownKeys = true` and no Android screen consumes
this DTO.

## What was fixed during the task loop

- **Task 1 (pre-dispatch ruling):** the plan's "already reviewed" test tried to insert a second
  `tmp_duplicates` row for the same `(image_id1, image_id2)` pair — violates the real
  `uq_tmp_duplicates_pair` unique constraint (only one row can ever exist per unordered pair).
  Fixed by using a separate pair for that case instead of reusing the first.
- **Task 4 (BLOCKED, round 1):** two plan-authored test fixtures were genuinely broken — a
  `stage: 'ocr_prepass'` paired with the wrong branch's expected message (the real code renders a
  different message for that specific stage), and a fixture using identical
  `tier_remaining`/`blocked_total` values that made a `findByText` regex match ambiguously. Both
  fixed in the plan document; the implementer's Steps 3-5 implementation (already complete and
  correct) was untouched by either fix.

## What the whole-branch review found, and what was done

**Important (all fixed in the final fix wave, commit `7cc6fc6`, re-review confirmed ADDRESSED
with no new breakage):**

1. Nothing pinned the `tier_a`/`tier_b` branch of `count_unreviewed_subjects`'s `reviewed_col`
   ternary — no test ever set `tier_a_reviewed_at` on a row. A silently swapped ternary would
   have passed the whole suite. → new integration test proves the tier_a query reads only its own
   column.
2. The service→repository call contract was asserted on tier only, never on the distance band —
   a transposed `(low, high)` would have gone undetected. → the three `TestGetRunStatus` mocked
   tests now assert the full `call_args` tuple against the real `_tier_band()` output, plus a new
   real-DB `get_run_status` integration test (no mocks) covering both tiers end-to-end.
4. `progressBaselineRef` was captured once ever, never re-scoped when the active review tier
   changed — a Tier A → Tier B auto-advance within one session could diff a Tier A count against
   a Tier B count in the "since you opened this page" delta. → the baseline now carries its tier
   and recaptures on tier change; a new test proves the old bug (asserts `-0`, not the stale
   cross-tier `-30`).
7/8. The partial-submit status refetch could call `setStatus(null)` on a transient 404, blanking
   the page and discarding unsubmitted decisions; `StatusBanner` used strict `!== null` while the
   rest of the file used loose `!= null`. → both guarded/made consistent.

**Deferred as follow-up work (not blocking this merge):**

- **#3 (performance):** `blocked_total` materializes two full unreviewed-id sets per
  `get_run_status` call (on every page load and every partial submit), with no `work_mem` tuning
  applied — unlike the structurally identical `list_tier_b_review_page` query, which needed it.
  Measured live against `general` (read-only role): `unreviewed_subject_ids(tier_b)` alone took
  ~500ms via a Parallel Seq Scan on `tmp_duplicates`, not using the existing
  `idx_tmp_duplicates_tier_b_unreviewed_distance` partial index. Real, but a latency concern, not
  a correctness one — ruled to open a dedicated follow-up spec for this query's tuning, matching
  the same code area's own established pattern (the "Query A candidate bound" / "Query A partial
  index" work was itself a post-ship follow-up, not a blocking condition on the queue it tunes).
- **#5:** `baseline.blockedTotal` is captured and threaded through props but never rendered — a
  dead field relative to the spec's "and same for blocked_total" line.
- **#6:** two submits in flight concurrently (the UI only disables the card being submitted, not
  the whole page) can misattribute the delta message, since both read `prevRemaining` from the
  same render closure.
- **#9:** the new field descriptions (schema, `all.d.ts`, generated Python DTO, `backend_api.md`)
  say "null when the stage has no active tier (hash_dedup, ocr_prepass)" — inherited from the
  existing `stage` description, but `ocr_prepass` is no longer written by anything and
  `format_validation` (a real non-tier stage) is missing. Cosmetic, replicated in 4 places.
- **#10:** minor nits — a redundant ternary that could be `TIER_LABEL[tier]`, the baseline span
  rendering `(-0 since you opened this page)` on first paint before anything has happened, and
  per-method `import uuid`/`SimpleNamespace` in two new service tests that duplicate module-level
  imports already present in the file (harmless, matches the file's existing style).

#5, #6, #9, and #10 are recommended as one natural follow-up commit alongside the parked minor
from Task 4's own review (no test asserts the baseline-delta suffix text/arithmetic).

## Strengths independently confirmed by the final review

- Predicate parity between the new count queries and `list_tier_b_review_page`'s subject-set CTE
  is byte-for-byte identical, not a parallel reimplementation that could drift.
- `blocked_total` is provably the exact set `ingest_promote`'s `get_promotable_ids` would exclude
  — confirmed `get_blocked_pending_ids` (the method the spec explicitly says NOT to reuse here,
  since it can overcount `cross_corpus` matches) still has exactly one caller, and nothing new
  routes through it.
- `_tier_for_stage` (backend) and `tierForStage` (frontend) verified to agree over the *entire*
  real stage domain — every stage any script actually writes (`hash_dedup`, `format_validation`,
  `tier_a_review`, `tier_b_review`, `promoted`), not just the stages exercised by tests.
- Both mid-execution plan-bug rulings (Task 1, Task 4) hold up under the final review and left no
  stale test code behind.

## Verification

All test evidence in this report was independently re-run directly by the controller, not taken
on trust from subagent self-reports: frontend `vitest run` (221/221, 24 files), `tsc -b`, `eslint
src/`, `cd Backend && pytest -q` (309/309), and the full `tests/integration/` sweep with
`DATABASE_URL` set explicitly (314/314) — both before and after the final-review fix wave.
