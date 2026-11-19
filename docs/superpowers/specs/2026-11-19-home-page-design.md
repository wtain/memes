# Home page at `/`

Status: planned
Plan: docs/superpowers/plans/2026-11-19-home-page.md
Originates from: docs/superpowers/specs/2026-11-18-ui-ux-audit.md

Scope: audit ranking item 6 (I1 + I20), fixes F2 and F8 (partly F9). Frontend only (`Frontend/memes-frontend/`). No backend, no `shared/schemas`, no `backend_api.md` change.
Assumes the components of `docs/superpowers/specs/2026-11-18-design-system-and-card-redesign.md` exist in `src/ui/`: `Button`, `ButtonLink`, `Badge`, `Card`, `Skeleton`, `EmptyState`, plus the redesigned `MemeCard` and the semantic tokens.
Out of scope: header search and the nav rework (item 8; this spec only adds one "Home" entry), the Reply page's own empty state (F8's page-internal part), the Explore page, dark mode, Android.

## Decisions (owner-approved 2026-11-19)

The owner approved the spec with every recommended default of section 11:

1. Rail order: Recently added first, Trending second.
2. Home on both stages; the owner's `/` on dev is Home too (Explore stays one click away).
3. The newest-12 rail stays "newest", not a random "Discover" rail; revisit after seeing real data.
4. `DEFAULT_ROUTE` for hidden routes becomes `/`.
5. Neutral public wording of the description line (the owner may edit it).
6. "Your last searches" lives in per-browser `localStorage`, clearable, never sent to a server.
7. No "N memes" count, therefore no new endpoint.
8. Reply shortcut unchanged on prod: it follows `visibility.ts` (hidden today).

Addendum (plan-time code reading, 2026-11-19, no database): `GET /api/images` without `q` (`ImageRepository.search`, `_build_filtered_ids_query`) filters `status == "active"` and orders by `created_at desc, id desc`, but does NOT exclude flagged images (`image_extras.flagged` is only outer-joined and returned as `Meme.flagged`). Trending does exclude them. So flagged memes can appear in Recently added. The plan filters `meme.flagged` out of that rail on the client; whether to also change the backend (a `record`-like param or a default filter) is an owner decision, not part of this spec.

## 1. Problem and facts verified in the code

1. `router.tsx`: `{ path: "/", element: <Navigate to="/explore" replace /> }`. A visitor lands in the infinite grid of Explore with no search box, no explanation and no entry per intent (audit F2).
2. Every endpoint a Home needs already exists and is served on prod (`Backend/app/stage_policy.py`):
   - `GET /api/images` without `q` is the recency feed (`backend_api.md`: "Without `q` they are ordered by recency"). It is the browse feed, so it records no impressions and, on prod (`history.record_browse: false`), no history row. `record=false` also suppresses both. READER.
   - `GET /api/images/trending` (READER); `window=all` is gated by `search.trending.min_popularity` (default 2), so on a young or prod library it can return 0 to 2 memes (audit F9).
   - `GET /api/concepts` (READER, `concepts` is shown on prod). `POST /api/meme-from-text/search` is READER but the `reply` feature is not in `SHOWN_ON_PROD`, so it is hidden there today.
   - `GET /api/history` is **DEV_ONLY** (decision 10) and is **global**, not per viewer (the app has no accounts). `GET /api/diagnostics/statistics` is DEV_ONLY.
3. `/search?q=` already works: `SearchPage` reads `q` through `parseSearchParams`.
4. `visibility.ts` has `DEFAULT_ROUTE = "/explore"`, the target for any hidden route (`FeatureRouteGuard`). `AppLayout` has a `Find` menu with Search, Reply, Recommendations, Explore, Trends, Trending memes; there is no brand and no Home link.
5. `MemeCard` takes a click handler (no router dependency); `ExplorePage`/`TrendingMemesPage` open a details modal or navigate to `/memes/:id`.

## 2. Approaches considered

| | A. Search-first minimal | B. Dashboard | C. Hero plus rails (recommended) |
|---|---|---|---|
| What | A centred search box, one sentence, three example chips. Nothing else. | Stat tiles (counts), recent searches, trending, recent, pending-review counts, reply box, quick links. | Search hero with the one-line orientation and examples, then at most two content rails (Recently added, Trending) and a slim Reply shortcut. |
| For | Fastest, zero new data, Google-like focus. Strong on "recognition over recall" and minimalism. | Owner sees workstation state. | Fixes the "empty screen" with real content while keeping one primary action. Streaming and photo sites (Netflix, Google Photos, Unsplash, Giphy) lead with content rails under a search; a first-time friend sees what the site holds before typing. |
| Against | A first-time visitor sees an almost empty page again: the exact complaint (F2). A search box alone does not show the corpus. | Violates the 3-4 widget limit and Nielsen #8 (aesthetic and minimalist design). Statistics and history are DEV_ONLY, so prod would be a different, emptier page. Needs per-stage code paths. | Two extra requests per visit (cheap). Needs per-widget empty and error states. |

Recommendation: **C**. Industry basis: Nielsen #1 (visibility of system status: skeleton and honest empty states), #6 (recognition rather than recall: example searches and visible memes instead of a blank box), #7 (flexibility: the box for experts, rails for browsers), #8 (minimalist: one primary action, at most two rails, the shortcut is a single line); content-first rails with progressive disclosure ("See all" leads to the full list) as used by streaming and photo sites. A is the shape C degrades into when the rails have nothing to show (section 5).

## 3. Page design

Route `/` renders `HomePage` inside `AppLayout` (same container `mx-auto max-w-6xl px-4`). Four blocks, in this order; only block 1 is mandatory.

| # | Block | Role | Shown when |
|---|---|---|---|
| 1 | Search hero: heading, one-line "what is this", large search box, chip row | the one primary action | always |
| 2 | Recently added (rail) | content, freshness | has >= 1 meme; otherwise hidden (empty library: section 4.2) |
| 3 | Trending (rail) | content, popularity | has >= 3 memes after dedupe (a rail with 1 or 2 looks broken: F9); otherwise hidden |
| 4 | Reply shortcut (one slim card, not a rail) | secondary intent | `useFeature("reply")` true |

"Continue where you left off" is a chip row inside block 1, not a fifth widget (4.1.3).

### 3.1 Wireframe, desktop (>= 1024 px, container 1152 px)

```
+----------------------------------------------------------------------------------+
| Home  Find v  Library v  Cleanup v  Admin v                       (existing nav) |
+----------------------------------------------------------------------------------+
|                                                                                  |
|            Find the right meme for any moment                    <h1>             |
|   Search the meme library by the words on a meme, what it shows, or a feeling.   |
|                                                                                  |
|   +------------------------------------------------------------+ +----------+   |
|   | (o) Search memes...                                        | | Search   |   |
|   +------------------------------------------------------------+ +----------+   |
|   Try:  [ cats ]  [ monday ]  [ work ]      or: Your last searches [pizza] [x]   |
|                                                                                  |
|   +-- Reply with a meme (dev only) ----------------------------------------+     |
|   |  Got a message to answer? Paste it and get matching memes.   [ Open ]  |     |
|   +------------------------------------------------------------------------+     |
|                                                                                  |
|   Recently added                                              See all  >         |
|   +------+ +------+ +------+ +------+ +------+ +------+                          |
|   | card | | card | | card | | card | | card | | card |   6 visible, rail        |
|   +------+ +------+ +------+ +------+ +------+ +------+   scrolls to 12          |
|                                                                                  |
|   Trending  (Memes that keep coming back.)                    See all  >         |
|   +------+ +------+ +------+ +------+ +------+ +------+                          |
|   | card | | card | | card | | card | | card | | card |                          |
|   +------+ +------+ +------+ +------+ +------+ +------+                          |
+----------------------------------------------------------------------------------+
```

The Reply shortcut sits directly under the chips because it is the second intent ("find" vs "reply"), costs one line of height, and must not push the rails below the fold on a laptop.

### 3.2 Wireframe, phone (360 to 430 px, 16 px gutters)

```
+--------------------------------+
| nav (item 8 reworks it)        |
+--------------------------------+
| Find the right meme            |
| for any moment                 |
| Search the library by words,   |
| picture or feeling.            |
| +----------------------------+ |
| | (o) Search memes...        | |   h 48, full width
| +----------------------------+ |
| [ Search ]                     |   full width Button, h 44
| Try: [cats] [monday] [work]    |   chips wrap, h 44
| +----------------------------+ |
| | Reply with a meme   [Open] | |   dev only
| +----------------------------+ |
| Recently added     See all >   |
| +-------+ +-------+ +--       |   2 cards plus a peek of the third
| | card  | | card  | | ca      |   (signals "scroll"), scroll-snap-x,
| +-------+ +-------+ +--       |   no arrows
| Trending           See all >   |
| +-------+ +-------+ +--       |
+--------------------------------+
```

### 3.3 Layout rules

- One `h1`; each rail title is an `h2`.
- Search form: `<form role="search">`, a native `<input type="search" name="q" aria-label="Search memes">` and a submit `Button`. Submit calls `navigate("/search?q=" + encodeURIComponent(q))`; a blank submit goes to `/search` (the browse list), never a no-op. Input height 48 px, font size 16 px (stops iOS Safari zooming on focus). No `autoFocus` on touch devices (the keyboard would cover the rails); on a fine pointer (`matchMedia("(pointer: fine)")`, read in an effect, StrictMode safe) focus the input on mount.
- 8pt grid and tokens of the design-system spec; no raw colours.
- Rail: flex row, `overflow-x-auto`, `scroll-snap-type: x proximity`, fixed card width per breakpoint so the row height is constant (no layout shift between skeleton and content): `w-40` below `sm`, `w-44` from `sm`, and six across on `lg` (matches the 6 columns of `MemesList`). Keyboard: plain tab sequence over the cards; the browser scrolls a focused card into view.
- At most 12 cards per rail. "See all" is a text `ButtonLink` to `/explore` (Recently added) and `/trending-memes` (Trending).
- Card click is the same as in the lists (whole card links to `/memes/:id`, design-system spec 2.3). No curator actions are passed to the cards: Home is a showcase, not a work list.

## 4. Widgets

### 4.1 Block 1: Search hero, orientation, examples, last searches

#### 4.1.1 Orientation line

Static text, shown on every visit: "Search the meme library by the words on a meme, what it shows, or a feeling." Not hidden after the first visit: hiding it from localStorage state gives different layouts for the same URL (Nielsen #4) and fails for visitors with cleared storage; it costs one line. Progressive disclosure is applied to the chips instead (4.1.3). A "N memes" count is omitted: `GET /api/diagnostics/statistics` is DEV_ONLY and there is no public count; a new endpoint is not worth it (open question 7).

#### 4.1.2 Three example searches that work on the real data

Hard-coded examples are wrong for a corpus the code cannot see, and a chip that returns nothing is the worst first impression. Derive and verify:

1. **Candidates.** From the memes the two rails already fetched (up to 24, no extra request) take each meme's `tags`; keep tags whose `name` is a single token (`/^[\p{L}\p{N}]{3,24}$/u`; `GET /api/images?q=` ANDs the lemmas of every token, each matched against OCR lemmas or tag values, so a multi-word tag name is not guaranteed to match as one tag). Count occurrences, sort by count then name (stable).
2. **Verify.** For the top 6 candidates call `searchMemes({ query, limit: 3, record: false })` in parallel (abortable) and keep the first three that return 3 items. `record: false` keeps these probes out of history and impressions. Cache the verified list in `sessionStorage` (`home.examples.v1`, try/catch, validated on read: at most 3 short strings) so returning to Home costs nothing.
3. **Fallback.** Fewer than 2 verified: render no example chips and no "Try:" label.
4. Works identically on dev and prod (READER endpoints only). Chips are `ButtonLink`s to `/search?q=<word>`.

Rejected source: `GET /api/concepts`. Concept names are not guaranteed to be matchable single tokens, and it is one more request for nothing the tags in memory do not give.

#### 4.1.3 "Continue where you left off": decision

No server-side history. A small per-browser "Your last searches" chip row from `localStorage`.

- `GET /api/history` is DEV_ONLY, so it cannot exist on prod, and it is global: one history for everyone using that backend. On dev it would present the partner's searches as "yours"; on a friends' prod it would be a privacy leak even if allowed. A reliable per-viewer source is browser storage.
- `src/home/recentSearches.ts`: `readRecentSearches()`, `rememberSearch(q)`, `clearRecentSearches()`. Key `home.recentSearches.v1` in `localStorage`; at most 5 distinct (case-insensitive) entries, newest first, trimmed, 64 chars max; every access in try/catch (storage can throw or be empty: the row just does not render). Written by the Home form and by `SearchPage` when a non-blank query is committed, so searches made elsewhere count. Nothing is sent to a server.
- Rendering: only when non-empty; labelled "Your last searches", with a "Clear" text button (user control). Progressive disclosure: with stored searches the row replaces the "Try:" row (one chip row in the hero); a first-time visitor sees the examples.
- Remembering scroll position or the last opened meme is out of scope (unreliable).

#### 4.1.4 States

| State | Behaviour |
|---|---|
| Loading | Hero is static and renders at once. The chip row reserves one line (44 px) with a `Skeleton` of three pills until verification ends: no layout shift. |
| Empty | Fewer than 2 verified examples and no stored searches: the row is not rendered. |
| Error | Verification failures equal the empty case (silent). Never blocks the search box. |

Prod and dev: same.

### 4.2 Block 2: Recently added

- **Data source:** existing `MemesApi.searchMemes({ limit: 12, record: false })` = `GET /api/images?limit=12&record=false`, no `q`, no facets: the recency feed. READER on prod. No new endpoint, no schema change. The browse feed records nothing already; `record=false` is passed so a future change to that rule cannot make Home visits count as searches (the comics editor does the same).
- **Verify during implementation, not assumed:** that the unfiltered feed excludes flagged and non-`active` images and orders by `created_at` descending (check the repository code, assert it with a mocked API test, record the answer as an addendum here). Flagged images on a public Home would be a blocker.
- **Caveat:** an ingestion run promotes many images at once, so the 12 newest are usually one batch. Acceptable for "what is new"; alternatives in open question 3.
- **Loading:** 6 `Skeleton` cards of the final size, `aria-busy="true"` on the section. **Empty (0 items):** section not rendered; if Trending is also empty Home shows an `EmptyState` below the hero: "No memes yet." plus, on dev only (`useFeature("upload")`), a `ButtonLink` "Upload memes" to `/upload`; on prod "Nothing here yet, check back soon." **Error:** compact `EmptyState` in the section: "Could not load recent memes." with a `Button` "Try again" that refetches this rail only. Rails fail independently.
- **Prod vs dev:** identical. **Responsive:** 3.3.

### 4.3 Block 3: Trending

- **Data source:** existing `MemesApi.getTrendingMemes(12, undefined, "all")`. READER. Window `all`, not `30d`: `30d` is empty whenever nothing was absorbed recently and needs a `windowGain` explanation; `all` is the stable "popular" list. Server eligibility (`min_popularity`) unchanged.
- Same `MemeCard`; the popularity badge of the redesigned card plus the rail subtitle "Memes that keep coming back." explain the word (F9).
- **Dedupe:** entries already in Recently added are dropped. Fewer than 3 left: the rail is hidden.
- **Loading / error:** as 4.2. **Empty or < 3:** not rendered, no "nothing trending" message on Home (the full page keeps its own empty state, ranking item 4).
- **Prod vs dev:** identical. On a small prod library the rail will often be hidden: intended degradation.

### 4.4 Block 4: Reply shortcut

- **Data source:** none. Static `Card`: title "Reply with a meme", one line "Paste a message you want to answer and get matching memes.", `ButtonLink` "Open Reply" to `/reply`. No embedded textarea: the Reply page owns its state, sessionStorage persistence and parser calls; a second entry would duplicate them.
- **Visibility:** `useFeature("reply")`. `reply` is not in `SHOWN_ON_PROD`, so on a `VITE_STAGE=prod` build the card is not rendered; on dev it always shows (dev ignores capabilities). If reply is later enabled for prod the shortcut follows `visibility.ts` with no change. The `it` environment has no parser: the Reply page already explains it; no special case here.
- **States:** none (static). **Responsive:** one row from `sm` (text left, button right); stacked on phones with a full-width button.

### 4.5 Deliberately not on Home

Statistics tiles, pending-review counts, recommendations, history, news trends, a feature tour, auto-advancing carousels. Each is DEV_ONLY, a fifth widget, or motion that conflicts with Nielsen #7/#8.

## 5. Whole-page states

| Situation | Page |
|---|---|
| All fetches succeed | Hero, Recent, Trending (if >= 3), Reply (dev) |
| Trending hidden | Hero, Recent, Reply: still a full page |
| Both rails empty, no errors | Hero plus `EmptyState` (this is approach A) |
| Both rails error | Hero plus two error blocks, each with its own retry |
| Slow network | Skeleton rails; the search box works at once |
| Backend down | Both rails show errors; the box still navigates (Search shows its own error) |

Fetching follows the repo's StrictMode and React Compiler rules: effects survive a double mount (a started-guard like `TrendingMemesPage`, or `AbortController` plus a request counter), refs only in handlers and effects. A hook `useHomeFeeds(memesApi)` returns `{ recent, trending }`, each `{ status: "loading" | "ready" | "error", items }`, and `retry(name)`.

## 6. Routing and navigation changes

1. `router.tsx`: replace `{ path: "/", element: <Navigate to="/explore" replace /> }` with `{ path: "/", element: <HomePage memesApi={memesApi} /> }`; remove the `Navigate` import if unused elsewhere in the file (it is not).
2. `/explore` and its `Find > Explore` menu entry stay. Explore is the only place for the unfiltered windowed full library, and "See all" of Recently added leads there. On prod nothing changes for it (browsing is never hidden); bookmarks keep working.
3. `AppLayout.tsx`: add one leading top-level `Home` link (`to: "/"`, `end: true`) before the groups; a plain link, not a `list` page (no `handleListNavClick`). Minimal answer to "no way back"; the brand and nav rework belong to item 8, which replaces it with a logo link.
4. `visibility.ts`: `DEFAULT_ROUTE` from `"/explore"` to `"/"`, so a hidden route (a friend opening `/statistics` on prod) lands on the front door. Update the assertions in `visibility.test.ts` and `FeaturesProvider.test.tsx` that expect `/explore`. `featureForPath("/")` is `null`, so there is no loop.
5. Prod: `/` is served on both stages. All endpoints are READER: no `stage_policy.py` change, no `make_router`, no `ROUTE_POLICY` line, no new `Feature`, no Android `FeatureVisibility.kt` entry (Android is out of scope). No new endpoint is needed; if implementation finds one is, stop and ask the owner (it would break the frontend-only decision).

## 7. Files expected to be touched (frontend only)

| File | Change |
|---|---|
| `src/pages/HomePage.tsx` (new) | page, whole-page states |
| `src/home/useHomeFeeds.ts` (new) | the two fetches, statuses, retry |
| `src/home/examples.ts` (new) | `pickExampleCandidates`, `verifyExamples`, sessionStorage cache |
| `src/home/recentSearches.ts` (new) | read, remember, clear, all try/catch |
| `src/home/Rail.tsx` (new) | titled scroll-snap rail with "See all", skeleton and error slots |
| `src/home/SearchHero.tsx` (new) | form, heading, sentence, chip rows |
| `src/app/router.tsx` | `/` route |
| `src/app/AppLayout.tsx` | Home link |
| `src/features/visibility.ts` | `DEFAULT_ROUTE = "/"` |
| `src/pages/SearchPage.tsx` | `rememberSearch` on a committed non-blank query |
| `src/ui/*` | consumed only; a missing variant (compact `EmptyState`, text `ButtonLink`) is added there, not in `home/` |
| tests next to each file | section 8 |

Not touched: `Backend/`, `backend_api.md`, `shared/schemas/`, generated types, Android. `MemesApi` already has `searchMemes` and `getTrendingMemes`.

## 8. Tests

Vitest and Testing Library, `MemoryRouter`, a fake `MemesApi`, no network.

1. `HomePage`: one `h1`; a search form; submitting "cat" navigates to `/search?q=cat`; blank submit to `/search`; `a&b c` becomes `q=a%26b%20c`.
2. Rails: both resolved gives both headings and 12 cards each; "See all" links point to `/explore` and `/trending-memes`; trending drops ids present in recent; fewer than 3 left hides the Trending heading; 0 and 0 shows the `EmptyState`.
3. Independence: recent rejects, trending resolves: error block with "Try again" in recent only; clicking refetches once and renders cards. The recent call is `searchMemes` with `{ limit: 12, record: false }` and no `query` (exact request asserted).
4. Loading: skeleton cards present, `aria-busy="true"`, input usable.
5. Reply card: rendered on dev; absent when `FeaturesContext` stage is `prod`, also when `capabilities.memeFromText` is true (build flag wins, as in `visibility.test.ts`).
6. Examples: candidates ignore multi-word and short tags, rank by frequency then name; verification keeps words whose probe returned 3 items, calling `searchMemes` with `record: false`, `limit: 3`; fewer than 2 gives no chip row; a valid cache is reused without probes; a corrupt cache is ignored.
7. Recent searches: case-insensitive dedupe, newest first, cap 5, cap 64 chars, "Clear" empties; with `localStorage` throwing Home renders without the row and without an error; chips link to `/search?q=`.
8. Routing: `/` renders Home (no redirect); "Home" in `AppLayout` is current only on `/`; `/statistics` on prod redirects to `/`; `visibility.test.ts` updated for `DEFAULT_ROUTE`.
9. StrictMode: mounting under `<StrictMode>` issues each feed request once.
10. Gates: `tsc -b`, `eslint src/` with 0 warnings, `vitest run`; `git diff Frontend/memes-frontend/src/types/generated/` empty.

## 9. Acceptance criteria

1. `/` on dev and on a `VITE_STAGE=prod` build shows the page of section 3 with no redirect; `/explore` still works.
2. One primary action: a search box at least 48 px high, focused on desktop only; Enter or the button goes to `/search?q=<text>`.
3. At most four blocks and two rails; on 1366x768 the hero and the start of the first rail are visible; on a 390x844 phone the hero, chips and top of the first rail are visible, with no horizontal page scroll.
4. A first-time visitor sees the one-line description and up to three example chips, each returning at least 3 results at verification time; fewer than two verified means none shown.
5. A returning visitor with stored searches sees "Your last searches" (max 5, clearable); with storage blocked the page works.
6. Recently added shows the 12 newest unflagged active memes and records no history or impression (rule verified, 4.2).
7. Trending shows only with at least 3 distinct memes after dedupe, otherwise absent without a message.
8. The Reply card is absent on prod and present on dev.
9. Each rail has loading (no layout shift), empty (hidden) and error (retry) behaviour, independently.
10. A hidden route on prod (for example `/statistics`) lands on `/`.
11. No backend, schema or `backend_api.md` change; Home never calls a DEV_ONLY endpoint (`/api/history`, `/api/diagnostics/statistics`); a test asserts it.
12. Keyboard order: box, button, chips, Reply, rail cards; visible focus ring; contrast per the design-system tokens.

## 10. Risks

- Flagged leak onto a public Home: the 4.2 verification is mandatory.
- Auto-derived chips can surface an odd word: mitigated by single-token, frequency and verified-count filters; no manual override list is built.
- Repeated fetches Home to Search to Home: small calls; examples cached in `sessionStorage`.
- Dependency: needs the design-system components; the plan should order Home after them, otherwise the implementer uses existing classes and a follow-up restyles.

## 11. Open questions for the owner (with recommended default)

1. Rail order: Recently added first or Trending first? Default: Recently added first (changes with every ingestion), Trending second.
2. Should the owner's own `/` on dev stay Explore, with Home only on prod? Default: no, Home on both stages; Explore is one click away; one behaviour is easier to keep correct.
3. If the newest 12 are always one ingestion batch, is that fine, or use a random `recommendations` rail ("Discover")? Default: keep the newest; revisit after seeing real data.
4. Change `DEFAULT_ROUTE` for hidden routes to `/` instead of `/explore`? Default: yes.
5. Public wording of the description line (assumes a neutral "meme library")? Default: the neutral text above; the owner edits it.
6. Is per-browser `localStorage` acceptable for "Your last searches", given dev has a global server history? Default: yes, local only, clearable, never sent to a server.
7. Show "N memes" in the description? Needs a new public READER count endpoint. Default: no.
8. If reply is later enabled for friends, anything to change? Default: no, the shortcut already follows `visibility.ts`.
