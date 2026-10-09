# P5c curator tools: review report

Spec: `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-09-p5c-curator-tools-design.md` (status done; its addendum holds the live check, the deviations, the rollout notes and the handoff).
Plan: `docs/superpowers/plans/2026-10-09-p5c-curator-tools.md`. Branch: `p5c-curator` (forked from the plan commit `f6f75d3`).

## Review process

Subagent-driven: one implementer per task; a task review after Tasks 1, 2 and 5 (Tasks 3, 4 and 6 were verified by the controller, then covered by the whole-branch review); fix rounds with the controller re-running every gate (tsc, eslint, vitest, vite build, every pytest root and the whole integration root) after each task; one whole-branch review on the most capable model; one fix wave in two halves (backend, then frontend); a controller live check on the seeded test database with the real embedding model and the browser. Throughout, a real GPU batch (`build_meme_profiles` on `metal`) ran against the real database from the main checkout; no task or review touched it.

## Acceptance criteria

| # | Criterion | Result |
|---|---|---|
| 1 | A curator edits a profile on the meme page; stored as human-verified; used by search at once | Met. Live: save shows `Saved`, values `human, 1.00`; the inline vector's model name and digest equal the batch job's. A repeated identical search can be served from the 120-second result cache (documented). |
| 2 | The batch never overwrites a human-verified profile (also during a run); review only after a taxonomy bump | Met after the whole-branch review: both writers now lock the row (found by the review, covered by two real-session tests); `select_work` and `coverage()` implement the taxonomy-only rule. |
| 3 | Confirm and Reset as specified, including the refusals | Met. Confirm re-validates the whole stored profile (409 without a profile, 422 for a removed key, an over-cap axis or a long situation); reset clears the profile and this pipeline's failure row; the UI marks removed keys and explains refusals. |
| 4 | Coverage page: consistent counts, last runs, as-of numbers, paged lists linking to the meme | Met after the review fixed the as-of numbers (they read the wrong place in the run stats): every category total equals its count and its list, both jobs' runs render, paging has no duplicates. |
| 5 | No migration; schemas, three type trees, `backend_api.md`, accessibility basics, gates | Met. Screen reader and narrow layout not verified live. |
| 6 | Addendum, live check, tracker, `CLAUDE.md`, this report | Met. |

## What was fixed

- Per task: the human-verified staleness rule broke one older integration test that seeded a human profile stale only by prompt version (seed changed, ruled correct under decision 2); a tiny request/response `maxItems` generator problem (removed from the schema, the service enforces the limit); edits typed during an in-flight save were discarded and a save in flight on one meme could leave the panel permanently busy after switching memes (inputs blocked while busy, busy state keyed by image, panel keyed per meme); no retry for a failed profile load.
- Whole-branch review (four Important findings, all fixed): a curator save and a batch commit window could mix model values into a human-verified profile, give a 500 or hang (row lock, `lock_timeout`); the as-of numbers always showed "-" (the job stores them under `stats.coverage`, and the plan's fixture had the same wrong shape), and the embeddings run was never rendered; the panel did not show each value's source and confidence; a taxonomy bump gave no way to find the removed key and the 422 texts were generic. Also: a failed image's record is cleared by a curator save or confirm, Confirm re-validates the profile, a retry for a failed first page of a category, and several test pins.
- Live check: nothing new found beyond the review; all 21 API checks and the browser flows passed.

## What was not fixed (acceptable deviations, explained)

- A repeated identical search can be served from the 120-second result cache after an edit; the vector status compares the model name only; a failed image without a profile has no Reset button (saving a profile by hand clears the failure); failure alerts are mounted on demand; an unknown-key 422 names the client's own key.
- The `globalThis.Request` wrapper in `ProfileEditor.test.tsx` (works around jsdom and Node disagreeing about abort signals) is file-scoped and restored.

## What was intentionally ignored

- Authentication and per-curator audit (documented trust model), bulk edits, re-running vision extraction for one image.

## Operational notes

- No migration. Restart the backends to expose the new routes.
- `metal` was profiled before this branch merged (18,052 profiles for 18,088 active images, 18,041 vectors); `general` still needs its backlog run (about 6.7 hours).
- The live check used `ocrdb_test` only; the seeded rows, servers and browser tab were removed afterwards.
