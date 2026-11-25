# Meme detail quick-look Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The meme detail shows the image beside a tabbed info panel, and the modal lets a person step through the opened-from list with the arrow keys.

**Architecture:** A new `Tabs` primitive in `src/ui/`; `MemeDetails` split into an extracted `MemeImageViewer` plus a tabbed panel (all panels stay mounted, inactive ones hidden, so fetching is unchanged); `Modal` gets opt-in `size`/`titleHidden`; `MemeDetailsModal` takes optional `neighbors` and the three list hosts supply them.

**Tech Stack:** React 19 (StrictMode, React Compiler lint rules), TypeScript, Vitest + Testing Library, Tailwind tokens in `src/ui/`.

**Spec:** `docs/superpowers/specs/2026-11-23-meme-detail-quick-look-design.md`

## Global Constraints

- Frontend only (`Frontend/memes-frontend/`); no backend, no API, no `backend_api.md` change.
- No change to data fetching: every `useFetchById` call and every on-mount fetching block keeps fetching on mount, once per meme (also in StrictMode). Inactive tab panels are mounted and hidden with the `hidden` attribute, never unmounted.
- Arrow-key meme navigation is ignored in `input`, `textarea`, `select`, `contenteditable`, when focus is on an element with `role="tab"`, and with Ctrl, Meta or Alt held.
- Tabs: Info (default), Similar, Stats, Curate (only when `useFeature("curator")`). Info = id/file name line, text lines, descriptions (+ curator approve/reject buttons), tags. Similar = Visual/Semantic toggle + similar grid. Stats = popularity, saves, impressions, comics block (only with the `comics` feature), concepts table (only when there are concepts). Curate = description note + flag toggle. `MemeActions` (layout `row`) sits above the tabs, always visible.
- `Modal` defaults (size and visible title) stay exactly as today; the new behaviour is opt-in through `size="wide"` and `titleHidden`. `MemesDuplicatesList` gets no `neighbors`.
- Page heading `Meme {id}` in `MemePage` stays. Similar-memes cards keep navigating to `/memes/{id}`.
- Existing tests keep passing; where content moved behind a tab, update that test minimally (click the tab first) and say so in the commit body.
- Gate before every commit, from `Frontend/memes-frontend/`: `pnpm exec tsc -b`, `pnpm exec eslint src/ --max-warnings 0`, `pnpm exec vitest run`. Commit messages end with the two attribution lines from the session reminder (Co-Authored-By and Claude-Session). Subagents never bind ports 5173-5175 / 8081-8083, never read `environments/.env.*`.

## Review Focus

- Left/Right while a tab is focused move between tabs and never change the meme; typing in the description note never changes the meme.
- A hidden (inactive) panel is still mounted: `getTopConceptsForImage`, `getDescriptions`, `similarMemes` (per source), popularity/saves/impressions each still fire exactly once per mount and once more only when the meme changes.
- Changing the meme (arrow keys) resets the tab to Info, clears stale note text and never shows the previous meme's counts (the existing "never shows the previous meme" tests keep their meaning).
- At the first and last item the missing direction's button is disabled and its key does nothing; an evicted/unknown opened meme shows no arrows.
- A prod-like build (curator off) shows no Curate tab and no curator controls anywhere in the panel.
- The wide modal on a narrow screen stays usable (single scrolling column, close button reachable).
- Other `Modal` users render exactly as before.

---

### Task 1: `Tabs` primitive

**Files:**
- Create: `Frontend/memes-frontend/src/ui/Tabs.tsx`, `Frontend/memes-frontend/src/ui/Tabs.test.tsx`
- Modify: `Frontend/memes-frontend/src/ui/index.ts` (export `Tabs`, `tabId`, `panelId`)

**Interfaces:**
- Produces: `Tabs(props: { tabs: { id: string; label: string }[]; value: string; onChange: (id: string) => void; idPrefix: string; "aria-label"?: string })` rendering only the tablist; `tabId(idPrefix: string, id: string): string` and `panelId(idPrefix: string, id: string): string` so the caller renders `role="tabpanel"` elements with `id={panelId(...)}`, `aria-labelledby={tabId(...)}` and `hidden` when inactive.

- [ ] **Step 1: Write failing tests** in `Tabs.test.tsx`: `renders a tablist with one tab per entry and marks the selected one` (roles, `aria-selected`, `aria-controls` equals `panelId`); `only the selected tab is tabbable` (`tabindex` 0 vs -1); `click selects` (`onChange` called with the id); `ArrowRight and ArrowLeft move to the next and previous tab and wrap` (focus moves and `onChange` called); `Home and End jump to the first and last tab`; `tab buttons are type=button`.
- [ ] **Step 2: Run to see them fail:** `pnpm exec vitest run src/ui/Tabs.test.tsx`.
- [ ] **Step 3: Implement** `Tabs.tsx` as specified (roving tabindex; keyboard handler on the tablist calls `onChange` and moves focus to the new tab via refs inside the handler; token styling, selected tab marked with the accent underline, `pointer-coarse:min-h-11`). The key handler must `stopPropagation` for Left/Right/Home/End so a document-level listener does not also act.
- [ ] **Step 4: Run** `pnpm exec vitest run src/ui` -> pass.
- [ ] **Step 5: Gate and commit** `feat(ui): accessible Tabs primitive`.

---

### Task 2: Two-column `MemeDetails` with tabs

**Files:**
- Create: `Frontend/memes-frontend/src/components/MemeImageViewer.tsx`
- Modify: `Frontend/memes-frontend/src/components/MemeDetails.tsx`, `Frontend/memes-frontend/src/components/MemeDetailsModal.tsx`, `Frontend/memes-frontend/src/components/Modal.tsx`, `Frontend/memes-frontend/src/pages/MemePage.tsx` (only if its markup needs the `layout` prop or a width change)
- Test: `Frontend/memes-frontend/src/components/MemeDetails.test.tsx`, `Frontend/memes-frontend/src/components/Modal.test.tsx`, `Frontend/memes-frontend/src/pages/MemePage.test.tsx` (existing, plus new cases)

**Interfaces:**
- Consumes: `Tabs`, `tabId`, `panelId` from `../ui` (Task 1).
- Produces: `MemeImageViewer(props: { src: string; alt: string })` (zoom/pan/controls moved out of `MemeDetails` unchanged); `MemeDetails` props gain `layout?: "modal" | "page"` (default `"page"`); `Modal` props gain `size?: "default" | "wide"` and `titleHidden?: boolean`; `MemeDetailsModal` passes `layout="modal"`, `size="wide"`, `titleHidden`.

- [ ] **Step 1: Write failing tests** in `MemeDetails.test.tsx` (new `describe('panel tabs')`): `shows Info content by default` (text lines, descriptions, tags visible; Similar/Stats/Curate content not visible via `toBeVisible`/the `hidden` panel); `Similar tab shows the toggle and the grid`; `Stats tab shows popularity, saves and impressions`; `Curate tab exists only with the curator feature` (mock `useFeature` like the file's existing curator tests) and holds the note textarea and the flag toggle; `hidden panels stay mounted: similarMemes and getTopConceptsForImage are each called once without opening any tab`; `the tab resets to Info when the meme changes` (rerender with another meme after selecting Stats); `omits the concepts table header when there are no concepts`. In `Modal.test.tsx`: `size="wide"` applies the wide container class, `titleHidden` renders the heading with `sr-only` and keeps the close button, defaults unchanged.
- [ ] **Step 2: Run to see them fail:** `pnpm exec vitest run src/components/MemeDetails.test.tsx src/components/Modal.test.tsx`.
- [ ] **Step 3: Implement.** Extract `MemeImageViewer.tsx` (zoom state, wheel listener, drag, controls; unchanged behaviour). In `MemeDetails.tsx` keep all hooks and fetches where they are; render a grid `lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-6`, viewer column `lg:sticky lg:top-0` with the image limited to `max-h-[75vh]` and `object-contain`, panel column with `MemeActions` then `Tabs` and four `role="tabpanel"` containers (Info, Similar, Stats, Curate-if-curator) that are always rendered and use the `hidden` attribute when inactive. `layout="modal"` makes the two columns scroll independently at `lg` (the panel column `overflow-y-auto` with a max height of the modal body; the modal body itself `overflow-hidden` at `lg`); `layout="page"` lets the page scroll. Tab state is `useState` reset when `meme.id` changes (id-keyed child component or a keyed wrapper; avoid an effect that sets state synchronously). Hide the concepts table when `concepts.length === 0`. `Modal`: `size`/`titleHidden` per Global Constraints (close button top-right when the title is hidden). `MemeDetailsModal`: pass the new props.
- [ ] **Step 4: Update existing tests** that look inside a now-hidden panel (for example the `Save note`/`Clear note` button queries, the Semantic toggle queries, popularity/saves/impressions blocks) by opening the tab first; keep each assertion's meaning; list each changed test in the report and commit body. Run `pnpm exec vitest run src/components src/pages` -> pass.
- [ ] **Step 5: Gate and commit** `feat(details): two-column meme detail with tabbed info panel`.

---

### Task 3: Quick-look arrow navigation

**Files:**
- Modify: `Frontend/memes-frontend/src/components/MemeDetailsModal.tsx`, `Frontend/memes-frontend/src/components/MemesList.tsx`, `Frontend/memes-frontend/src/pages/TrendingMemesPage.tsx`, `Frontend/memes-frontend/src/pages/ReplyPage.tsx`
- Test: `Frontend/memes-frontend/src/components/MemeDetailsModal.test.tsx` (create), `Frontend/memes-frontend/src/components/MemesList.test.tsx`, `Frontend/memes-frontend/src/pages/TrendingMemesPage.test.tsx`, the existing ReplyPage test file (find with Glob `ReplyPage*.test.*`)

**Interfaces:**
- Consumes: `MemeDetailsModal` and `MemeDetails` from Task 2.
- Produces: `MemeDetailsModal` props gain `neighbors?: { prev?: Meme; next?: Meme; onPrev?: () => void; onNext?: () => void }`.

- [ ] **Step 1: Write failing tests.** `MemeDetailsModal.test.tsx`: `ArrowRight calls onNext and ArrowLeft calls onPrev`; `arrows are ignored while focus is in a textarea` (open the Curate tab with the curator feature mocked on and focus the note), `in an input`, `on a tab` (Left/Right move tabs, `onNext`/`onPrev` not called), and `with ctrl, meta or alt held`; `Previous and Next buttons call the callbacks and are disabled when a callback is missing`; `no neighbors prop shows no buttons and ignores arrows`; `preloads the next and previous images` (spy on `window.Image` or `HTMLImageElement` src assignment; asserts the neighbour image URLs from `memesApi.getImageUrl`); `Escape still closes`. Host tests: `MemesList` - opening a card then ArrowRight shows the next meme's details, `loadForward` is requested when stepping to one of the last two items while more exist, no arrows when the opened meme is not in the list; `TrendingMemesPage` and `ReplyPage` - ArrowRight/ArrowLeft step through their lists and the buttons are disabled at the ends.
- [ ] **Step 2: Run to see them fail:** `pnpm exec vitest run src/components/MemeDetailsModal.test.tsx src/components/MemesList.test.tsx src/pages`.
- [ ] **Step 3: Implement** the `neighbors` prop in `MemeDetailsModal` (document keydown listener in an effect with the guard from Global Constraints, calling `preventDefault` only when it acts; round Previous/Next buttons at the image area edges with `aria-label` `Previous meme`/`Next meme`; an effect preloading `neighbors.prev`/`neighbors.next` images through `new Image().src = memesApi.getImageUrl(meme)`). In `MemesList` derive the index of `selectedMeme` in `items` by id and pass `neighbors` with `onPrev`/`onNext` that call `setSelectedMeme(items[index ± 1])`; when stepping forward to index >= `items.length - 2` and `hasMoreForward`, call `loadForward()`; pass no `neighbors` when the id is not found. Do the same in `TrendingMemesPage` (over `items.map(entry => entry.meme)`, no auto load) and `ReplyPage` (over its result memes). Leave `MemesDuplicatesList` untouched.
- [ ] **Step 4: Run** `pnpm exec vitest run src/components src/pages` -> pass.
- [ ] **Step 5: Gate and commit** `feat(details): step through the grid with arrow keys in the quick-look`.

---

### Task 4: Verification and docs (controller)

**Files:**
- Modify: `docs/superpowers/specs/2026-11-18-ui-ux-audit.md` (Progress), `docs/superpowers/specs/2026-11-23-meme-detail-quick-look-design.md` (status -> `done`)

- [ ] **Step 1:** run the full frontend gate once more.
- [ ] **Step 2:** visual check on a throwaway vite server (untracked config proxying `/api` to the `general` backend on 8082, port 5190+, `VITE_BACKEND_API_URL` set to the server's own URL; never 5173-5175, 8081-8083): open Explore, click a card, check two columns, tabs, arrows (keyboard and buttons), the Curate tab, focus on tabs not moving the meme, the full page `/memes/<id>`, and the dead-end behaviour at the last item; use element lookups instead of pixel clicks where a click could write to the live database (flag toggle, saves, popularity set, note save); stop the server by its listening port afterwards and delete the temporary config.
- [ ] **Step 3:** update the audit doc Progress entry (item 7 done, Stats stays visible to everyone: a product decision for the owner, follow-ups from the reviews) and set the spec status to `done`; commit `docs(audit): mark meme detail quick-look (item 7) done`.
