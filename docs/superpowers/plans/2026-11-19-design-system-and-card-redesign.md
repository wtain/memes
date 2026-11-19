# Design System and Meme Card Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Tailwind 4 design tokens and a small hand-rolled `src/ui/` component set, then redesign `MemeCard` (link, hover/focus action bar, equal height, compact tags) and `MemesList` (skeletons, empty state, phone grid).

**Architecture:** Tokens live in `src/index.css` (`:root` vars + `@theme inline`). Components are plain function components with class-lookup variants, no new dependency. `MemeCard` is restructured so the image anchor and all controls are siblings; reveal is CSS-only (`group-hover`, `group-focus-within`, `pointer-coarse`).

**Tech Stack:** React, TypeScript, Tailwind 4 (`@tailwindcss/vite`), vitest + testing-library, pnpm.

**Spec:** `docs/superpowers/specs/2026-11-18-design-system-and-card-redesign.md` (read the "Decisions (owner-approved 2026-11-19)" block first; it overrides the alt-text rule).

## Global Constraints

- Work only inside `Frontend/memes-frontend/` (paths below are relative to it unless prefixed). Backend, `shared/`, `AndroidClient/`, `src/types/generated/`, `features/visibility.ts`, `app/router.tsx`, `tailwind.config.js` are not touched.
- No new entry in `package.json`; `package.json` and `pnpm-lock.yaml` must show an empty `git diff`.
- Edit files with the Edit/Write tools, never through PowerShell Get/Set-Content.
- Do not bind ports 5173-5175 or 8081-8083. No dev server is required; manual checks use `vite preview` on a free port such as 5199 only if the owner asks.
- React Compiler ESLint rules: no ref reads/writes in render, no effects in `ui/*`, no `useMemo`/`useCallback` added for performance.
- Curator gates stay `useFeature("curator")` and `comicsAction && useFeature("comics")`; never read `import.meta.env.VITE_STAGE` in the card. Simulate prod in tests with `FeaturesContext.Provider value={{ stage: "prod", capabilities: null }}` (from `src/features/useFeature.ts`).
- Semantic tokens only in new code (`bg-surface`, `text-fg-muted`); `text-gray-400` banned for text; motion only under `motion-safe:`; spacing multiples of 4px, no arbitrary values.
- `CardComicsAction` change must be its own commit (Task 8).
- Verification set, run from `Frontend/memes-frontend`: `pnpm exec tsc -b`, `pnpm exec eslint src/ --max-warnings 0`, `pnpm exec vitest run`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01R83wsKu7FzDMDHH3jYGJSe
  ```

## Review Focus

- Prod stage with `meme.flagged = true`: no badge, no ring, no empty action bar rendered (tests in Task 10).
- Meme with `text` undefined/empty: no OCR button, no `aria-describedby`, alt still "Meme" (Task 10).
- Meme with no tags and with 3+ tags: footer present in both, same wrapper (Tasks 6, 10).
- Ctrl/Meta/Shift/Alt/middle click on the image link must not call `onClick` nor `preventDefault` (Task 10).
- Card used with no `onClick` (e.g. a caller that only wants the link): plain click is left to the browser (Task 10).

## Task overview and parallelism

| Task | Subject | Depends on | Parallel? |
|---|---|---|---|
| 1 | Install dependencies | none | first, alone |
| 2 | Tokens in `index.css` + token test | 1 | sequential (blocks all) |
| 3 | `Button`, `ButtonLink`, icons | 2 | parallel with 4, 5, 6, 7 |
| 4 | `Badge` | 2 | parallel |
| 5 | `Card` | 2 | parallel |
| 6 | `Skeleton`, `MemeCardSkeleton` | 2 | parallel |
| 7 | `EmptyState` | 2, 3 | after 3 (uses `Button` only in a test) or run last of the group |
| 8 | `ui/index.ts` barrel | 3-7 | sequential |
| 9 | Modal scrim, `Tag`, `TagList` compact | 2, 4 | parallel with Task 10 only if it does not touch `MemeCard` files (it does not) |
| 10 | `CardComicsAction` compact (own commit) | 3, 8 | parallel with 9 |
| 11 | `MemeCard` redesign + tests | 3, 4, 5, 8, 9, 10 | sequential, shared files |
| 12 | `MemesList` + tests | 6, 7, 8, 11 | sequential after 11 |
| 13 | Final test sweep and acceptance | all | last |

Tasks 3 to 7 touch disjoint new files and can be dispatched in parallel after Task 2 (they only share the soon-created barrel, which Task 8 owns). Tasks 11, 12 and 13 edit shared files (`MemeCard.tsx`, `MemeCard.test.tsx`, `MemesList.tsx`) and must run strictly one after another.

---

### Task 1: Install dependencies in the worktree

**Files:** none changed (creates ignored `node_modules/`).

- [ ] **Step 1: Install with the lockfile frozen.** From `Frontend/memes-frontend`: `pnpm install --frozen-lockfile`. Use the maximum tool timeout (600000 ms), run in the foreground, never with `run_in_background` (a killed install leaves a corrupted tree, see the CLAUDE.md venv/timeout gotcha). If it times out, rerun the same command; do not delete `node_modules` partially.
- [ ] **Step 2: Confirm `pnpm-lock.yaml` and `package.json` are unchanged.** Run `git status --short -- package.json pnpm-lock.yaml`. Expected: no output. If the lock changed, `git checkout -- pnpm-lock.yaml` and report.
- [ ] **Step 3: Baseline.** Run the three verification commands. Expected: all pass (record any pre-existing failure verbatim; do not fix unrelated ones). No commit.

---

### Task 2: Design tokens

**Files:**
- Modify: `src/index.css` (currently one line, `@import "tailwindcss";`)
- Test: `src/ui/tokens.test.ts` (create)

**Produces:** utilities `bg-page bg-surface bg-surface-sunken bg-line ring-line border-line-strong text-fg text-fg-muted text-fg-subtle bg-brand hover:bg-brand-hover text-on-brand bg-danger text-on-danger ring-danger bg-success-bg text-success-fg bg-warning-bg text-warning-fg bg-danger-bg text-danger-fg bg-scrim ring-focus rounded-sm/md/lg shadow-card shadow-card-hover text-meta`.

- [ ] **Step 1: Write the failing test** `src/ui/tokens.test.ts` (read `src/index.css` with `fs.readFileSync(new URL("../index.css", import.meta.url), "utf8")`; vitest runs with css disabled so use text parsing):
  - `defines every light token in the dark block`: every `--c-*` name declared in the `:root {` block is declared in `:root[data-theme="dark"] {`, except `--c-scrim` (same in both) and `--c-line`'s ring use; assert the set difference is empty or only `--c-scrim`.
  - `maps every --color-* to an existing --c-*`: each `--color-x: var(--c-y)` in the `@theme inline` block has `--c-y` declared in `:root`.
  - `declares the global focus ring`: file contains `:focus-visible` and `var(--c-focus)`.
- [ ] **Step 2: Run** `pnpm exec vitest run src/ui/tokens.test.ts`. Expected: FAIL (tokens absent).
- [ ] **Step 3: Implement** `src/index.css` exactly as spec section 3.2 (five blocks: `:root`, `:root[data-theme="dark"]`, `@theme inline`, `@theme` for radius/shadow/`--text-meta`, `:focus-visible` rule), plus `body { background: var(--c-page); color: var(--c-fg); }`. Compute contrast ratios for fg, fg-muted, fg-subtle, brand on white, on-brand on brand, danger, the three tone pairs (light) and the dark values with a throwaway script in the scratchpad; record the ratios in a CSS comment. If a hex fails (text < 4.5:1, ring/icon < 3:1) adjust that hex and keep the names.
- [ ] **Step 4: Run** the test again. Expected: PASS. Then `pnpm exec vite build` is NOT required; instead run `pnpm exec tsc -b` and `pnpm exec eslint src/ --max-warnings 0`.
- [ ] **Step 5: Commit** `src/index.css src/ui/tokens.test.ts` as `feat(ui): design tokens in index.css`.

---

### Task 3: `Button`, `ButtonLink`, icons

**Files:**
- Create: `src/ui/Button.tsx`, `src/ui/icons.tsx`
- Test: `src/ui/Button.test.tsx`

**Produces (exact signatures):**
```ts
type BaseProps = { variant?: "primary"|"secondary"|"ghost"|"danger"; size?: "sm"|"md"|"lg"; pressed?: boolean; loading?: boolean }
type TextButtonProps = React.ComponentProps<"button"> & BaseProps & { iconOnly?: false }
type IconButtonProps = React.ComponentProps<"button"> & BaseProps & { iconOnly: true; "aria-label": string }
export function Button(props: TextButtonProps | IconButtonProps): JSX.Element
export function ButtonLink(props: React.ComponentProps<"a"> & { href: string } & Pick<BaseProps,"variant"|"size">): JSX.Element
// icons.tsx: FlagIcon, ComicsIcon, CheckIcon, TextIcon: (props: React.ComponentProps<"svg">) => JSX.Element
```

- [ ] **Step 1: Write failing tests** (`Button.test.tsx`): default `type="button"`; `type="submit"` override respected; each variant renders its token classes (`bg-brand`, `ring-line-strong`, `hover:bg-black/5`, `bg-danger`); `pressed` sets `aria-pressed="true"` and `pressed={false}` sets `"false"` while undefined sets no attribute; `loading` sets `disabled` and `aria-busy="true"`; `className="mt-2"` appended last; `sm` has `pointer-coarse:min-h-11`; icon-only without `aria-label` is a type error asserted with `// @ts-expect-error` on a `<Button iconOnly>x</Button>` usage; `ButtonLink` renders an `<a href>`; each icon is `aria-hidden="true"`.
- [ ] **Step 2: Run** `pnpm exec vitest run src/ui/Button.test.tsx`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** both files per spec 3.3 (lookup tables of class strings; sizes sm 32px/md 40px/lg 44px i.e. `min-h-8`/`min-h-10`/`min-h-11`, icon-only square with matching `min-w-`; disabled `disabled:opacity-50 disabled:pointer-events-none`; transitions only under `motion-safe:`). Icons: 20x20 viewBox inline SVG, `currentColor`.
- [ ] **Step 4: Run** the test file plus `pnpm exec tsc -b` (verifies the `@ts-expect-error`). Expected: PASS.
- [ ] **Step 5: Commit** as `feat(ui): Button, ButtonLink and icons`.

---

### Task 4: `Badge`

**Files:** Create `src/ui/Badge.tsx`; Test `src/ui/Badge.test.tsx`.

**Produces:** `export function Badge(props: React.ComponentProps<"span"> & { tone?: "neutral"|"success"|"warning"|"danger"|"brand" }): JSX.Element`

- [ ] **Step 1: Failing tests:** children render; default tone neutral uses `bg-line`-based neutral classes; `success` has `bg-success-bg text-success-fg`; `warning` and `danger` likewise (`bg-danger-bg text-danger-fg`); `brand` has a brand tint class; has `text-meta rounded-full`; not focusable (no `tabindex`); `title` passes through; className appended.
- [ ] **Step 2: Run** `pnpm exec vitest run src/ui/Badge.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** `Badge.tsx` (class lookup, `rounded-full px-2 py-0.5 text-meta font-medium`).
- [ ] **Step 4: Run** the test. Expected: PASS.
- [ ] **Step 5: Commit** as `feat(ui): Badge`.

---

### Task 5: `Card`

**Files:** Create `src/ui/Card.tsx`; Test `src/ui/Card.test.tsx`.

**Produces:** `export function Card(props: React.ComponentProps<"div"> & { padded?: boolean; interactive?: boolean }): JSX.Element`

- [ ] **Step 1: Failing tests:** base classes `rounded-lg bg-surface shadow-card ring-1 ring-line overflow-hidden`; `padded` adds `p-4`; `interactive` adds `hover:shadow-card-hover` and `motion-safe:transition-shadow`; neither flag by default; `data-testid`, `onClick`, `ref` props pass through; className appended; no class contains `border-black`.
- [ ] **Step 2: Run** `pnpm exec vitest run src/ui/Card.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** `Card.tsx`.
- [ ] **Step 4: Run** the test. Expected: PASS.
- [ ] **Step 5: Commit** as `feat(ui): Card`.

---

### Task 6: `Skeleton` and `MemeCardSkeleton`

**Files:** Create `src/ui/Skeleton.tsx`; Test `src/ui/Skeleton.test.tsx`.

**Produces:** `Skeleton(props: React.ComponentProps<"div">)`, `MemeCardSkeleton(): JSX.Element`

- [ ] **Step 1: Failing tests:** `Skeleton` has `aria-hidden="true"`, classes `rounded-md bg-line motion-safe:animate-pulse`, className appended; `MemeCardSkeleton` is `aria-hidden`, contains an `aspect-square` block, a footer block with `h-10` (same footer height as the card), and is not found by `getByRole("status")`.
- [ ] **Step 2: Run** `pnpm exec vitest run src/ui/Skeleton.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** `Skeleton.tsx` (`MemeCardSkeleton` wraps in `Card`'s classes by importing `Card`).
- [ ] **Step 4: Run** the test. Expected: PASS (needs Task 5 merged).
- [ ] **Step 5: Commit** as `feat(ui): Skeleton and MemeCardSkeleton`.

---

### Task 7: `EmptyState`

**Files:** Create `src/ui/EmptyState.tsx`; Test `src/ui/EmptyState.test.tsx`.

**Produces:** `EmptyState(props: { title: string; description?: string; action?: React.ReactNode; icon?: React.ReactNode; tone?: "empty"|"error" }): JSX.Element`

- [ ] **Step 1: Failing tests:** default `getByRole("status")`; `tone="error"` gives `getByRole("alert")`; `getByText(title)`; description and action (a `<Button>`) render when passed and are absent otherwise; icon wrapper is `aria-hidden`.
- [ ] **Step 2: Run** `pnpm exec vitest run src/ui/EmptyState.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** `EmptyState.tsx` (`py-12` centered, `text-fg` title, `text-fg-muted` description).
- [ ] **Step 4: Run** the test. Expected: PASS.
- [ ] **Step 5: Commit** as `feat(ui): EmptyState`.

---

### Task 8: `ui/index.ts` barrel

**Files:** Create `src/ui/index.ts` re-exporting `Button, ButtonLink, Badge, Card, Skeleton, MemeCardSkeleton, EmptyState` and the four icons.

- [ ] **Step 1: Write a failing test** `src/ui/index.test.ts` asserting each named export is defined.
- [ ] **Step 2: Run** it. Expected: FAIL.
- [ ] **Step 3: Implement** the barrel.
- [ ] **Step 4: Run** the verification set (tsc, eslint, full vitest). Expected: all pass.
- [ ] **Step 5: Commit** as `feat(ui): barrel export`.

---

### Task 9: Modal scrim, `Tag`, `TagList` compact

**Files:**
- Modify: `src/components/Modal.tsx:31` (`bg-black bg-opacity-60` becomes `bg-scrim`; this is the Tailwind 3 leftover the spec scopes, finding 6)
- Modify: `src/components/Tag.tsx`, `src/components/TagList.tsx`
- Test: `src/components/Modal.test.tsx`, `src/components/Tag.test.tsx`, `src/components/TagList.test.tsx`

**Produces:** `TagList` prop `variant?: "full" | "compact"` (default `"full"`, current behaviour untouched).

- [ ] **Step 1: Write failing tests.** Modal: backdrop element has class `bg-scrim` and not `bg-opacity-60`. TagList `variant="compact"`: at most 2 tags with score > 0.3; no `button` rendered; hidden tags produce one `+N` badge whose `title` lists their names; chip text is the tag name without `(source)`, chip `title` is `name (source)`; container has `flex-nowrap`; zero qualifying tags renders an empty container without error; default variant tests unchanged. Tag: still a `span` with `#` prefix.
- [ ] **Step 2: Run** `pnpm exec vitest run src/components/Modal.test.tsx src/components/Tag.test.tsx src/components/TagList.test.tsx`. Expected: new tests FAIL, old ones pass.
- [ ] **Step 3: Implement** the three files (`Tag` uses `Badge` neutral classes; compact chips are `max-w-24 truncate`; `+N` is a non-interactive `Badge`).
- [ ] **Step 4: Run** the three files, then the verification set. Expected: PASS.
- [ ] **Step 5: Commit** as `feat(ui): modal scrim token, compact TagList`.

---

### Task 10: `CardComicsAction` compact mode (own commit)

**Files:**
- Modify: `src/components/comics/CardComicsAction.tsx` (add optional prop `compact?: boolean`; only the render branch differs, no logic change; non-compact output stays identical)
- Test: `src/components/comics/CardComicsAction.test.tsx`

- [ ] **Step 1: Write failing tests** (existing tests keep their providers/mocks): with `compact`, `getByRole("button", { name: "Add to comics" })` exists (and `Add to ${working.name}` when a working comics is set), is icon-only (no visible text), and the add flow calls the same API as non-compact; success message renders inside an element with `role="status"`, an error inside `role="alert"`; both are absolutely positioned (class `absolute`) so layout height does not change; without `compact` the existing DOM assertions still pass.
- [ ] **Step 2: Run** `pnpm exec vitest run src/components/comics/CardComicsAction.test.tsx`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** the `compact` branch using `Button iconOnly variant="ghost"` and `ComicsIcon` from `src/ui`.
- [ ] **Step 4: Run** the file, then the verification set. Expected: PASS.
- [ ] **Step 5: Commit alone** as `feat(comics): compact icon mode for CardComicsAction`. This commit must touch no other file.

---

### Task 11: `MemeCard` redesign

**Files:**
- Modify: `src/components/MemeCard.tsx` (render only; props gain only `href?: string`)
- Test: `src/components/MemeCard.test.tsx`

**Consumes:** `Card, Badge, Button, FlagIcon, TextIcon` (Task 8), `TagList variant` (Task 9), `CardComicsAction compact` (Task 10).

**Behaviour to implement (spec 4.1 to 4.6, with decisions applied):**
- Root `<Card interactive>` with `group relative flex h-full flex-col`; flagged adds `ring-2 ring-danger` (curator only). No `border-black`/`border-red-500`.
- Image wrapped in `<a href={href ?? \`/memes/${meme.id}\`}>`; `onClick` handler: return unchanged when `showText || !onClick`, or `e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey`; else `e.preventDefault(); onClick()`.
- Image `alt="Meme"`; when `meme.text` has lines, a visually hidden element (`sr-only`, id from `useId`) holds the first lines joined and cut to 120 chars, and the img has `aria-describedby` pointing at it; otherwise no `aria-describedby`.
- Top-left persistent `Badge tone="danger"` "Flagged" when `curator && isFlagged`. Top-right action bar (`group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:opacity-100 opacity-0`, `bg-scrim` backing) rendered only when it has children: `role="group" aria-label="Curator actions"` containing the flag `Button iconOnly variant="ghost" pressed={isFlagged} aria-label="Flagged"` (curator only; same `markImageIsFlagged`/`unmarkImageIsFlagged` behaviour, state flips after the promise resolves) and `CardComicsAction compact` (`comicsAction && comicsOn`). Marked comment `{/* SLOT item 3: copy / download / open-link actions go here, before the curator group */}`; the `Share actions` group renders only with children (none yet).
- OCR button (label text "OCR", `Button size="sm" variant="ghost"`, same reveal classes) bottom-right, a sibling of the anchor; overlay gets `role="button" tabIndex={0}`, closes on click, Enter, Space and Escape; image click ignored while open.
- Cluster/select/keeper/caption-match strips keep their markup. Footer `data-testid="card-footer"` with `h-10 overflow-hidden`, always rendered, containing `<TagList variant="compact">`.
- No ref reads in render; the existing `copyTimerRef` pattern is unchanged.

- [ ] **Step 1: Update existing tests and write the new failing ones** in `MemeCard.test.tsx`: (a) image query becomes `getByRole("img", { name: "Meme" })`; assert `aria-describedby` target text contains "Hello World"; absent when `text` is empty. (b) The four flag tests use `getByRole("button", { name: "Flagged" })` with `aria-pressed` false/true and click-to-call-API. (c) Prod (`FeaturesContext` stage prod): no Flagged button, no `Add to comics` even with `comicsAction`, no `getByRole("group", { name: "Curator actions" })`, no "Flagged" badge with `flagged: true`. (d) Dev: group present; `comicsAction` false hides comics button, true shows it. (e) Dev + flagged: "Flagged" badge visible, root class has no `border-black` and has `ring-danger`. (f) Link: img inside `a[href="/memes/meme-1"]`, `href` prop overrides; plain click calls `onClick` and `fireEvent.click` returns false (defaultPrevented); with `ctrlKey`, `metaKey`, `shiftKey`, `altKey` and `button: 1` it does not call `onClick` and is not prevented; no `onClick` prop: not prevented. (g) `expect(link).not.toContainElement(button)` for OCR and Flagged. (h) The action bar element has classes `group-focus-within:opacity-100` and `pointer-coarse:opacity-100` and its buttons have no `tabindex="-1"`. (i) `getByTestId("card-footer")` exists with and without tags. (j) OCR overlay opens/closes by click, Enter, Escape; image click ignored while open. (k) `queryByRole("group", { name: "Share actions" })` is null.
- [ ] **Step 2: Run** `pnpm exec vitest run src/components/MemeCard.test.tsx`. Expected: FAIL on the changed and new tests.
- [ ] **Step 3: Implement** the `MemeCard.tsx` render per the behaviour list above.
- [ ] **Step 4: Run** `MemeCard.test.tsx`, then the dependent suites `pnpm exec vitest run src/components` (MemesDuplicatesList, ConceptDetails, MemeDetails, reply ResultCard tests, TrendingMemesPage). Fix call-site tests only where the DOM assumption (checkbox to button, alt text) changed; do not delete tests.
- [ ] **Step 5: Verify** `pnpm exec tsc -b` and `pnpm exec eslint src/ --max-warnings 0`. Expected: pass (watch the React Compiler rules).
- [ ] **Step 6: Commit** as `feat(ui): redesigned MemeCard (link, action bar, equal height)`.

---

### Task 12: `MemesList` grid, skeletons, empty state

**Files:**
- Modify: `src/components/MemesList.tsx` (grid classes `grid-cols-1 md:grid-cols-6` to `grid-cols-2 sm:grid-cols-3 md:grid-cols-6`; `COLUMNS = 6` untouched)
- Test: `src/components/MemesList.test.tsx`

- [ ] **Step 1: Write failing tests:** before the mocked first page resolves, 6 skeleton cards are present and `getByRole("status")` has text "Loading..."; the container has `aria-busy="true"`; an empty result renders `getByText("Nothing to show")` through `EmptyState` (role status); cards expose `a[href^="/memes/"]`; the Add-to-comics test still finds `button` named "Add to comics". Existing tests keep passing.
- [ ] **Step 2: Run** `pnpm exec vitest run src/components/MemesList.test.tsx`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** the changes: initial-load skeleton grid with a visually hidden `<span role="status">Loading...</span>`; keep the bottom "Loading..." status line for later pages; `EmptyState title="Nothing to show"`. Pagination, scroll reset and modal selection untouched.
- [ ] **Step 4: Run** the file, then the verification set. Expected: PASS.
- [ ] **Step 5: Commit** as `feat(ui): MemesList phone grid, skeletons, empty state`.

---

### Task 13: Final sweep and acceptance check

**Files:** none new (fix-ups only if a check fails).

- [ ] **Step 1: Run** from `Frontend/memes-frontend`: `pnpm exec tsc -b`, `pnpm exec eslint src/ --max-warnings 0`, `pnpm exec vitest run`. Expected: all pass.
- [ ] **Step 2: Scope checks.** `git diff main -- package.json pnpm-lock.yaml ../../Backend ../../shared ../../AndroidClient src/types/generated` must be empty; `git diff main --stat -- src` lists only files named in this plan.
- [ ] **Step 3: Report manual checks left for the owner** (spec section 7 items 6 to 13: hover/focus/coarse-pointer reveal, prod build hides curator controls, modifier-click new tab, 44px targets, 390px width, scrim translucency). No dev server is started by this plan.
- [ ] **Step 4: Commit** any fix-ups as `fix(ui): <what>`; otherwise nothing to commit.
