# Admin Batch Controller Web UI — Review

Spec: `docs/superpowers/specs/2026-07-31-admin-web-ui-design.md`
Plan: `docs/superpowers/plans/2026-07-31-admin-web-ui.md`

Implemented via subagent-driven-development: four tasks, each with its own implementer + task
reviewer, followed by a final whole-branch review and one fix round.

## Per-task reviews

All four tasks passed their scoped review clean (spec compliant, no Critical/Important findings):

1. **Shared JSON schemas** (`shared/schemas/run{trigger,status,list}response.schema.json` +
   regenerated TS/Kotlin types) — approved, minor note only (Android regen incidentally backfilled
   pre-existing drift from unrelated Ingestion types).
2. **`MemesApi` methods** (`triggerBatchRun`, `listBatchRuns` on `HttpMemesApi`) — approved, zero
   findings.
3. **`AdminBatchesPage` component** (trigger toolbar with two-click confirm, paginated
   auto-polling run-history table) + full test suite — approved; two documented deviations from
   the brief's verbatim code (a duplicate-text test-query fix, an ESLint-driven `useEffect`
   ordering change) were independently verified as legitimate rather than taken on the
   implementer's word.
4. **Routing/nav wiring** — approved, zero findings.

## Final whole-branch review

Caught one issue no task-scoped review could see: **Task 1's mandated Android DTO regeneration
(`AndroidClient/scripts/generate_dtos.py`) emitted uncompilable `Any`-typed properties** for any
JSON Schema field with a nullable union type (`{"type": ["string", "null"]}`) — affecting the new
`RunStatusResponse.completed_at`/`.error` and three pre-existing Ingestion classes whose drift had
been latent until this branch's regen made it live, compiled source. Since `shared/schemas/**`
changed, Android CI would have run on this branch and failed. Rated Critical.

Also flagged (Important): the trigger button gave no feedback during the request round-trip, and a
`409`-already-running error never auto-cleared — a double-fire from operator impatience could leave
a permanent stale error message next to a batch that was actually running fine.

## Fix round (1 of 1, clean)

- **Android generator fix:** `generate_dtos.py` now unwraps nullable-union types into proper
  Kotlin-nullable fields instead of falling through to `Any`; also fixed the same bug class for
  `object`-typed nullable fields (→ `JsonObject?`). Regenerated `Models.kt` verified to compile via
  a real `.\gradlew :app:testDebugUnitTest --no-daemon` run (BUILD SUCCESSFUL).
- **Trigger UX fix:** added `triggeringBatch` in-flight state (button disables, shows
  "Triggering…" for the full round-trip) and auto-clearing of stale `triggerErrors` whenever
  `load()` sees the batch is no longer in that error state — two new tests cover both.
- Full frontend gate (`tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`) clean throughout: 132
  tests across 18 files, 0 lint warnings.

Scoped re-review confirmed both findings ADDRESSED with no new Critical/Important breakage.

## Parked (non-blocking)

- **Minor, introduced by the fix itself:** `triggeringBatch` is a single scalar, not per-batch —
  triggering batch B while batch A's request is still in flight re-enables A's button mid-flight,
  allowing a duplicate request on A. Narrow race; the backend's one-active-per-kind unique index
  already prevents any actual double-run regardless, so the worst case is a harmless duplicate
  request. Ruling: real but not load-bearing, nothing downstream depends on it — deferred.
- No test for the page-level load-error path (spec's own test list didn't include one; the
  behavior itself is implemented at `AdminBatchesPage.tsx`).
- `page` isn't clamped when `total` shrinks — self-correcting via the Prev button.
- Polling uses `setInterval` rather than a self-scheduling `setTimeout` chain — low risk given the
  endpoint is two cheap queries.
- Run history timestamps render as raw ISO strings — consistent with the rest of the app (no
  existing date-formatting convention to follow), just not the prettiest.

## Outcome

**Ready to merge.** All Critical/Important findings from both the per-task and whole-branch reviews
are fixed and re-verified; the one residual finding is a narrow, non-load-bearing UI race
explicitly parked with a ruling.
