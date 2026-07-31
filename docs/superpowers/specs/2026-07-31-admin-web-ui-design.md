# Admin Batch Controller Web UI — Design

Status: approved
Originates from: docs/superpowers/specs/2026-07-28-admin-batch-controller-design.md

**Date:** 2026-07-31.

Frontend for the admin batch controller backend (`Backend/app/api/admin.py`, merged to `main`
2026-07-31): a page to trigger `trends_batch`, `move_flagged`, and `unregister_deleted_images` on
demand and see run status/history, so an operator doesn't need SSH/terminal access to the box for
routine maintenance triggers.

---

## Motivation

The backend (trigger/status/list-runs endpoints) exists and is deployed to all three environments,
but there is no UI in front of it — triggering a batch or checking its status currently requires
`curl` or a terminal. This spec adds the frontend page only; no backend changes.

## Scope

**In scope:** one page, `/admin`, with a trigger control per allow-listed batch and a paginated run
history table for all three admin-kind runs.

**Out of scope:**
- Any authentication/authorization — matches the backend spec's deliberate no-auth-yet posture
  (`docs/security/admin-permissions-todo.md`).
- Discovering batch names dynamically — there is no list-available-batches endpoint (the backend
  intentionally keeps the trigger surface a fixed allow-list, not something to enumerate over the
  network). The three names are hardcoded in the page, sourced from
  `environments/batch_registry.yaml`.
- Ingestion, scheduler config, or any admin surface beyond the three batches already covered by
  `Backend/app/api/admin.py`.
- Log viewing — the backend spec deliberately has no fetch-log-by-run_id endpoint; an operator who
  needs a run's output still looks at `logs/{env}/` directly on the box.

## Design

### Shared types

Three new schemas in `shared/schemas/`, registered in `all.schema.json`, mirroring
`Backend/app/api/admin.py`'s three Pydantic response models field-for-field:

- `runtriggerresponse.schema.json` → `RunTriggerResponse` (`run_id`, `status`)
- `runstatusresponse.schema.json` → `RunStatusResponse` (`run_id`, `batch_name`, `trigger`,
  `status`, `created_at`, `completed_at` nullable, `error` nullable)
- `runlistresponse.schema.json` → `RunListResponse` (`items: RunStatusResponse[]`, `total`)

Regenerate via `Frontend/generate-types.sh` (also regenerates unused Android DTOs — accepted, per
the project's existing type-source convention) and `AndroidClient/scripts/generate_dtos.py`, then
verify no unexpected diff beyond the new types.

### API client

Add to `MemesApi` (`Frontend/memes-frontend/src/api/MemesApi.ts`) and its `HttpMemesApi`
implementation, following the existing ingestion methods' fetch/error-handling style:

```typescript
triggerBatchRun(batchName: string): Promise<RunTriggerResponse>
listBatchRuns(limit?: number, offset?: number): Promise<RunListResponse>
```

`triggerBatchRun` posts to `/api/admin/batches/{batchName}/run` and must surface `409` (already
running) and `404` (unknown batch) as thrown `Error`s with a message the page can display, not as
silent failures — mirroring `resolveIngestionCluster`'s `if (!res.ok) throw new Error(...)` pattern
but the page differentiates on `res.status` for these two so it can show "<batch> is already
running" instead of a generic message. `listBatchRuns` builds the query string from whichever of
`limit`/`offset` are passed (both optional, matching the backend's own defaults of 50/0).

### Page: `AdminBatchesPage.tsx`

Single file under `src/pages/`, no sub-components (matches the page's small scope) — hooks-based
state, structured like `IngestionReviewPage.tsx`'s `load()`-callback-plus-`useEffect` pattern.

```typescript
const ADMIN_BATCHES = ["trends_batch", "move_flagged", "unregister_deleted_images"] as const
// Source of truth: environments/batch_registry.yaml. No endpoint enumerates these --
// the backend deliberately keeps the trigger surface a fixed allow-list.
```

**State:** `runs: RunStatusResponse[]`, `total: number`, `page: number` (0-based, `offset = page *
PAGE_SIZE`), `loading`, `error`, `pendingConfirm: string | null` (which batch's button is in its
"Confirm?" state), `triggerError: Record<string, string>` (per-batch inline error, keyed by batch
name).

**Layout, top to bottom:**

1. **Batches toolbar** — one row per `ADMIN_BATCHES` entry: name, a status chip showing that
   batch's most recent run (found by filtering `runs` client-side for `batch_name === name`,
   taking the first — the list is already `created_at DESC` — falling back to "no runs yet" if
   none are in the currently-loaded page), and the **Run** button.
   - **Run** button behavior: first click sets `pendingConfirm` to that batch name, re-labels the
     button "Confirm?", and starts a 3-second `setTimeout` that reverts `pendingConfirm` to `null`
     if nothing else happens. A second click on the same button before the timeout fires calls
     `triggerBatchRun`; clicking a *different* batch's Run button while one is pending clears the
     first timeout and moves `pendingConfirm` to the new batch instead. On success, clear
     `pendingConfirm` (and the pending timeout), clear that batch's
     `triggerError`, and call `load()` immediately (also starts polling — see below). On failure,
     clear `pendingConfirm` and set `triggerError[batchName]` to a message based on status (`409`
     → "already running", `404` → "not recognized" as a defensive fallback, anything else → the
     thrown error's message).
   - Inline error (if any) renders under that batch's row, cleared on the row's next trigger
     attempt (success or failure both replace it).
2. **Run History table** — columns: Batch, Trigger, Status (colored badge: blue=running,
   green=completed, red=failed), Created, Completed, Error (truncated to ~40 chars with a `title`
   tooltip for the full text, matching `MemberTile`'s `ocrText` truncation pattern in
   `IngestionReviewPage.tsx`). Below the table, Prev/Next buttons using `total` to disable Next on
   the last page and Prev on the first; `PAGE_SIZE = 20`.

**Data flow:**

- `load()` calls `listBatchRuns(PAGE_SIZE, page * PAGE_SIZE)`, sets `runs`/`total`, clears/sets
  `error`, matching `IngestionReviewPage`'s `load()` shape (a `useCallback` depending on
  `memesApi` and `page`, invoked from a mount `useEffect` and whenever `page` changes).
- **Polling:** a `useEffect` keyed on `runs` sets up (or clears) a single `setInterval(load,
  4000)` — active only while `runs.some(r => r.status === "running")` is true for the *currently
  loaded page*; cleared on unmount and whenever that condition goes false. Triggering a batch calls
  `load()` immediately afterward regardless of the current polling state, so the new "running" row
  appears without waiting for the next tick (and the effect then starts polling on its own once
  `runs` updates to include it).
- Changing `page` does not affect the trigger toolbar (item 1), which always reflects each batch's
  single most-recent run regardless of which history page is showing — if that run isn't on the
  currently loaded page (e.g. operator paged back into older history), the chip falls back to "no
  recent run" rather than showing stale/wrong data; this is an accepted minor gap given the
  toolbar's job is "what's happening right now," not history browsing.

### Navigation

Add a nav link to `AppLayout.tsx`, positioned after "Ingestion" (matching the existing order —
newest/most-operational items last):

```tsx
<NavLink to="/admin" className={({ isActive }) => isActive ? "font-semibold text-blue-600" : "text-gray-600"}>
  Admin
</NavLink>
```

Register the route in `router.tsx`: `{ path: "/admin", element: <AdminBatchesPage memesApi={memesApi} /> }`.

### Error handling

- List-fetch errors (`load()` failing entirely) use the same page-level error state pattern
  `IngestionReviewPage` uses: replace the whole page body with an error message, no partial/stale
  table shown underneath.
- Trigger errors are scoped to that one batch's row (see above) — a failed trigger must not clear
  or replace the history table or other batches' state.

### Testing

`AdminBatchesPage.test.tsx` (Vitest + Testing Library, mocking `MemesApi` — matching
`IngestionReviewPage.test.tsx`'s style):

- Renders one toolbar row per `ADMIN_BATCHES` entry and the history table from a mocked
  `listBatchRuns` response.
- Trigger flow: click "Run" → button shows "Confirm?" → click again → `triggerBatchRun` called with
  the right batch name → `load()` re-invoked (mock call count increments).
- Clicking a different batch's "Run" while one is in "Confirm?" cancels the first (asserts the
  first button's label reverts and only the second batch's trigger fires on its own confirm).
- `409` response → inline error text for that batch only, other rows/table unaffected.
- Polling: using fake timers, a `listBatchRuns` mock returning a `"running"` row causes a second
  call after the interval elapses; once the mock's next response has no `"running"` rows, advancing
  timers further causes no additional calls.
- Pagination: clicking Next calls `listBatchRuns` with `offset = PAGE_SIZE`; Prev/Next
  disabled-state at the first/last page boundaries given a known `total`.

## Rollout

1. Add the three JSON schemas + `all.schema.json` entries; regenerate frontend and Android types;
   verify no unexpected diff.
2. Add `triggerBatchRun`/`listBatchRuns` to `MemesApi` + `HttpMemesApi`.
3. Implement `AdminBatchesPage.tsx` + its test file.
4. Register the route in `router.tsx` and the nav link in `AppLayout.tsx`.
5. Manual check: `tsc -b && eslint src/ && vitest run` from `Frontend/memes-frontend/`; if a real
   backend is reachable in the dev environment, load `/admin`, trigger `move_flagged`, confirm the
   row appears and transitions to `completed` without a manual refresh.
