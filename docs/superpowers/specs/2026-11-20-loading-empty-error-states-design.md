# Loading, empty and error states (UI/UX audit ranking item 4)

Status: done
Plan: `docs/superpowers/plans/2026-11-20-loading-empty-error-states.md`
Originates from: `docs/superpowers/specs/2026-11-18-ui-ux-audit.md` (ranking item 4, findings F9 and F12, follow-up F-e)

Approval: the owner said "let's go" to the scope proposed in the session of 2026-11-20 (recommendation: do item 4 with F-e first, then item 5).

## Intent

Friends will use a public read-only stage. The first things they hit are a failed load, a meme link that does not exist, and an almost empty page. Today a failed list fetch retries in a loop (the frozen tab of F-e), a meme page that fails to load says "Loading..." forever, and Trending explains nothing. Each screen should always end in one of three clear states: content, a helpful empty state, or an error with a way to retry.

## Goals

1. A failed fetch never retries by itself in a loop and never leaves the screen in a dead end.
2. Explore (`MemesList`): an error state with Retry, for a failed first page (replaces the grid area) and for a failed later page (an inline row under the loaded cards, cards stay).
3. Meme page (`/meme/:id`): skeleton while loading, and an error state with Retry when the fetch fails or the meme does not exist, with a link back to Explore.
4. Trending: card skeletons while loading, an `EmptyState` with a one-line explanation and a link to Explore when there is nothing, and the existing error with a `Button` retry.
5. Explore's empty state tells a search that found nothing apart from an empty library.
6. Reuse `src/ui/` (`EmptyState`, `Skeleton`, `MemeCardSkeleton`, `Button`); no new visual language.

## Non-goals

- Admin and curator screens (tables, review pages, batches). Their states stay as they are.
- Statistics page (has an error state; its "Loading…" line is left).
- Reply page empty state (belongs to the Home/Reply work, ranking item 6).
- Dark mode, copy translation, a global error boundary.
- No backend change and no API contract change; `backend_api.md` is untouched.

## Anti-goals

- The pagination hook must not change its behaviour for successful fetches: paging, eviction, replay and the existing hook tests keep passing unchanged.
- No automatic retry loop, no retry timers: retry is always a click.

## Constraints

- React Compiler lint rules and `StrictMode`: effects survive a double mount, refs only in handlers and effects.
- `MemesDuplicatesList` (curator page) also uses `useWindowedPagination`. It gets no new UI, but it must stop looping on a failure because the hook stops.
- Pre-commit: `tsc -b`, `eslint src/` with 0 warnings, `vitest run`; the controller re-runs them.

## Approaches considered

Hook error handling:
- A. Catch inside `useWindowedPagination`, expose `error` and `retry()`, and make `loadForward` refuse to run while an error is set. Fixes the loop at its source, helps every consumer. **Chosen.**
- B. Catch in each caller's `fetchPage` and return an empty page. Hides the failure from the hook, so the list shows "Nothing to show" and looks like an empty library. Rejected.
- C. Retry with backoff. More moving parts, hides real outages from the person, anti-goal. Rejected.

## Design

### Hook (`src/hooks/useWindowedPagination.ts`)

- New state `error: boolean` (true after a failed forward or backward fetch) and a `retry()` function.
- `loadForward` and `loadBackward` wrap the fetch in `try/catch/finally`. On failure: set `error`, leave `pages`, cursors and `hasMore*` as they were (nothing was consumed), clear `loading`.
- While `error` is set, `loadForward` and `loadBackward` return immediately, so `endReached` and `startReached` cannot loop.
- `retry()` clears `error` and calls `loadForward` (or `loadBackward` when the failed call was a backward one).
- A `resetKey` change clears `error` with the other bookkeeping.
- The result type gains `error` and `retry`; existing fields are unchanged.

### Explore (`src/components/MemesList.tsx`)

- `error && items.length === 0`: `EmptyState tone="error"` titled "Could not load memes", action `Button` "Try again" wired to `retry`. The skeleton and the "Nothing to show" state are hidden in this case.
- `error && items.length > 0`: an inline `role="alert"` row under the loaded cards with the same message and a "Try again" button; the cards stay.
- Empty (no items, no error, not loading): with a non-empty `filter` or any tag filter selected, "No memes match" with the description "Try fewer words or clear a filter."; otherwise the existing "Nothing to show".
- The small "Loading..." line shown under loaded cards while a later page loads stays, but gets `role="status"`. The first-load skeleton grid is unchanged.

### Meme page (`src/pages/MemePage.tsx`)

- Local state `status: "loading" | "ready" | "error"`. `useFetchById` already takes an `onError`; use it.
- Loading: a `role="status"` region with a large `Skeleton` image block (square, `max-w-5xl` column) and two text-line skeletons.
- Error: `EmptyState tone="error"` titled "Could not load this meme", description "It may have been removed, or the connection failed.", actions "Try again" (re-runs the fetch; implemented by bumping a request counter that is part of the fetch key) and a link "Back to Explore" (`buttonClasses` on a `Link` to `/explore`).
- Navigating to a different id resets to loading.

### Trending (`src/pages/TrendingMemesPage.tsx`)

- While loading and no cards yet: a grid of 6 `MemeCardSkeleton` in the existing grid classes plus a `role="status"` `sr-only` "Loading..." line (same pattern as `MemesList`). When cards exist and more are loading, keep a small status line.
- Empty (not loading, no error, no cards): `EmptyState` with title "Nothing trending yet" (all time) or "Nothing trending in the last 30 days" and the description "Memes climb here when their duplicates are merged: the more copies of a meme show up, the more popular it is." plus an action link "Browse memes" to `/explore`. The two existing messages move into the title.
- Error: keep the `role="alert"` text and replace the underline link with a `Button` "Retry"; behaviour of the retry (first page vs next page) is unchanged.
- The window buttons and card grid are untouched.

### Tests

- Hook: a failed forward fetch sets `error`, does not call `fetchPage` again on further `loadForward` calls, `retry()` re-fetches the same cursor and clears `error`; a failed backward fetch likewise; `resetKey` change clears the error; all existing hook tests unchanged.
- `MemesList`: first-page failure shows the error state and Retry recovers; later-page failure keeps the cards and shows the inline row; filtered empty message vs plain empty message.
- `MemePage`: loading skeleton, error with Retry recovering, id change resets.
- Trending: skeleton while loading, empty copy per window with the Explore link, Retry `Button`.

## Left to the builder

Exact class names within the token set, the precise wording of the inline "Try again" row, and whether the retry counter on the meme page is a `useState` number or a key on a small inner component, as long as StrictMode does not double-fetch (one request per mount/id, as `useFetchById` guarantees today).

## Decisions I made without asking (check these)

- Retry is a click only, never automatic.
- Statistics and Reply are out of scope.
- The meme page shows the same error for "not found" and "network failure" (the API client's error type does not need to be inspected for this); the description names both causes.
