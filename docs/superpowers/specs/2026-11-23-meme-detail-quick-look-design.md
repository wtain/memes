# Meme detail: two-column layout and quick-look browsing (UI/UX audit ranking item 7, I3 + I4)

Status: done
Plan: `docs/superpowers/plans/2026-11-23-meme-detail-quick-look.md`
Originates from: `docs/superpowers/specs/2026-11-18-ui-ux-audit.md` (ranking item 7, ideas I3 and I4, finding F6)

Approval: the owner chose option A ("keep the modal as a quick-look, two columns, arrows through the grid, same layout on the full page") and said "A, go ahead" in the session of 2026-11-23.

## Intent

The detail view is the screen friends open most. Today the image fills the first screen and everything else (IDs, text, descriptions, tags, popularity, saves, impressions, concepts table, similar memes) follows in one long column; looking at the next meme means closing the modal and clicking another card. The detail should show the meme big beside a compact, tabbed information panel, and a person should step through the memes of the grid with the arrow keys without leaving it.

## Goals

1. `MemeDetails` becomes two columns from the `lg` breakpoint up: the image (with its existing zoom and pan) on the left, an information panel on the right; below `lg` they stack (image, then panel).
2. The panel starts with the action row (`MemeActions`, unchanged) followed by tabs: **Info** (default: id and file name line, text lines, descriptions with the curator approve/reject buttons, tags), **Similar** (the Visual/Semantic toggle and the similar-memes grid), **Stats** (popularity, saves, impressions, the comics block when the `comics` feature is on, and the concepts table only when there are concepts), and **Curate** (only when the `curator` feature is on: description note and the flag toggle).
3. A small reusable `Tabs` primitive in `src/ui/` (accessible tablist: `role="tablist"`/`tab`/`tabpanel`, `aria-selected`, roving focus with Left/Right/Home/End on the tabs).
4. In the modal, when the host gives it neighbours, Left and Right arrow keys and two on-screen buttons show the previous and next meme of the list the person opened it from (Explore grid, Trending, Reply results); the list screens keep their scroll position because the modal never navigates. The next meme's image is preloaded.
5. The full page `/memes/:id` uses the same two-column layout.
6. The modal is wider for this layout and its title bar no longer takes a heading row (the title stays for screen readers).
7. The empty ID/Name concepts table header (finding F6) no longer shows when there are no concepts.

## Non-goals

- Changing what is fetched, when it is fetched, or any API (backend untouched; `backend_api.md` unchanged).
- Per-meme `<title>` or meta for the page, the page heading `Meme {id}` (tests rely on it), or the share page.
- Quick-look browsing in `MemesDuplicatesList` (a curator page: its modal keeps no arrows).
- Making the modal URL-addressable, a count like "3 of 36" (paging makes the total unknown), swipe gestures, a fullscreen image mode.
- Hiding the Stats tab on prod: popularity, saves and impressions stay visible to everyone as today; only the curator-gated controls inside them stay gated. (A product decision for the owner, noted in the audit doc.)
- The similar-memes cards still navigate to `/memes/{id}` (full page) as today.

## Anti-goals

- No change in data-fetching behaviour: every `useFetchById` call and every block that fetches on mount keeps fetching on mount, exactly once per meme (also in StrictMode). Inactive tab panels stay mounted and are hidden (the `hidden` attribute), they are not unmounted, so counts and tests keep their meaning.
- Arrow keys must never be hijacked while a person is typing or operating a control that uses them: not in `input`, `textarea`, `select`, `contenteditable`, not while focus is on a `tab` (Left/Right move between tabs there), and not with Ctrl, Meta or Alt held.
- No curator control becomes visible on a prod build: the `curator` gate on the Curate tab, the description feedback buttons and the popularity set control stays; a prod build without curator features shows no Curate tab.
- The existing `Modal` keeps working unchanged for its other users (`IngestionReviewPage` peek, `ConceptDetailsModal` and others): new behaviour is opt-in through props.

## Constraints

- React 19 StrictMode and the React Compiler ESLint rules: every effect survives a double mount, refs are touched only in handlers and effects.
- Frontend only, `Frontend/memes-frontend/`. Gate before every commit: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`.
- Tokens and components from `src/ui/` (`Button`, `Badge`, `Card`); the current tokens (`bg-scrim`, `text-fg` ...) are used instead of new colours.
- Existing tests keep passing; where content moved behind a tab the test is updated minimally (open the tab first) and the change is stated in the commit body.

## Approaches considered

Overall shape (chosen by the owner):
- A. Keep the modal as a quick-look with two columns and arrows (**chosen**).
- B. Drop the modal and use the full page with back-to-results (item 10): rejected for now, item 10 stays a separate small follow-up.

Panel organisation:
- Tabs with all panels mounted and hidden (**chosen**): same fetches as today, no layout jump, tests only need a tab click.
- Tabs that unmount inactive panels: saves a few requests but changes fetch timing and `key`/reset semantics of the blocks. Rejected.
- Collapsible sections in one scrolling column: still long, no gain over today. Rejected.

Where the neighbour list lives:
- The host (list screen) computes neighbours from its own list and passes `neighbors` to the modal (**chosen**): the modal stays dumb and reusable, and each list knows its own paging.
- A global "current list" store: more machinery for no benefit. Rejected.

## Design

### `src/ui/Tabs.tsx`

`Tabs` with props `tabs: { id: string; label: string }[]`, `value: string`, `onChange(id)`, `idPrefix: string`, plus children rendered by the caller as panels with the helper ids (`tabId(prefix, id)`, `panelId(prefix, id)` exported). Tab buttons are `type="button"`, `role="tab"`, `aria-selected`, `aria-controls`, `tabIndex` 0 only on the selected one; Left/Right wrap, Home/End jump; activation follows focus. Styling from tokens, touch target `pointer-coarse:min-h-11`. Exported from `src/ui/index.ts`.

### `MemeDetails` and its pieces

- Extract the zoom/pan image into `src/components/MemeImageViewer.tsx` with unchanged behaviour (Ctrl+wheel, +/- buttons, drag pan, controls fade); `MemeDetails` composes: viewer, then the panel.
- Layout: a grid `lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]` with a gap; the viewer column is `lg:sticky lg:top-0` with the image limited to the viewport height (`max-h-[75vh]`, `object-contain`); the panel column scrolls with the page on the full page and, in the modal, the two columns scroll independently (the modal body does not scroll as a whole at `lg`). Below `lg` everything is one scrolling column.
- A `layout?: "modal" | "page"` prop (default `"page"`) selects the scrolling behaviour; `MemeDetailsModal` passes `"modal"`.
- The panel's tab state is local and resets to Info when the meme changes (`key` or an id-keyed reset); the Similar source toggle keeps carrying over between memes as today.
- The concepts table renders only when `concepts.length > 0`.

### `Modal` and `MemeDetailsModal`

- `Modal` gains optional `size?: "default" | "wide"` (wide: `max-w-7xl`, nearly full height) and `titleHidden?: boolean` (the heading is `sr-only` and the visible header row is only the close button, placed top-right); defaults keep today's look. The scroll behaviour for `wide` is left to the content (`overflow-hidden` container at `lg`).
- `MemeDetailsModal` gains `neighbors?: { onPrev?: () => void; onNext?: () => void }`. When present: a `document` keydown listener (added in an effect) for ArrowLeft and ArrowRight calls `onPrev`/`onNext` unless the guard above applies; two round buttons `aria-label="Previous meme"` and `"Next meme"` at the left and right edge of the image area, disabled (and not shown as hover targets) when the callback is missing. Image of the next neighbour is preloaded with `new Image().src` in an effect (only when `onNext` exists and the host passes the neighbour meme: `neighbors.next?: Meme` for that purpose, same for `prev`).
- Escape and backdrop close stay as they are.

### Hosts

- `MemesList`: derives the index of the opened meme in its `items` (by id); `onPrev`/`onNext` set `selectedMeme` to the adjacent item; moving to one of the last two items while `hasMoreForward` calls `loadForward()` so the next arrow rarely dead-ends. If the opened meme is no longer in `items` (evicted), both arrows are absent.
- `TrendingMemesPage`: the same over its `items` (the memes of `items`); no auto "Show more".
- `ReplyPage`: the same over its result list.
- `MemesDuplicatesList`: unchanged (no `neighbors`).

## Tests

- `Tabs`: renders the roles and aria wiring; click and keyboard (Left/Right wrap, Home/End); only the selected tab is tabbable; panels are linked by id.
- `MemeDetails`: Info content is visible by default; Similar, Stats and Curate content live behind their tabs; Curate tab absent without the curator feature; a hidden panel is still mounted and fetches happened once (the existing once-per-mount tests keep passing); the concepts table header is absent without concepts; tab resets to Info on a new meme; the zoom controls still work (existing tests).
- `MemeDetailsModal`: ArrowRight/ArrowLeft call the neighbours; arrows are ignored with focus in a `textarea`, an `input`, on a `tab`, and with Ctrl/Meta/Alt; Previous/Next buttons disabled without a callback; absent `neighbors` shows no buttons and ignores the arrows; the next image is preloaded; Escape still closes.
- Hosts: `MemesList` steps through items, loads more near the end and shows no arrows for an evicted meme; `TrendingMemesPage` and `ReplyPage` step through their lists.
- `Modal`: default size and title unchanged; `size="wide"` and `titleHidden` behave as specified.

## Left to the builder

Exact class names and spacing inside the token set, the visual design of the arrow buttons, how the tab id helpers are named, how `MemeDetails` is split further if the file stays large, and which existing tests need a tab click.

## Decisions I made without asking (check these)

- Tab names Info, Similar, Stats, Curate and what sits in each.
- Stats (popularity, saves, impressions) stays visible to everyone on prod.
- Arrows follow the list as loaded in the screen; no total count.
- The page heading `Meme {id}` stays for now.
