# Windowed, Bidirectional Infinite Scroll — Design

Status: draft

**Date:** 2026-08-10.

Bounds the memory/DOM cost of `MemesList`'s infinite scroll (today it only ever appends, forever)
by adopting `react-virtuoso` for rendering, adds scroll-up ("load earlier") support symmetric to
the existing scroll-down loading, and uses that same mechanism to fix the duplicates page losing
your place when you navigate away and back.

---

## Motivation

`MemesList` (`Frontend/memes-frontend/src/components/MemesList.tsx`) keeps every fetched `Meme` in
a single `memes` array for the life of the component and never trims it. For a long browsing
session (thousands of images scrolled through), this means an ever-growing DOM tree and an
ever-growing set of mounted `<img>` elements with decoded bitmaps in memory — the dominant real
cost. There's also no way to scroll back up to reveal earlier items once the browser's own
scrollback runs out of rendered content above the viewport, since nothing is ever un-rendered in
the first place, so this is purely a growth problem today, not yet a "can't get back" problem.

Separately, `ExploreDuplicatesPage.tsx` persists a cursor to the URL as the user scrolls
(`onCursorChange`), intended to let the browser's back button (or a page reload) resume near where
the user was. In practice it doesn't work: on remount, `MemesList` fetches only the *continuation*
batch starting at that cursor — none of the previously-scrolled pages are re-fetched — so the page
comes back short, and the browser can't restore the old scroll position because the document isn't
tall enough yet. The user visibly lands back near the top instead of where they left off.

Both problems share a root cause: `MemesList` can only ever move forward through a cursor stream,
never backward. This spec fixes both by adding real bidirectional loading.

## Scope

**In scope:**
- All six `MemesList` consumers (Search, Duplicates, Untagged, Flagged, No-OCR, Recommendations)
  get windowed rendering via `react-virtuoso`, replacing the current unbounded array + manual
  `IntersectionObserver` sentinel.
- Scroll-up ("load earlier page") support for all six, via a session-local cursor history replay.
- A real backend "page before cursor" query for the duplicates endpoint specifically, so a page
  load that starts mid-stream (URL `?cursor=X`) can still load *earlier* than `X`, not just resume
  forward from it — this is what actually fixes the duplicates scroll-position problem, since a
  fresh mount has no session history to replay from.
- `ExploreDuplicatesPage`'s persisted URL cursor changes from "forward frontier reached so far" to
  "cursor of the page currently at the top of the viewport" — a much closer approximation of "where
  the user actually was."

**Out of scope:**
- `IngestionReviewPage` — a bounded, non-infinite cluster list (loaded in full per tier), unrelated
  to this problem.
- Adding backward pagination to the other five endpoints' backends. Their scroll-up support is
  session-only (replaying cursors already visited this session); a cold deep link into the middle
  of one of those lists still can't load earlier than its start cursor. Only duplicates gets a real
  backward query, per the "stick to end, not start" bug being specific to that page.
- Any change to how many items each fetch returns (limits stay 21/36/40 depending on listing type,
  matching current per-page-type values in `MemesList.tsx`).

## Design

### New dependency: `react-virtuoso`

Not currently in `package.json`. Chosen over a hand-rolled page-deque + manual
`IntersectionObserver` + manual `scrollHeight`-delta compensation because it already solves the
hard part — prepending content above the viewport without a visible scroll jump — via its
documented `firstItemIndex` mechanism (built for exactly this "chat scrollback" shape of problem),
and provides `startReached`/`endReached` callbacks that map directly onto "need an earlier/later
page."

**Revised during implementation (Task 6):** the original design here specified `VirtuosoGrid` for
the six flat listings. Implementation found that `VirtuosoGridProps` in the installed
`react-virtuoso@4.18.11` has no `firstItemIndex` field at all (confirmed against the library's own
`.d.ts` — it's list/table-only, on `VirtuosoProps`/`TableVirtuosoProps`, not the grid variant), and
no equivalent prop provides the same jump-free-prepend guarantee. Since jump-free backward loading
is the entire point of this feature, all seven consumers use plain **`Virtuoso`** (single column),
never `VirtuosoGrid`:

- **The six flat listings** (Search, Explore, Untagged, Flagged, No-OCR, Recommendations) — each virtualized
  row is a *chunk* of up to 6 `MemeCard`s (matching the existing `md:grid-cols-6` column count),
  rendered via the same `grid grid-cols-1 md:grid-cols-6 gap-4` Tailwind classes applied to each
  row's wrapper `div`. Chunk boundaries are **aligned to the item's absolute position in the true
  (unbounded) sequence**, not recomputed relative to whatever's currently in the window — see
  "Row chunking" below. This keeps row boundaries stable and page-boundary-agnostic (matching
  today's seamless grid flow) rather than visibly breaking at every ~21-40-item page edge.
- **The duplicates page** — one whole **cluster** per virtualized row, each row rendering its
  existing internal `flex-wrap` of member `MemeCard`s unchanged, exactly as originally designed.
  Virtuoso's dynamic height measurement handles clusters of varying member counts fine; no fixed
  row height is required.

### Row chunking (six flat listings)

Because `useWindowedPagination`'s `firstItemIndex` already tracks the absolute position of
`items[0]` in the true sequence (that's its whole purpose), row boundaries for the fixed 6-column
chunking can be derived **statelessly** from `firstItemIndex` alone — no page-diffing or effect-based
tracking needed (unlike the cluster-row case below, where variable cluster sizes make a simple
modulo impossible): the first row contains `6 - (firstItemIndex mod 6)` items (a full row if already
aligned), subsequent rows are full 6-item chunks, and the row-level `firstItemIndex` passed to
`Virtuoso` is simply `Math.floor(firstItemIndex / 6)`. Recomputed via `useMemo` on every render from
`items`/`firstItemIndex` directly.

### `useWindowedPagination` hook

New file, `Frontend/memes-frontend/src/hooks/useWindowedPagination.ts`, extracted out of
`MemesList` rather than grown inline, so the windowing/cursor-replay logic is isolated and
independently testable from rendering. Owns:

- **`pages: Page[]`** — a deque of fetched pages (each `{ cursor: string | undefined, items: Meme[] }`,
  where `cursor` is the cursor *used to fetch* that page — i.e. `pages[0].cursor` is the initial
  cursor, `undefined` for a from-scratch load). Capped at `MAX_PAGES = 4`: when a fetch in one
  direction pushes the deque past the cap, the page at the *opposite* end is evicted.
- **`cursorHistory`** — implicitly `pages.map(p => p.cursor)` plus the trailing `nextCursor` of the
  last page; this **is** the session cursor stack. Nothing extra to track — evicting a page from
  `pages` does not forget its cursor's position in history, because the neighboring page's own
  `cursor` field already encodes "what came before it." Scrolling up past an evicted page re-fetches
  it by calling the same forward-fetch function with that remembered cursor — a plain replay, no new
  API surface, safe because keyset pagination is stable absent concurrent deletes (an accepted,
  pre-existing property of every cursor in this codebase, not something this spec changes).
- **`loadForward()`** / **`loadBackward()`** — call the page-type-specific fetch function (the
  existing `getResponseFromBackend` branch in `MemesList`, moved into the hook) with, respectively,
  the last page's `nextCursor` or the page-before-the-first-page's cursor. `loadBackward()` is a
  no-op (nothing to do, not an error) when there is no earlier cursor to replay **and** the page
  type isn't duplicates — see below for duplicates' extra capability here.
- **`firstItemIndex`** — virtuoso's own bookkeeping value, decremented by the prepended page's
  item count on every successful `loadBackward()`, per virtuoso's documented prepend pattern.

Wired to virtuoso via `startReached={loadBackward}` / `endReached={loadForward}`.

### Duplicates: real backward query

New capability on the duplicates endpoint (`GET /api/images/duplicates`), mirroring the compound
`(cluster_id, image_id)` keyset cursor already shipped for the forward-skip-bug fix
(`Backend/app/repositories/image_repository.py:416`, `Backend/app/services/image_service.py:286`):

- Router (`Backend/app/api/images.py:175`): add `direction: Literal["forward", "backward"] = "forward"`
  query param to the existing `/duplicates` route (no new route).
- `ImageService.get_duplicates_clustered`: on `direction="backward"`, call a new
  `ImageRepository.get_duplicates_clustered_before` — same query shape as the existing method, but
  `tuple_(cluster.cluster_id, img.id) < tuple_(cursor_cluster_id, cursor_image_id)`, ordered
  `DESC` on both columns so the `LIMIT` takes the rows *immediately before* the cursor, then
  reversed back to ascending order in Python before returning (so the response's item order is
  always ascending regardless of fetch direction — the frontend never needs to know which direction
  produced a given page).
- `MemeSearchResponse` gains a new optional `previousCursor: string | undefined` field (added to
  `shared/schemas/memesearchresponse.schema.json`, regenerated into both Backend and Frontend
  types) rather than overloading `nextCursor` with direction-dependent meaning — `nextCursor`/
  `hasNext` keep meaning "forward from here" everywhere, including on a backward-fetched page
  (where they simply resolve to the cursor that was passed in, since that's by construction the
  boundary immediately after this page's last item). `previousCursor` is set only when a backward
  fetch finds more items beyond what it returned; `null`/absent means this page reached the true
  beginning. Every other endpoint leaves `previousCursor` unset.
- `Frontend/memes-frontend/src/api/MemesApi.ts` / `HttpMemesApi.ts`: `iterateDuplicates` gains a
  `direction?: "forward" | "backward"` param, passed through as the query string param above.

The other five `iterate*`/`search`/`getRecommendations` methods are unchanged — their backward
loading is purely the session-replay described above, calling the existing forward method with an
earlier remembered cursor.

`useWindowedPagination` takes a `supportsColdBackward?: boolean` option (set only by
`ExploreDuplicatesPage`). When true and `loadBackward()` is called with no earlier page in session
history, it calls the fetch function with `direction="backward"` and the current first page's own
cursor instead of no-op'ing — this is the one branch point in the hook that's aware duplicates has
a real backward query; every other page type simply has the flag unset and falls back to the no-op.

### Cluster-row assembly (duplicates page only)

`useWindowedPagination`'s pages are fetched and evicted at raw-image granularity (40 images/page,
plain whole-page FIFO eviction — no cluster-awareness in the hook itself, keeping it generic for
all six consumers). `Virtuoso` rows are whole clusters assembled by grouping the currently-windowed
items by `clusterId` (same grouping the old implementation already did, just scoped to the window
instead of an ever-growing array). A cluster whose remaining members haven't loaded yet (still on
an adjacent, not-yet-fetched page) briefly renders with fewer members than it truly has; this
self-heals as soon as that page loads, and never *permanently* loses a member — that guarantee
comes entirely from the compound-cursor pagination fix (already shipped), not from anything in the
windowing layer. This is an accepted characteristic carried forward unchanged from the pre-windowing
implementation, which had the exact same transient behavior; the duplicates page is browsing-only
(no keep/reject decisions happen here — that's `IngestionReviewPage`, a separate, non-infinite
feature), so a momentarily-incomplete cluster during active scrolling is low-stakes.

Because cluster rows have different cardinality than the hook's item-level window, the rendering
layer tracks its own row-level `firstItemIndex` for `Virtuoso`'s prepend-without-jump mechanism,
derived by diffing the hook's `pages` (identified by cursor) between renders and adjusting by the
number of distinct clusters each added/evicted page contributed. This slightly over/under-counts
when a cluster straddles a page boundary (counted once per page instead of once overall) — accepted
given clusters are typically small relative to the 40-item page size; the failure mode is a one-row
scroll adjustment, self-corrected on the next load, not a correctness issue.

### URL cursor tracking (`ExploreDuplicatesPage`)

Today: `onCursorChange` fires after every forward page load with that page's `nextCursor` — "how
far forward have we ever gotten." Replaced with: a `rangeChanged` callback from `Virtuoso`
(debounced ~300ms to avoid URL churn while actively scrolling) that looks up which page the
topmost currently-visible row belongs to, and persists *that page's own* `cursor` value — "where is
the user right now," which is what a resumed session should anchor to. On mount, this is also the
cursor `loadBackward()` uses as its starting point if the user immediately scrolls up.

### Error handling

- A failed `loadForward()`/`loadBackward()` leaves the existing `pages` deque untouched (no partial
  page inserted) and surfaces the same inline "Loading…" replaced by nothing / silently retryable
  state `MemesList` already has today for forward loads — no new error UI needed, this spec doesn't
  change failure presentation, only what triggers a load.
- `loadBackward()` racing with itself (fast scroll-up) is guarded the same way `loadForward` already
  guards today — a `loadingRef`-style in-flight flag, one direction of travel at a time.

### Testing

`react-virtuoso` relies on real layout measurement (`ResizeObserver`) that `jsdom` doesn't provide,
so component tests mock `react-virtuoso` itself rather than simulate real scroll events — a
documented, common pattern for this library. The mock renders `itemContent` for every item
synchronously (no virtualization in tests, just a plain map) and exposes `startReached`/`endReached`
as directly-callable props, so a test can do:

```tsx
vi.mock('react-virtuoso', () => ({
  // Both the flat listings (MemesList) and the duplicates page (MemesDuplicatesList) use plain
  // Virtuoso -- see "Revised during implementation (Task 6)" above for why VirtuosoGrid was
  // dropped. One mock target covers both.
  Virtuoso: (props) => <div>{props.data.map((item, i) => props.itemContent(i, item))}</div>,
}))
// then: fireEvent triggers aren't real scroll -- call the captured `startReached`/`endReached`
// props directly to simulate reaching an edge.
```

`useWindowedPagination` itself gets direct hook tests (`@testing-library/react`'s `renderHook`) independent
of any virtuoso mock: page eviction at `MAX_PAGES`, cursor history replay on `loadBackward`, and
cold-backward behavior (including exhaustion once a backward fetch returns no items), using a mock
fetch function rather than a real `MemesApi`.

New/changed test files:
- `Frontend/memes-frontend/src/hooks/useWindowedPagination.test.ts` (new)
- `Frontend/memes-frontend/src/components/MemesList.test.tsx` (updated for the virtuoso mock)
- `Backend/tests/test_image_service.py` (new cases for `direction="backward"`, mirroring the
  existing forward pagination test added for the cluster-split cursor fix)
- `Backend/tests/test_images_endpoints.py` (new case: `direction=backward` round-trips through the
  router)

## Rollout

1. Add `react-virtuoso` dependency.
2. Backend: add `get_duplicates_clustered_before` to `ImageRepository`, wire `direction` through
   `ImageService.get_duplicates_clustered` and the `/duplicates` router, add tests.
3. Frontend API layer: `iterateDuplicates(..., direction?)` in `MemesApi`/`HttpMemesApi`.
4. Extract `useWindowedPagination` hook (page deque, whole-page eviction, cursor history replay,
   cold-backward support) with its own unit tests, no UI wiring yet.
5. Rewrite `MemesList` to use the hook + `Virtuoso` with row-chunking for the six flat listings
   (Search, Explore, Untagged, Flagged, No-OCR, Recommendations); add a new, separate
   `MemesDuplicatesList` component using the hook + `Virtuoso` with cluster-row assembly for the
   duplicates page (kept as its own file rather than a branch inside `MemesList`, since it's the
   only consumer needing cluster-row assembly, cold backward loading, and cursor tracking),
   removing the old `IntersectionObserver` sentinel code from both.
6. `ExploreDuplicatesPage`: switch `onCursorChange` to the `rangeChanged`-based topmost-page cursor;
   wire `loadBackward` to use `direction=backward` specifically for this page. This is the
   `MemesDuplicatesList` component from step 5 (a dedicated component, not a `listDuplicates` prop
   on `MemesList`), which passes `supportsColdBackward: true` to `useWindowedPagination` so the hook
   knows it can go backward past session history for this one page type.
7. `tsc -b && eslint src/ && vitest run` from `Frontend/memes-frontend/`; `cd Backend && pytest`.
8. Manual check per `run` skill: scroll through Search far enough to confirm old pages actually
   unmount (DevTools element count stays bounded), scroll back up and confirm earlier items
   reappear without a visible jump; on Duplicates, scroll down, open a meme's permalink, browser-back,
   confirm the page resumes at the same cluster instead of near the top.
