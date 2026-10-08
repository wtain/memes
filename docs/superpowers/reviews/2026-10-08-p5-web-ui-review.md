# P5 web UI (searcher experience): review report

Spec: `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-08-p5-web-ui-design.md` (status done; its addendum holds the live check, the deviations and the handoff notes).
Plan: `docs/superpowers/plans/2026-10-08-p5-web-ui.md`. Branch: `p5-web-ui` (forked from the plan commit `7152d90`).

## Review process

Subagent-driven: one implementer per task, a task review (spec compliance and code quality) after each, scoped re-reviews of fix rounds, one whole-branch review on the most capable model, one final fix wave with its own scoped re-review and a small focus follow-up, then the controller's live check in a real browser. After an implementer's tsc claim turned out wrong (Task 2), the controller re-ran `tsc -b`, `eslint` and `vitest` after every task.

## Acceptance criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Taxonomy endpoint with schema, three regenerated type trees, `backend_api.md`, tests; `/reply` route and nav entry | Met. |
| 2 | A pasted message (EN, RU, ES) returns ranked results with the reading panel, explanations, duplicate badges and facets; blank input does nothing; 600-character limit with a counter; Show more without repeats or skips | Met. Live: EN, RU, ES each 20 results; Show more gave 40 unique cards. |
| 3 | Chip, situation and filter edits re-run retrieval only (the request carries the profile), debounced, reset to the original reading, an emptied axis is excluded | Met. Live: `parseMs: 0` for a chip removal and for a facet filter; the server accepted the edited profile. |
| 4 | Degraded, unavailable, zero-result and error states; with Ollama unreachable a search still returns results and shows the limited-understanding notice; no message text in errors or logs | Met. Degraded verified live; unavailable and zero-result by tests. |
| 5 | Accessibility and responsive requirements | Mostly met: tests assert roles, labels, focus targets, live regions; screen reader and the narrow layout were not verified live. |
| 6 | `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`, backend roots pass; generated types have no diff | Met on the final commit: 443 frontend tests, `vite build`, Backend root 394. |
| 7 | Addendum, tracker, `CLAUDE.md`, review report | Met (this report). |

## What was fixed

- Per task: Kotlin generator emitting `List<Any>` (a P4 latent Android build break, fixed at the generator); `useTaxonomy` never loading under `StrictMode` (a real dev-server bug, found in the first scoped re-review and confirmed by tests that fail on the old code); "Show more" racing an in-flight refine; the picker reopening on re-click; blank Ctrl+Enter; unclamped explanation scores; results vanishing after a failed refine and "Try again" discarding the user's edits.
- Whole-branch review (four Important findings, all fixed): focus after Show more, a missing endpoint reading as an error, the results area unmounting from the zero-results state, and Show more mixing two rankings after the server cache expired. Also: busy feedback, focus after self-removing controls, an always-mounted live region, count wording, vocabulary retry, a 422 retry loop, the paste hint, and three focus gaps found by the re-review.

## What was not fixed (acceptable deviations, explained)

- Existing `MemeCard` image is not keyboard-focusable (existing component, outside this feature); focus after Show more lands on the first button in the card.
- Two polite live regions can announce together on a degraded finish; tolerable.
- The nav label is "Reply" (recorded as a deviation).
- Deferred minor findings recorded in the ledger (redundant `instanceof` branch in `isAbortError`, a `TypeError` in the Kotlin generator for list-typed `items.type`, mixed line endings in two new files).

## What was intentionally ignored

- Gradle compile of the regenerated `Models.kt`: the wrapper cannot download its distribution in this environment (certificate error); the change is verified by regeneration only.
- No UI for tag filters, no language selector, no rating or curator tools (separate specs).

## Operational notes

- The running `metal` and `general` backends need a restart to expose `/api/meme-from-text/taxonomy` and the search endpoint; until then `/reply` shows "Message search isn't available in this environment".
- The live check used a minimal backend on free ports with the read-only role and no scheduler; nothing was written to any database.
