# Loading, empty and error states Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every public list and detail screen ends in content, a helpful empty state, or an error with a click-to-retry, and a failed list fetch no longer loops.

**Architecture:** `useWindowedPagination` catches fetch failures and exposes `error` and `retry()`; `MemesList`, `MemePage` and `TrendingMemesPage` render states with the existing `src/ui/` components (`EmptyState`, `Skeleton`, `MemeCardSkeleton`, `Button`, `ButtonLink`). Frontend only.

**Tech Stack:** React 19 (StrictMode, React Compiler lint rules), TypeScript, Vitest + Testing Library, Tailwind tokens in `src/ui/`.

**Spec:** `docs/superpowers/specs/2026-11-20-loading-empty-error-states-design.md`

## Global Constraints

- Work only in `Frontend/memes-frontend/`; no backend, no API contract, no `backend_api.md` change.
- Retry is a click only: no automatic retry, no timers, no backoff.
- Successful-fetch behaviour of `useWindowedPagination` is unchanged; every existing test in `useWindowedPagination.test.ts`, `MemesList.test.tsx`, `MemePage.test.tsx`, `MemePage.stage.test.tsx` and `TrendingMemesPage.test.tsx` keeps passing unmodified (add tests; edit an old one only if it asserted copy this plan changes, and say so in the commit).
- Effects must survive a StrictMode double mount; refs are touched only in handlers and effects.
- Error states use `EmptyState tone="error"` (renders `role="alert"`); loading regions use `role="status"`.
- Gate before every commit, from `Frontend/memes-frontend/`: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`.
- Commit messages end with the two attribution lines from the session reminder (Co-Authored-By and Claude-Session).

## Review Focus

- A fetch that fails on the very first page of Explore must show the error state, not "Nothing to show", and must call `fetchPage` exactly once until Retry is clicked.
- A fetch that fails on page 2 keeps page 1's cards on screen and shows an inline alert row; scrolling to the end again must not refetch.
- Retry after a failure refetches the same cursor (no skipped or duplicated page).
- Changing the filter while in the error state clears the error and loads the new query.
- `/meme/<unknown id>` shows the error state with a link to Explore, never an endless "Loading...".
- Moving from one `/meme/:id` to another shows loading, not the previous meme, and an old response arriving late is ignored.
- Trending's empty copy differs per window, and a failed "Show more" does not wipe the cards already shown.

---

### Task 1: Pagination hook error and retry

**Files:**
- Modify: `Frontend/memes-frontend/src/hooks/useWindowedPagination.ts`
- Test: `Frontend/memes-frontend/src/hooks/useWindowedPagination.test.ts`

**Interfaces:**
- Produces: `UseWindowedPaginationResult` gains `error: boolean` and `retry: () => void`. All other fields unchanged. `MemesDuplicatesList` destructures the result by name, so it keeps compiling without edits.

- [ ] **Step 1: Write failing tests** in `useWindowedPagination.test.ts`, following that file's existing `renderHook` style:
  - `sets error and does not refetch while errored`: `fetchPage` rejects once; after the first load `result.current.error === true`, `items` is `[]`, `loading === false`; calling `result.current.loadForward()` again resolves without a second `fetchPage` call (`toHaveBeenCalledTimes(1)`).
  - `retry refetches the same cursor and clears the error`: `fetchPage` rejects on the first call, resolves on the second; after `act(() => result.current.retry())` and settling, `error === false`, the items of the second response are present, and the second call received the same `cursor` argument as the first.
  - `a failed later page keeps the loaded pages`: first call resolves with `hasNext: true, nextCursor: "c2"`, second call (cursor `"c2"`) rejects; `items` still equals page 1's items, `error === true`; after retry succeeds, items are page 1 then page 2.
  - `resetKey change clears the error`: rerender with a new `resetKey` after a failure; `error === false` once the new first fetch is issued.
  - `a failed backward fetch sets error and retry replays backward`: use the existing cold-backward test setup (`supportsColdBackward`, `initialCursor`), make the backward `fetchPage` reject once, assert `error === true`, then `retry()` triggers the backward fetch again (not a forward one).

- [ ] **Step 2: Run to see them fail:** `vitest run src/hooks/useWindowedPagination.test.ts` — the five new tests fail (`error` undefined).

- [ ] **Step 3: Implement** in `useWindowedPagination.ts`: add `error` state plus an `errorRef` mirror and a `failedDirectionRef` (`"forward" | "backward" | null`). In `loadForward` and `loadBackward`: return immediately when `errorRef.current` is set; add a `catch` that sets `errorRef`, `error` state and `failedDirectionRef`, and leaves pages, cursors, `hasMore*` untouched (the existing `finally` still clears `loading`). Note `loadBackward`'s early `return`s inside `try` already run `finally`; keep them. `retry()` clears the error (ref, state, direction) and calls `loadBackward` or `loadForward` per `failedDirectionRef`; it must be stable (`useCallback`) and use refs to the two loaders as the reset effect already does. The `[resetKey]` reset effect also clears the error ref, state and direction. Add `error` and `retry` to the returned object and to `UseWindowedPaginationResult`.

- [ ] **Step 4: Run** `vitest run src/hooks/` — all hook tests (old and new) pass; then `vitest run src/components/MemesDuplicatesList` — still passes.

- [ ] **Step 5: Gate and commit:** `tsc -b`, `eslint src/ --max-warnings 0`, then `git commit` with message `feat(pagination): catch fetch failures, expose error and retry (F-e)`.

---

### Task 2: Explore error and empty states

**Files:**
- Modify: `Frontend/memes-frontend/src/components/MemesList.tsx`
- Test: `Frontend/memes-frontend/src/components/MemesList.test.tsx`

**Interfaces:**
- Consumes: `error: boolean` and `retry: () => void` from `useWindowedPagination` (Task 1); `EmptyState`, `Button` from `../ui`.

- [ ] **Step 1: Write failing tests** in `MemesList.test.tsx`, reusing that file's `memesApi` mock helpers:
  - `shows an error state with Try again when the first page fails`: `searchMemes` rejects; expect an alert with text `Could not load memes`, no `Nothing to show`, and `searchMemes` called once; mock it to resolve then click `Try again`: cards appear and the alert is gone.
  - `keeps loaded cards and shows an inline alert when a later page fails`: page 1 resolves (`hasNext: true`, cursor), page 2 rejects; cards of page 1 stay; an alert containing `Try again` is present; a `Try again` click fetches the same cursor again.
  - `says no memes match for a query that found nothing`: `filter="zzzz"`, empty result: text `No memes match` and `Try fewer words or clear a filter.`; same for a non-empty `tagFilters`.
  - `keeps "Nothing to show" for an unfiltered empty list`.

- [ ] **Step 2: Run to see them fail:** `vitest run src/components/MemesList.test.tsx`.

- [ ] **Step 3: Implement in `MemesList.tsx`:** destructure `error, retry` from the hook. Render per spec "Explore": (a) `error && items.length === 0` → `<EmptyState tone="error" title="Could not load memes" action={<Button onClick={retry}>Try again</Button>} />` and suppress the skeleton and the empty state; (b) `error && items.length > 0` → an inline row below the cards with `role="alert"`, the same title, and a `Button` "Try again"; (c) the empty state when `items.length === 0 && !loading && !error`: title `No memes match` plus description `Try fewer words or clear a filter.` when `filter` is non-empty or any `tagFilters` value list is non-empty, otherwise the existing `Nothing to show`; (d) the bottom "Loading..." line keeps its text but gets `role="status"`. Do not touch the skeleton grid or Virtuoso setup. Wrap the unfiltered/filtered decision in a small named helper in the same file.

- [ ] **Step 4: Run** `vitest run src/components/MemesList.test.tsx src/pages` — pass.

- [ ] **Step 5: Gate and commit:** `feat(explore): error and no-match states with retry`.

---

### Task 3: Meme page loading and error states

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/MemePage.tsx`
- Test: `Frontend/memes-frontend/src/pages/MemePage.test.tsx`

**Interfaces:**
- Consumes: `useFetchById(id, fetcher, onResult, onError)` from `../utils/useFetchById` (unchanged); `EmptyState`, `Skeleton`, `Button`, `ButtonLink` from `../ui` (check `ButtonLink`'s props in `src/ui/Button.tsx` and use it for the Explore link, falling back to `<Link className={buttonClasses(...)}>` as `home/Rail.tsx` does).

- [ ] **Step 1: Write failing tests** in `MemePage.test.tsx` (keep its router and mock-api setup):
  - `shows a skeleton status while the meme loads` (pending promise): a `role="status"` region exists and no `Meme <id>` heading.
  - `shows the error state with Try again and Back to Explore when the fetch fails`: `getMeme` rejects; alert `Could not load this meme`, description contains `It may have been removed`, a link `Back to Explore` with `href` `/explore`; make `getMeme` resolve and click `Try again`: heading `Meme <id>` appears.
  - `a late response for the previous id is ignored`: navigate from `/meme/a` to `/meme/b` with `a` resolving after `b`; the page shows `b`.

- [ ] **Step 2: Run to see them fail:** `vitest run src/pages/MemePage.test.tsx`.

- [ ] **Step 3: Implement in `MemePage.tsx`:** keep `meme` state and add `status: "loading" | "ready" | "error"` and an `attempt` counter. `useFetchById` takes the id only, so make retry re-run it by mounting a small inner component keyed by `${id}:${attempt}` that owns the fetch and state (this also resets state when the id changes, so no previous meme can flash). The outer `MemePage` holds `attempt` and passes `onRetry`. Loading renders a `role="status"` block: `sr-only` "Loading..." plus a square `Skeleton` (`max-w-5xl mx-auto`) and two short line `Skeleton`s. Error renders the `EmptyState` from the spec with `Button` "Try again" and the Explore link as its `action`. Ready renders today's markup unchanged (including the curator `ProfileEditor`). `MemePage.stage.test.tsx` must still pass.

- [ ] **Step 4: Run** `vitest run src/pages/MemePage` — both files pass.

- [ ] **Step 5: Gate and commit:** `feat(meme-page): loading skeleton and error state with retry`.

---

### Task 4: Trending loading, empty and error states

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/TrendingMemesPage.tsx`
- Test: `Frontend/memes-frontend/src/pages/TrendingMemesPage.test.tsx`

**Interfaces:**
- Consumes: `EmptyState`, `Button`, `MemeCardSkeleton`, `buttonClasses`/`ButtonLink` from `../ui`.

- [ ] **Step 1: Write failing tests** in `TrendingMemesPage.test.tsx` (keep its setup; if an existing test asserts the old empty copy `No trending memes yet.` or `No memes picked up duplicates in the last 30 days.`, update that assertion to the new title and note it in the commit):
  - `shows card skeletons while the first page loads`: pending promise, `getAllByTestId("meme-card-skeleton")` has 6 entries and a `role="status"` region with `Loading...`.
  - `empty all-time list explains trending and links to Explore`: title `Nothing trending yet`, text containing `the more copies of a meme show up`, a link `Browse memes` to `/explore`.
  - `empty 30-day list has its own title`: `?window=30d` gives `Nothing trending in the last 30 days`.
  - `error shows a Retry button that reloads`: first call rejects; `role="alert"` with `Could not load trending memes.` and a `Retry` button; second call resolves and cards appear.
  - `a failed Show more keeps the cards`: page 1 resolves with `hasNext: true`, page 2 rejects; page 1 cards remain, the alert is shown, and `Retry` requests the next cursor.

- [ ] **Step 2: Run to see them fail:** `vitest run src/pages/TrendingMemesPage.test.tsx`.

- [ ] **Step 3: Implement in `TrendingMemesPage.tsx`:** when `isLoading && items.length === 0` render 6 `MemeCardSkeleton` in the existing grid classes plus the `sr-only` `role="status"` "Loading..." line; when `isLoading && items.length > 0` keep a small `role="status"` "Loading..." line. Replace the empty block with the `EmptyState` per spec (title per window, the one-line explanation as description, action = link `Browse memes` to `/explore`). Replace the error row's underline `<button>` with `<Button>` "Retry" with the same handler. Leave the window buttons, grid and "Show more" as they are.

- [ ] **Step 4: Run** `vitest run src/pages/TrendingMemesPage.test.tsx` — pass.

- [ ] **Step 5: Gate and commit:** `feat(trending): skeletons, guided empty state, retry button`.

---

### Task 5: Whole-branch verification and docs

**Files:**
- Modify: `docs/superpowers/specs/2026-11-18-ui-ux-audit.md` (Progress section), `docs/superpowers/specs/2026-11-20-loading-empty-error-states-design.md` (status line)

- [ ] **Step 1:** From `Frontend/memes-frontend/` run the full gate once more: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`; expected: all clean and green.
- [ ] **Step 2:** Visual check per the epic recipe (throwaway vite server on a free port 5190+, untracked config proxying `/api` to the `general` backend 8082, `VITE_BACKEND_API_URL` set to that server's own URL; never bind 5173-5175 or 8081-8083, never click anything that writes). Check: Explore with the backend unreachable (point `VITE_BACKEND_API_URL` at a dead port) shows the error state once and does not hammer the network (`read_network_requests` count stays flat); `/meme/00000000-0000-0000-0000-000000000000` shows the error state; Trending with `?window=30d`. Use element lookups, not pixel clicks. Stop the server by its listening port afterwards, delete the temporary config.
- [ ] **Step 3:** Update the audit doc's Progress entry for item 4 to done (branch `worktree-states-skeletons`, F-e fixed) and set the new spec's status to `done`.
- [ ] **Step 4: Commit:** `docs(audit): mark item 4 and F-e done`.
