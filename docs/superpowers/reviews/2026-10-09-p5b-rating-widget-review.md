# P5b rating widget: review report

Spec: `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-09-p5b-rating-widget-design.md` (status done; its addendum holds the live check, the deviations, the migration commands and the handoff).
Plan: `docs/superpowers/plans/2026-10-09-p5b-rating-widget.md`. Branch: `p5b-ratings` (forked from the plan commit `bec184e`).

## Review process

Subagent-driven: one implementer per task; a task review after Tasks 1, 2 and 4 (Tasks 3, 5 and 6 are mechanical or thin and were verified by the controller, then covered by the whole-branch review); fix rounds with the controller re-running every gate (tsc, eslint, vitest, vite build, every pytest root and the whole integration root); one whole-branch review on the most capable model; one fix wave (backend and frontend halves); a controller live check on the seeded test database; one more fix found by the live check.

## Acceptance criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Table, migration, repository, four endpoints, `meta.queryHash`, schemas, `backend_api.md` | Met. |
| 2 | Rating bar with optimistic update and rollback, grades restored for a message, forget-text action | Met. Live: rate, re-rate, clear, reload, forget, failed save. |
| 3 | Text stored only with a rating, clearable, never logged or in an error body | Met. Traced through router, handler, service, rollback path and frontend; verified live (422 bodies carry no input). |
| 4 | `export-ratings` produces import-ready files with no silent overwrite | Met after the fix wave (`--force` now removes the files the export owns). Verified live: `import-judgments` accepted a produced file into a copy. |
| 5 | Accessibility basics | Mostly met: roles, `aria-pressed`, always-mounted status line, focus handling and touch targets are tested; screen reader and narrow layout not verified live. |
| 6 | Addendum, live check, migration notes, tracker, `CLAUDE.md`, this report | Met. |

## What was fixed

- Per task: the migration drift guard was weak (now compares defaults, check expressions, index columns, primary key, comment, with negative controls); FastAPI's default 422 echoed the rejected input, which contains the user's text (now stripped for all `/api/meme-from-text` routes); NUL characters gave a 500 (now a 422); an untyped `RatingContext` (explicit interface); a "Text forgotten" live region that was inserted with its text and lost focus; a stale request writing into the next message's confirmed grades; a late load overwriting a newer confirmed grade.
- Whole-branch review (one Important, several Minor, all fixed): `--force` left stale `judgments-<lang>.json` behind, which a later import could re-apply over newer human judgments; a rating's `reading` came from the current edit state instead of the request that produced the list; the forgotten-text notice never reset; missing tests (rank after Show more, failed load, re-search); default export directory relative to the working directory; table comment missing; trust model undocumented.
- Live check: the Forget button pushed the cards down on the first rating (slot now reserved).

## What was not fixed (acceptable deviations, explained)

- A race between the image-active check and the upsert would surface as a 500; rare and harmless for a single-owner tool.
- The saved announcement uses the current grade, and there is one event slot for all cards.
- `get_rating_service` is an async generator without cleanup (harmless).
- Ratings endpoints are unauthenticated like the rest of the API (documented).

## What was intentionally ignored

- Gradle compile of the regenerated `Models.kt` (no Kotlin `queryHash`: the generator types `meta` as `JsonObject`).
- No UI to browse or edit past ratings, no rating-influenced ranking, no auto-merge into the gold set (separate specs).

## Operational notes

- Apply the migration on metal, general and it (`alembic upgrade head` from `Storage/` with each environment's `.env` loaded) and restart the backends; until then rating shows an inline error.
- The live check used `ocrdb_test` only; the seeded rows and the dev servers were removed afterwards.
