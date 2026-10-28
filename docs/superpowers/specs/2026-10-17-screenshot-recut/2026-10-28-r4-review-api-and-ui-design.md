# R4: Review API and UI - design

Status: draft
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md (project R4)
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
Follows: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-26-r3c-refresh-stale-design.md

R3a leaves each cut as a `proposed` `image_cuts` row with a `cut_proposed` image. R4 is the read side of the review: two endpoints
that list the proposals and describe one, and an admin page that shows the source with the proposed box drawn on it next to the
cut. Approve and reject (FR-9, FR-10) are R5; the page shows their buttons disabled so R5 only enables them and wires the calls.
Requirement covered: FR-8.

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | Scope is the read side only | The state transitions, flagging and events are one unit of work with their own risks (R5); the page is useful for judging detection quality before they exist |
| 2 | One proposal at a time, as a queue | It matches the decide-then-next flow R5 adds; a thumbnail strip shows the position |
| 3 | The queue holds `proposed` rows only, ordered by `confidence DESC, id ASC` | `detected` rows have no cut file yet and `failed` rows have no cut. Highest confidence first lets a reviewer clear the likely approvals quickly |
| 4 | Page at `/admin/recut`, open proposal in `?cut=<id>` | Beside `/admin/batches` and `/admin/profiles`; a reload or a link lands on the same before/after |
| 5 | Box drawn in the browser, no image dimensions in the API | The overlay is scaled from the loaded source image's natural size, so a NULL `width`/`height` cannot break it, and R6 reuses the overlay |
| 6 | No derived-data freshness flag | Stale OCR or descriptions do not affect judging the crop; when to refresh is R7 |
| 7 | No migration, no change to `Storage/models.py` | The columns R4 reads already exist (R1) |

## Backend

New `Backend/app/api/recut.py` (registered in `Backend/app/main.py` with the `/api` prefix) and
`Backend/app/repositories/recut_repository.py`. No service layer: both endpoints are read-only pass-throughs. The routers return the
schema-generated models directly (no hand-written response classes, see the gotcha in CLAUDE.md). Access control is whatever the
existing curator routers use.

### GET /api/recut/proposals?offset=0&limit=50

`image_cuts` rows with `state = 'proposed'` and non-null `source_image_id` and `cut_image_id`, ordered `confidence DESC, id ASC`.
`limit` 1 to 200 (default 50), `offset` >= 0; anything else is a 422. Response `{ items, total }`, `total` counting every matching
row. Item: `id`, `sourceImageId`, `cutImageId`, `confidence`, `box { x, y, w, h }` (source pixels, from `crop_x/y/w/h`).

### GET /api/recut/proposals/{id}

The item fields plus `state`, `detector`, `detectorVersion`, `detectRunId`, `createdAt`, `previousId` and `nextId`. The neighbours
are the adjacent rows in the same ordering among `proposed` rows (keyset on `(confidence, id)`), so the page steps without a second
call; both are null at the ends. The detail answers a row in any state that still has both images (a link stays valid after R5
decides it); an unknown id, or a row missing either image, is a 404. For a row that is not `proposed` the neighbours are those of
its position in the `proposed` queue.

### Images

The page loads both images from the existing `GET /api/images/{id}`. `ImageRepository.get_filename` has no status filter, so
`cut_proposed` images already serve; R4 changes nothing there. A missing file is already a 404.

## Frontend

- `src/pages/RecutReviewPage.tsx`, routed at `/admin/recut` in `src/app/router.tsx`, with a link in the admin navigation
  (`AppLayout.tsx`, next to Profiles).
- `src/recut/useRecutQueue.ts` (loads the list page and the open proposal's detail) and `src/recut/boxOverlay.ts` (pure: box and
  natural size to percentage CSS).
- `src/components/recut/`: `BeforeAfter`, `BoxOverlay`, `QueueStrip`, `ProposalInfo`.
- Types come from the generated schemas; none are hand-written.

Behaviour:

- The URL carries `?cut=<id>`. Without it, or with an id that is not in the queue and has no detail, the page opens the first
  proposal and rewrites the URL (replace, not push). An empty queue shows "No proposals waiting for review".
- Source on the left with the box as an absolutely positioned rectangle in percentages of the image (correct at any display size),
  cut on the right, the thumbnail strip below with the open one highlighted, "n of total" and previous/next above. Arrow keys step.
- Below the images: confidence, the box as `x, y, w x h`, the share of the source area removed, detector and version.
- Approve and Reject are visible and disabled, with a hint that they arrive with the approve/reject step.
- The strip loads 50 items and fetches the next page when the open proposal nears the end of what is loaded.
- An image that fails to load shows a "file missing" placeholder in its pane; a failed request shows the error pattern of the other
  admin pages.
- StrictMode and the React Compiler rules: effects fetch with an abort guard and survive a double mount; refs are touched only in
  handlers and effects.

## Shared schemas and docs

- `shared/schemas/`: `recut-proposal`, `recut-proposal-page` and `recut-proposal-detail` (`.schema.json`). All three trees are
  regenerated (TypeScript with `Frontend/generate-types.sh`, Kotlin with `AndroidClient/scripts/generate_dtos.py`, Python per
  `documents/generation.md`); the CI diff check on the generated TypeScript must be clean.
- `backend_api.md`: a "Recut review" section with both endpoints (parameters, shapes, ordering, 404 rules, a note that the decision
  endpoints arrive with R5).
- `docs/image-status.md`: a line saying `cut_proposed` images are served by `GET /api/images/{id}` and the review page is where they
  become visible.
- This spec: the tracker row R4 moves to `draft` and its Delivers text becomes the read side; the R5 row gains the approve and
  reject endpoints and enabling the buttons. The R3c spec gets a `Follow-ups` link here.
- No `CLAUDE.md` batch-list change: R4 adds no batch job.

## Testing

- Integration (`tests/integration/`, `ocrdb_test`): list ordering and tie-break by id; only `proposed` rows with both images appear;
  `total` and paging; detail neighbours at the start, middle and end; detail of a decided row; 404 for an unknown id and for a row
  without a cut image. Seed rows are committed first and ids captured before commit (see the savepoint gotcha in the R3c notes).
- Router tests (`Backend/tests/`, mocked repository): parameter bounds and the `limit` cap, 404, response shape against the schema.
- Smoke: `/api/diagnostics/health` and `/api/images?limit=1` still answer.
- Frontend (vitest): `boxOverlay` (percentages, a box filling the whole image); `useRecutQueue` (first load, stale `?cut` falling back
  to the first proposal, empty queue, next page); page (empty state, stepping with buttons and keys, disabled action buttons,
  broken-image placeholder). Then `tsc -b`, `eslint src/` with 0 warnings and the generated-types diff.

## Out of scope

Approve and reject and every state change (R5); box adjustment (R6); viewing decided, failed or detected rows in the page and
filtering by detect run (later, if wanted); derived-data freshness in the page (R7); any live rollout, since R4 changes no data. The
page can be checked against real proposals only after R3a has run on a live database, which waits on the migration go-ahead.

## Delivery

Worktree `recut-r4` from local `main`; no migration and no change to `Storage/models.py`, which keeps clear of the parallel work.
Inline execution with a ledger and a final fresh-Opus review, as for R3.
