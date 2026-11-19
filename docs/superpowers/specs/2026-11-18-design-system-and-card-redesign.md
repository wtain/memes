# Design system (tokens + shared components) and meme card redesign

Status: planned
Plan: docs/superpowers/plans/2026-11-19-design-system-and-card-redesign.md
Originates from: docs/superpowers/specs/2026-11-18-ui-ux-audit.md

## Decisions (owner-approved 2026-11-19)

These answer section 9 and override any earlier text in this spec that disagrees.

1. The Flagged ring and the "Flagged" badge always show for curators on dev (state, not clutter). Prod behaviour is unchanged (hidden).
2. Two columns on phones is fine. A broader mobile pass is deferred to a later wave.
3. Image `alt` is the generic "Meme" (not the OCR text). The OCR text is offered as a description: the image gets `aria-describedby` pointing at a visually hidden element holding the first OCR lines (cut to 120 characters), rendered only when OCR text exists. This replaces the alt rule in 4.2 and the `/Hello World/` query in section 6 (tests query by name "Meme" and check the description).
4. The dark-mode toggle ships later as a follow-up. Tokens only now (section 3.6 stands).

Scope: audit ranking items 1 (I19+I9, fixes F13, F14, part of F1) and 2 (I6+I21, fixes F4). Frontend only (`Frontend/memes-frontend/`).
Out of scope: item 3 (copy / download / open-link actions; a slot is reserved, see 4.6), skeletons and empty states on other pages (item 4), the dark-mode toggle, the responsive column rework (item 9), the detail page (items 5/7), Android, backend, `shared/schemas`.

## 1. Findings that shape the design

Verified in the code (not only in the audit):

1. **The curator gate already exists in `MemeCard`.** `useFeature("curator")` guards the Flagged checkbox and `comicsAction && useFeature("comics")` guards "Add to comics". On a `VITE_STAGE=prod` build neither is in `SHOWN_ON_PROD` (`features/visibility.ts`), so they are already hidden there. The audit saw them because it looked at a dev build, where every feature is visible by design. So the "public blocker" is not a missing gate; it is that on dev (the owner's daily use) the controls permanently dominate the card. The fix is therefore: keep both gates exactly as they are (they are correct), add tests that prove them (none exist for the hidden case), and move the controls out of the always-visible layout.
2. `MemeCard` is used in six places: `MemesList` (explore/search/untagged/no-ocr/flagged/recommendations), `MemesDuplicatesList` (selectable, similarity), `ConceptDetails`, `MemeDetails` (similar memes), `reply/ResultCard`, `TrendingMemesPage`. Any card change shows up in all of them.
3. `MemesList` renders rows of a hard-coded `COLUMNS = 6` through `react-virtuoso`; the CSS grid inside a row is `grid-cols-1 md:grid-cols-6`. The chunk size and the CSS column count are coupled, so this step does not change the desktop column count (that is audit item 9).
4. There is no router-free link: card tests render `MemeCard` with no `MemoryRouter`, and callers navigate with a handler (`onClick`). A react-router `<Link>` would force a router into every card test.
5. Tailwind is v4 through `@tailwindcss/vite`; `src/index.css` is a single `@import "tailwindcss"`. `tailwind.config.js` (with `important: true`) is **not loaded** by Tailwind 4 (no `@config` directive) and is dead. Tokens go in CSS (`@theme`), not in that file.
6. `Modal.tsx` uses `bg-opacity-60`, a Tailwind 3 utility removed in v4, so the backdrop is probably a solid black today. One-token fix (`bg-black/60`), in scope because the card opens this modal; the implementer confirms in the browser.
7. Existing page chrome: `bg-gray-100` page, white `rounded-lg shadow-sm` tiles on the best page (`StatisticsPage`), text `gray-500` for labels. The tokens below are derived from exactly that look so the cards match the visual reference.

## 2. Approaches considered

### 2.1 Component foundation

| Option | Pros | Cons |
|---|---|---|
| A. Hand-rolled components on Tailwind 4 tokens (recommended) | No new dependency; about 8 small files; matches the current stack; React Compiler and StrictMode are fully under our control; the app needs only buttons, badges, cards, skeletons | We own the a11y of anything interactive; a real menu or popover would be work |
| B. Headless library (Radix UI or React Aria) for primitives | Menus, dialogs, popovers with correct keyboard and focus handling | New dependency (one package per primitive for Radix); this step needs no menu or popover (see 2.2); its rule "do not add a dependency without need" is not met; bundle and upgrade cost for a 3-user app |
| C. Copy-paste kit (shadcn/ui style: Radix + cva + tailwind-merge) | Polished defaults | Adds 3-5 dependencies and a code-gen workflow; heavy for a handful of components |

Recommendation: **A**. Revisit B only when a feature truly needs a menu, combobox or popover (candidate: item 3 if it grows the card to more than four actions, or the Ctrl-K palette of item 11). If that happens, adopt a single headless package for that one primitive, not a kit.

### 2.2 Where the curator controls live

| Option | Pros | Cons |
|---|---|---|
| A. Hover/focus action bar over the image, icon buttons (recommended) | Zero extra clicks for the owner's flag/comics workflow; two buttons only; no menu component to build; also covers item 3 later (3 more icons) | Hover is not available on touch, so the bar must also show on focus and on touch devices (rule below) |
| B. Always-visible overflow ("three dots") menu | Familiar; works identically on touch and mouse | Adds a click to every curator action (the owner flags and adds to comics all day); needs a menu primitive with roving focus, Escape, outside-click: hand-rolled risk or a library (2.1 B) |
| C. Keep controls in a footer row but restyle | Smallest diff | Does not fix the actual problem (clutter, unequal height) |

Recommendation: **A** now. The bar is a flex row, so when the action count passes four (item 3 added), the last slot can become an overflow trigger without redesign. Industry basis: image galleries (Google Photos, Pinterest, Unsplash) reveal per-item actions on hover and keyboard focus and keep them persistently visible on touch.

Rules for any hover-revealed control (WCAG 2.2: 1.4.13 Content on Hover or Focus, 2.1.1 Keyboard, 2.4.7 Focus Visible, 2.5.8 Target Size):

1. Visible on `:hover` and on `:focus-within` of the card, so Tab reveals it.
2. Always visible on touch devices (`pointer-coarse:` variant, Tailwind >= 4.1; the repo has 4.3.3).
3. Never the only way to learn state: a flagged card shows a persistent badge, not only a bar button state.
4. Hidden controls stay in the DOM and tab order (opacity, not `display:none` or `hidden`), otherwise keyboard users cannot reach them.

### 2.3 Card as link

| Option | Verdict |
|---|---|
| A. Plain `<a href="/memes/:id">` around the image with an `onClick` that intercepts only a plain primary click (recommended) | Middle-click, Ctrl/Cmd-click, Shift-click and "Open in new tab" work natively; no router needed in tests; plain click keeps the existing modal/`navigate` behaviour |
| B. react-router `<Link>` | Client-side navigation for free, but needs a router in every card test and in `ResultCard`/`ConceptDetails` tests; behaviour on plain click still needs the handler |
| C. Stretched-link overlay (a link covering the whole card via `::after`) | Whole card clickable, but fights with the other controls and the OCR overlay; rejected |

Recommendation: **A**. A plain anchor does a full page load only when the browser handles the click (new tab, modifier keys), which is the intended case; plain clicks are handled by `onClick` as before. The app has no router `basename`, so `/memes/${id}` is the correct href.

## 3. Design system (audit item 1)

### 3.1 Principles and standards used

- **WCAG 2.2 AA**: text contrast 4.5:1 (3:1 for 18.66px bold or 24px text), non-text UI (focus ring, control borders, icons) 3:1, target size min 24x24 CSS px (2.5.8); we aim for **44x44** on touch (Apple HIG 44pt, Material 48dp, WCAG 2.5.5 AAA). The repo already uses `min-h-11` (44px) in places.
- **8pt grid**: all layout gaps, paddings and component heights are multiples of 4, with 8 as the step for layout (Tailwind `2, 4, 6, 8, 12` i.e. 8, 16, 24, 32, 48 px). Do not introduce arbitrary values (`px-[13px]`).
- **Semantic tokens, not raw colours**: components say `bg-surface text-fg`, never `bg-white text-gray-900`. That is what makes dark mode a token swap instead of a rewrite (F14).
- **Do not rely on colour alone** (1.4.1): flagged = red ring plus a "Flagged" badge with text.

### 3.2 Tokens (`src/index.css`)

Single file edit. Structure (names are the contract; hex values are the starting point, the implementer verifies contrast with a script and records the ratios in a comment):

```css
@import "tailwindcss";

/* 1. Raw semantic values, light by default */
:root {
  --c-page: #f3f4f6;        /* gray-100, current app background */
  --c-surface: #ffffff;
  --c-surface-sunken: #f9fafb;
  --c-line: #e5e7eb;        /* hairline borders, decorative only */
  --c-line-strong: #6b7280; /* borders that identify a control (>= 3:1 on surface) */
  --c-fg: #111827;          /* 17:1 on surface */
  --c-fg-muted: #4b5563;    /* ~7.6:1 */
  --c-fg-subtle: #6b7280;   /* ~4.8:1 on white; the lowest allowed for text. gray-400 is banned for text (2.5:1) */
  --c-brand: #2563eb;       /* ~5.2:1 on white; white text on it also ~5.2:1 */
  --c-brand-hover: #1d4ed8;
  --c-on-brand: #ffffff;
  --c-danger: #dc2626;      /* ~4.8:1 on white */
  --c-danger-hover: #b91c1c;
  --c-on-danger: #ffffff;
  --c-success-bg: #dcfce7; --c-success-fg: #166534;
  --c-warning-bg: #fef3c7; --c-warning-fg: #92400e;
  --c-danger-bg: #fee2e2;  --c-danger-fg: #991b1b;
  --c-scrim: rgb(0 0 0 / 0.6);     /* modal backdrop and image overlays */
  --c-focus: #2563eb;
}

/* 2. Dark values: defined now, NOT switched on anywhere yet (see 3.6) */
:root[data-theme="dark"] {
  --c-page: #0b1120; --c-surface: #111827; --c-surface-sunken: #0f172a;
  --c-line: #1f2937; --c-line-strong: #9ca3af;
  --c-fg: #f3f4f6; --c-fg-muted: #d1d5db; --c-fg-subtle: #9ca3af;
  --c-brand: #60a5fa; --c-brand-hover: #93c5fd; --c-on-brand: #0b1120;
  --c-danger: #f87171; --c-danger-hover: #fca5a5; --c-on-danger: #0b1120;
  --c-success-bg: #14532d; --c-success-fg: #bbf7d0;
  --c-warning-bg: #78350f; --c-warning-fg: #fde68a;
  --c-danger-bg: #7f1d1d;  --c-danger-fg: #fecaca;
  --c-focus: #93c5fd;
}

/* 3. Expose to Tailwind utilities (bg-surface, text-fg-muted, ring-focus, ...) */
@theme inline {
  --color-page: var(--c-page);
  --color-surface: var(--c-surface);
  --color-surface-sunken: var(--c-surface-sunken);
  --color-line: var(--c-line);
  --color-line-strong: var(--c-line-strong);
  --color-fg: var(--c-fg);
  --color-fg-muted: var(--c-fg-muted);
  --color-fg-subtle: var(--c-fg-subtle);
  --color-brand: var(--c-brand);
  --color-brand-hover: var(--c-brand-hover);
  --color-on-brand: var(--c-on-brand);
  --color-danger: var(--c-danger);
  --color-danger-hover: var(--c-danger-hover);
  --color-on-danger: var(--c-on-danger);
  --color-success-bg: var(--c-success-bg); --color-success-fg: var(--c-success-fg);
  --color-warning-bg: var(--c-warning-bg); --color-warning-fg: var(--c-warning-fg);
  --color-danger-bg: var(--c-danger-bg);   --color-danger-fg: var(--c-danger-fg);
  --color-scrim: var(--c-scrim);
  --color-focus: var(--c-focus);
}

/* 4. Non-colour tokens */
@theme {
  --radius-sm: 0.375rem;  /* 6  chips, small buttons */
  --radius-md: 0.5rem;    /* 8  buttons, inputs */
  --radius-lg: 0.75rem;   /* 12 cards */
  --shadow-card: 0 1px 2px rgb(0 0 0 / 0.06), 0 1px 3px rgb(0 0 0 / 0.1);
  --shadow-card-hover: 0 4px 12px rgb(0 0 0 / 0.12);
  --text-meta: 0.75rem;   /* 12px: captions, badges, nothing else */
  --text-meta--line-height: 1rem;
}

/* 5. Focus ring: one rule for every interactive element, visible on any background */
:focus-visible { outline: 2px solid var(--c-focus); outline-offset: 2px; }
```

Notes:
- `@theme inline` is required so utilities reference the CSS variable (and so a `data-theme` switch re-themes them) instead of a value baked at build time.
- Spacing keeps the Tailwind default 4px scale (no override); the 8pt rule is a usage convention enforced in review, not a token.
- Type scale stays Tailwind's (`text-sm` 14, `text-base` 16, `text-lg`, `text-xl`), plus `text-meta` for 12px. Body and control text are `text-sm`/`text-base`; 12px only for badges and captions; nothing smaller. Existing `text-xs` is not bulk-replaced.
- Body defaults: `body { background: var(--c-page); color: var(--c-fg); }` in `index.css`. The existing wrapper `AppLayout` (`min-h-screen bg-gray-100 text-gray-900`) is left as is in this step (same visual result; migrated when the header is reworked, item 8).
- Raw palette utilities (`bg-gray-100`, `text-blue-600`) keep working; the 100+ existing usages are not touched. New and migrated code uses semantic tokens only.
- Small dependency-free stylistic additions (e.g. `prefers-reduced-motion`): all transitions in the new components use `motion-safe:` so reduced-motion users get none.

### 3.3 Component set (`src/ui/`)

New folder `Frontend/memes-frontend/src/ui/` with one file per component, an `index.ts` barrel, and a co-located test per component. No CSS-in-JS, no class-merging library: variants are lookup tables of class strings and callers pass `className` for layout only (appended last). All components are plain function components, no refs touched during render (React Compiler rules), no module-level mutable state (StrictMode double mount).

**Button** (`ui/Button.tsx`)
```ts
type ButtonProps = React.ComponentProps<"button"> & {
  variant?: "primary" | "secondary" | "ghost" | "danger"   // default "secondary"
  size?: "sm" | "md" | "lg"                                // 32 / 40 / 44 px high; default "md"
  iconOnly?: boolean                                       // square, requires aria-label (type-enforced, see below)
  pressed?: boolean                                        // toggle: maps to aria-pressed
  loading?: boolean                                        // disables, aria-busy, keeps width
}
```
- Always `type="button"` unless overridden (avoids accidental form submit).
- `iconOnly` requires `aria-label`: model as a discriminated union so `tsc` fails without it.
- Variants: primary `bg-brand text-on-brand hover:bg-brand-hover`; secondary `bg-surface text-fg ring-1 ring-inset ring-line-strong hover:bg-surface-sunken`; ghost `text-fg-muted hover:bg-black/5`; danger `bg-danger text-on-danger`. Disabled: `disabled:opacity-50 disabled:pointer-events-none` (disabled controls are exempt from contrast rules, WCAG 1.4.3).
- Touch target: `sm` is 32px on fine pointers and grows to 44px under `pointer-coarse:`; `md`/`lg` are already >= 40/44. Icon-only buttons are square at the same sizes.
- Focus: inherits the global `:focus-visible` rule; do not remove outlines.
- `ButtonLink` (same file): `<a>` styled as a Button (`href` required), for navigation that looks like a button; no router dependency.

**Badge** (`ui/Badge.tsx`) — also the "Chip".
```ts
type BadgeProps = React.ComponentProps<"span"> & {
  tone?: "neutral" | "success" | "warning" | "danger" | "brand"  // default "neutral"
}
```
- `rounded-full px-2 py-0.5 text-meta font-medium`, colours from the `*-bg`/`*-fg` token pairs (all pairs are >= 4.5:1). Replaces the ad hoc `bg-gray-200`, `bg-amber-100 text-amber-800`, `bg-green-100 text-green-800` pills.
- Non-interactive. An interactive chip is a `Button` (ghost, sm), not a Badge, so the a11y contract stays clear.

**Card** (`ui/Card.tsx`)
```ts
type CardProps = React.ComponentProps<"div"> & { padded?: boolean; interactive?: boolean }
```
- `rounded-lg bg-surface shadow-card ring-1 ring-black/5 overflow-hidden`; `padded` adds `p-4`; `interactive` adds `motion-safe:transition-shadow hover:shadow-card-hover`. This is the StatisticsPage tile, named. `MemeCard` uses `Card`'s classes via the component (`<Card interactive>`), which is also how the "no 2px black border" fix lands.
- The `ring-1 ring-black/5` hairline (not a border) gives a faint edge on the gray page; the image itself carries the visual weight. In dark mode the ring token must be swapped (`ring-line`): use `ring-line` rather than `ring-black/5` so one token drives both themes.

**Skeleton** (`ui/Skeleton.tsx`)
```ts
type SkeletonProps = React.ComponentProps<"div">   // size comes from className (w-/h-/aspect-)
```
- `rounded-md bg-line motion-safe:animate-pulse`, `aria-hidden`. Containers that show skeletons set `aria-busy="true"` and render one visually-hidden `<span role="status">Loading...</span>` (screen readers get one announcement, not N boxes; also keeps existing tests that look for "Loading..." text valid).
- `MemeCardSkeleton` (same file): an `aspect-square` block plus one footer line, same outer box as the real card so the grid does not jump.

**EmptyState** (`ui/EmptyState.tsx`)
```ts
type EmptyStateProps = {
  title: string; description?: string; action?: React.ReactNode; icon?: React.ReactNode
  tone?: "empty" | "error"   // error uses role="alert", empty uses role="status"
}
```
- Centered, `py-12`, `text-fg` title, `text-fg-muted` description, optional action (a `Button`). The title text is the visible string, so `getByText('Nothing to show')` keeps working if that is passed as the title.

Nothing else is strictly needed by item 2 beyond a tiny **`IconButton` usage of `Button iconOnly`** and the icons themselves: the repo has no icon library. Use small inline SVGs in `ui/icons.tsx` (flag, comics, check, text; 20x20 viewBox, `aria-hidden`, `currentColor`). No icon dependency is added. Item 3 will add copy/download/link icons to the same file.

`Tabs`, `Input`, `Select`, `Table`, `Tooltip`, `Menu`, `Dialog` are **deferred**. `Modal.tsx` stays where it is (only the scrim fix; a `Dialog` rewrite with focus trap and `aria-modal` is a separate item).

### 3.4 Accessibility rules baked into the components

1. Every interactive control has an accessible name (visible text or `aria-label`); icon-only without a label is a type error.
2. Toggle buttons use `aria-pressed`, never a changed label alone.
3. Focus is always visible (global `:focus-visible`, 2px, offset 2px, token `focus`, >= 3:1 against both page and card).
4. Min target 24x24 everywhere; 44x44 on coarse pointers for card actions and for any new primary action.
5. Text colours only from `fg`, `fg-muted`, `fg-subtle`, the `*-fg` tones and `on-*`; `text-gray-400` (and lighter) for text is banned in new code.
6. Motion only under `motion-safe:`.
7. No information by hover alone (see 2.2 rules).

### 3.5 Incremental migration

Principle: the design system is added next to the old styles; nothing is rewritten wholesale. A page migrates when it is touched for another reason.

Migrated in this epic step:
- `index.css` (tokens, focus rule, body defaults).
- `ui/*` (new).
- `components/MemeCard.tsx`, `components/TagList.tsx`, `components/Tag.tsx`, `components/comics/CardComicsAction.tsx` (compact mode only), `components/MemesList.tsx` (grid classes, loading and empty states), `components/Modal.tsx` (scrim token only).
- Call sites that need no code change but visually inherit the card: `MemesDuplicatesList`, `ConceptDetails`, `MemeDetails` (similar memes), `reply/ResultCard`, `TrendingMemesPage`. `TrendingMemesPage`'s hand-made green/amber pills may switch to `Badge` in the same change only if trivial (optional, not required).

Deferred (owner-facing admin and later waves): `AppLayout` and the nav (item 8), `StatisticsPage` (already the reference, switch to `Card` opportunistically), `FacetSidebar`/`MultiSelectFacet`, `MemeDetails` body and the detail page (items 5, 7), `ReplyPage`, all admin/ingestion/recut/comics editor screens, every table (F10), dark-mode toggle.

### 3.6 Dark mode

Tokens support it (the `:root[data-theme="dark"]` block). Shipping the switch is **out of scope**: it would need a toggle UI, persistence, a no-flash script in `index.html`, `color-scheme` handling, and an audit of the hundreds of raw `bg-white`/`text-gray-*` usages on un-migrated pages, which would render as white islands. Nothing sets `data-theme`, so the dark block is inert in this step. A follow-up can enable it per migrated surface. (A `prefers-color-scheme: dark` media block is deliberately not added for the same reason.) A single test asserts the dark block defines every token the light block does (guards drift), see section 6.

## 4. Card redesign (audit item 2)

### 4.1 Target layout

```
+--------------------------------+   <a href="/memes/:id"> wraps the image only
|  [Flagged]            [bar]    |   top-left: persistent badge (curator + flagged only)
|                                |   top-right: action bar (hover / focus-within / touch)
|           IMAGE  (aspect-sq)   |
|                                |
|                        [OCR]   |   bottom-right: OCR toggle (also revealed like the bar)
+--------------------------------+
| #tag1  #tag2  +3              |   footer: ONE line, fixed height
+--------------------------------+
```
Optional strips (only when their props are passed, used by duplicates review): cluster line, Select/Keeper row, "Caption match" line. They keep their current markup and sit between the image and the tag footer; they appear only inside `MemesDuplicatesList`, where every card in the grid has them, so equal height still holds there.

### 4.2 Visual rules

- Remove `border-2 border-black` / `border-red-500`. The card is `<Card interactive>` (shadow + hairline ring). Flagged: `ring-2 ring-danger` replaces the hairline, plus the persistent top-left **"Flagged" `Badge tone="danger"`** (text, not colour alone). Both only render when `curator` is visible: on prod a flagged item is not meaningful to a visitor and `flagged` would leak curator state otherwise.
- Equal height: card is `flex h-full flex-col`; image area is `aspect-square` (the default `variant="square"`; `variant="full"` is not used by any caller today and keeps its natural height, documented as not equal-height); the tag footer has a fixed `h-10` (`min-h-10`) and `overflow-hidden`, rendered even when there are no tags (empty footer) so cards with and without tags are the same height. The grid rows already stretch (`items-stretch` is the grid default) and `h-full` makes the card fill the cell. No truncation of the image: `object-cover` as today.
- Image `alt`: today it is the UUID (`alt={meme.id}`), which is noise for screen readers. New: first OCR lines joined and cut to 120 characters, or `"Meme"` when there is no OCR text. This is the only a11y name for the link (the anchor's name comes from its image). The test that finds the image by `{ name: 'meme-1' }` changes (section 6).
- Hover: `shadow-card-hover` and, under `motion-safe:`, nothing else (no scale transforms; they jitter virtualised grids).

### 4.3 Card is a link; plain click keeps current behaviour

- The image is wrapped in `<a href={href ?? `/memes/${meme.id}`}>`. New optional prop `href?: string` (default as shown) for callers that need a different target.
- Handler (pure function in the file, no refs):
  ```
  onClick(e):
    if (showText || !onClick) return           // OCR overlay open: ignore; no handler: let the browser navigate
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return  // browser opens new tab/window
    e.preventDefault(); onClick()
  ```
  Middle-click does not fire `click` on the anchor (it fires `auxclick`), so the browser opens a new tab natively. Existing callers keep passing `onClick` (modal in `MemesList`/`TrendingMemesPage`/`MemesDuplicatesList`, `navigate` in `MemeDetails`/`ConceptDetails`/`ResultCard`); their code does not change.
- Keyboard: the anchor is focusable and Enter activates it (click event with `button 0`), giving the card keyboard access it lacks today (an `<img onClick>` is unreachable by keyboard).
- Controls (bar, OCR button, footer) are **siblings** of the anchor, never inside it: interactive content inside `<a>` is invalid HTML and makes both clickable and unreliable. They are positioned over the image with `absolute`.

### 4.4 Tags: short set with overflow

- `TagList` gets an optional `variant?: "full" | "compact"` (default `"full"` = current behaviour, existing tests and `MemeDetails` unchanged).
- `compact`: shows at most **2** qualifying tags (score > 0.3, the current rule) on a single non-wrapping line (`flex-nowrap overflow-hidden`), each chip `max-w-[...]` truncated with ellipsis, label is the tag name only (the `(source)` suffix is dropped in the card; the full `name (source)` is the chip's `title`). Overflow is a non-interactive `+N` `Badge` whose `title` lists the hidden tags; it does not expand (expanding would change the card height). Full tag browsing stays on the details view.
- `Tag.tsx`: switch its classes to `Badge` tokens (`bg-gray-200` is fine on white but the new neutral tone is the shared one). Its test (`renders as a span element`, `#` prefix) still holds.

### 4.5 Curator controls: who sees them, where they live

Gating (unchanged semantics, now explicit and tested):
- Flag toggle renders only when `useFeature("curator")`.
- "Add to comics" renders only when `comicsAction && useFeature("comics")`.
- The bar container itself renders only when it has at least one child (curator toggle, comics action, or the reserved share slot once item 3 lands), so on prod a visitor sees no empty bar.
- `comicsAction` stays a prop (`MemesList` passes `!listFlagged`) and stays an additional gate, not replaced by the feature check.

Bar contents, in order (a flex row, `gap-1`, top-right, `rounded-md bg-scrim` backing so icons keep 3:1 contrast over any image; this is also why the overlay is dark in both themes):
1. `<!-- ITEM-3 SLOT -->` a clearly marked empty fragment: `{/* SLOT item 3: copy / download / open-link actions go here, before the curator group */}` rendered inside `<div role="group" aria-label="Share actions">` only when it has children. Item 3 adds its buttons here without touching anything else.
2. Curator group (`role="group" aria-label="Curator actions"`):
   - Flag toggle: `Button iconOnly variant="ghost" pressed={isFlagged} aria-label="Flagged"` (the label is the state noun so `aria-pressed` reads "Flagged, toggle button, pressed"). Behaviour identical to today: `markImageIsFlagged` / `unmarkImageIsFlagged`, local `isFlagged` state flips after the promise resolves. The `e.preventDefault()` from the checkbox is dropped (not needed on a button). Errors are still not surfaced (unchanged; out of scope).
   - Comics: `CardComicsAction` in a new `compact` mode.

`CardComicsAction` change (contained; the other worktree edits this component, see section 8): add one optional prop `compact?: boolean`. All logic (working comics, picker, `addDirect`, errors, `off`) is untouched. In `compact` mode only the **render** differs: the wrapper has no padding/border, the button is `Button iconOnly variant="ghost" aria-label={working ? \`Add to ${working.name}\` : "Add to comics"}` with a comics icon, and the "Added to X / Open editor" and error messages render in an absolutely positioned `role="status"` / `role="alert"` strip anchored under the bar (inside the image area) so the card height never changes. Default (non-compact) rendering is byte-for-byte as today so its existing tests stay green.

Reveal behaviour (CSS only, no state):
- Wrapper element is the card root with `group`; the bar and the OCR button get `opacity-0 transition-opacity motion-safe:duration-150 group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:opacity-100`.
- Exception: when the flag is on, the **badge** is always visible (it is not part of the bar).
- Because the bar is in the DOM at opacity 0, focusing any control inside it (Tab) triggers `focus-within` and reveals it; tests can still find the buttons.

### 4.6 OCR overlay

Kept. The trigger moves from "always visible, `text-xs`, tiny" to the same reveal behaviour as the bar, bottom-right, with a 32px (44px on coarse) hit area (`Button size="sm" variant="ghost"` on a scrim backing; label stays the text "OCR" so `getByRole('button', {name:'OCR'})` keeps working). The overlay (`showText`) markup and behaviour (click to close, ignores image click while open) stay; it is not made a dialog (it is not modal); it keeps `cursor-pointer` and gets `role="button" tabIndex=0` plus Escape/Enter to close, because a click-only closer is keyboard-inaccessible today.

### 4.7 `MemesList` changes

- Grid class: `grid-cols-1 md:grid-cols-6` becomes `grid-cols-2 sm:grid-cols-3 md:grid-cols-6` (two columns on phones is the gallery standard; one 100%-width square image per row wastes the screen). `COLUMNS = 6` and the chunking are untouched; only the number of visual rows within a Virtuoso item changes on small screens. Desktop is identical. The ragged 3-column intermediate range (6 items = 2 even rows) is clean; no change to chunking is needed because 6 divides by 2 and 3.
- `Loading...` (shown after the first page) and the initial load: initial load shows a grid of `MemeCardSkeleton` (6) with the visually hidden `Loading...` status; the bottom "Loading..." line stays as a small centered status. "Nothing to show" becomes `<EmptyState title="Nothing to show" />` (same visible text). Search-specific empty copy and suggestions are item 4, not here.
- No other behaviour change: pagination, scroll reset, modal selection untouched.

### 4.8 Other callers (no code change expected)

| Caller | Impact |
|---|---|
| `MemesDuplicatesList` | Same props; select/keeper row and caption-match line stay. Flag toggle now an icon button in the bar instead of a checkbox row. |
| `ConceptDetails`, `MemeDetails` (similar), `ResultCard`, `TrendingMemesPage` | Same props. `ResultCard` and `TrendingMemesPage` render their own pills under the card; the card's equal-height rule applies to the card only. |
| `MemeDetails` passes `comicsAction` | Gating unchanged. |

### 4.9 Not a route change

No router or backend route is added or changed. `/memes/:id` already exists (`app/router.tsx`) and is a `READER` route; the card only links to it. **`ROUTE_POLICY` (`Backend/app/stage_policy.py`) is untouched**, no `visibility.ts` or `FeatureVisibility.kt` entry is needed, no migration, no `backend_api.md` or `shared/schemas` change, Android untouched. (Android parity of the card is not in scope.)

## 5. Repository pitfalls to respect (CLAUDE.md)

- **React Compiler ESLint rules**: no ref reads/writes during render; `MemeCard` already uses a ref only in handlers/effects (`copyTimerRef`) and that stays. New components are pure functions of props; no `useMemo`/`useCallback` added "for performance" (the compiler does that). The anchor click handler is an inline function (allowed).
- **StrictMode double mount**: no effects added in `ui/*`. `MemeCard`'s existing copy-timer effect-free pattern is unchanged. Skeleton and reveal states are CSS.
- **Stage policy**: the gating is `useFeature`, which already combines the build flag and capability. Do not read `import.meta.env.VITE_STAGE` directly in the card.
- **Generated types**: do not hand-write API types; none are needed here (`Meme`, `MemeTag` already generated).
- Windows/PowerShell: edit files with the Edit tool, do not round-trip through `Get/Set-Content`.
- Run order from the frontend dir: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`.

## 6. Tests

Existing tests that **change** (`Frontend/memes-frontend/src/components/`):

| File | Test | Change |
|---|---|---|
| `MemeCard.test.tsx` | `renders the meme image ...` (`getByRole('img', { name: 'meme-1' })`) | alt is now derived from OCR text / "Meme"; query `getByRole('img', { name: /Hello World/ })` (the fixture's text is `['Hello World','Second line']`). |
| `MemeCard.test.tsx` | the 4 flag tests using `getByRole('checkbox')` | The flag control is now `getByRole('button', { name: 'Flagged' })` with `aria-pressed` (`false`/`true`) instead of checked/unchecked; the two API-call tests click the button. The Select checkbox tests (`name: 'Select'`) are unaffected. |
| `MemeCard.test.tsx` | `calls onClick when the image area is clicked` | Still passes (click on the img bubbles to the anchor). Keep; also assert `preventDefault` happened (see new tests). |
| `MemesList.test.tsx` | `renders a card for each returned meme` / the `Add to comics` test | Verify they still pass; Add-to-comics button name is unchanged in non-compact mode, **but the card now renders the compact variant (aria-label "Add to comics")**, same accessible name, so the query holds; adjust only if the DOM structure assumption differs. |
| `TagList.test.tsx`, `Tag.test.tsx`, `Modal.test.tsx` | none expected | They must pass unchanged (`variant` defaults to `"full"`; Modal only changes a class). If a Modal test asserts the old class string, update it. |

New tests (vitest + testing-library; wrap in `FeaturesContext.Provider` from `features/useFeature.ts`, which is already exported, to simulate prod: `value={{ stage: "prod", capabilities: null }}`):

`src/ui/*.test.tsx`
- `Button`: renders each variant; `type="button"` default; `iconOnly` without `aria-label` is a TypeScript error (use `// @ts-expect-error` in the test so `tsc -b` verifies it); `pressed` sets `aria-pressed`; `loading` disables and sets `aria-busy`; `className` is appended.
- `Badge`: tone classes; renders children; is not focusable.
- `Card`: `padded`/`interactive` classes; passes through props.
- `Skeleton` / `MemeCardSkeleton`: `aria-hidden`; `MemeCardSkeleton` is not announced.
- `EmptyState`: `tone="error"` has `role="alert"`, default `role="status"`; action renders.
- `tokens.test.ts`: read `src/index.css` as text; assert every `--c-*` defined in `:root` is also defined in `:root[data-theme="dark"]` and that every `--color-*` in `@theme inline` maps to a `--c-*` that exists (guards token drift; no jsdom CSS needed since `css: false`).

`MemeCard.test.tsx` additions
- Prod stage (`stage: "prod"`): no `Flagged` button, no `Add to comics` button even with `comicsAction`, no curator group, and no persistent "Flagged" badge even when `meme.flagged` is true.
- Dev stage: curator group present; `comicsAction` false hides the comics button; `comicsAction` true shows it.
- Flagged state: dev + `flagged: true` shows the `Flagged` badge and no `border-black` class on the root (assert the root has no class containing `border-black`).
- Link: the image is inside `<a href="/memes/meme-1">`; `href` prop overrides; plain click calls `onClick` and the event is `defaultPrevented`; `fireEvent.click` with `ctrlKey`, `metaKey`, `shiftKey` or `button: 1` does **not** call `onClick` and is not prevented; no `onClick` prop leaves the default (not prevented).
- Controls are siblings of the link: `expect(link).not.toContainElement(button)` for the OCR and flag buttons.
- Reveal-on-focus contract: the bar has `group-focus-within:opacity-100` and `pointer-coarse:opacity-100` classes (class assertion; real visibility is a manual check), and its buttons are focusable (`tabIndex` not `-1`, no `hidden`).
- Equal height contract: the tag footer element is rendered with and without tags (same wrapper present; assert via a `data-testid="card-footer"`).
- OCR overlay still opens/closes; additionally Escape/Enter close it; image click is ignored while it is open.
- Slot: a `data-testid="card-actions-slot"` (or `role="group" aria-label="Share actions"`) is not rendered while empty (placeholder test that item 3 will flip).

`TagList.test.tsx` additions: `variant="compact"` shows at most 2 tags, never a button, shows `+N` badge with a `title` listing hidden tag names, label has no `(source)`, and does not wrap (class `flex-nowrap`).

`CardComicsAction.test.tsx` addition: `compact` renders an icon-only button with the same accessible name and unchanged add-flow; status/error messages render with `role="status"`/`role="alert"`.

`MemesList.test.tsx` additions: initial load renders skeletons plus a status "Loading..." (until the mock resolves); empty result renders `EmptyState` with "Nothing to show"; cards expose links `a[href^="/memes/"]`.

## 7. Acceptance criteria

Automated (from `Frontend/memes-frontend/`):
1. `tsc -b` passes.
2. `eslint src/ --max-warnings 0` passes (including the React Compiler rules).
3. `vitest run` passes with all the tests above; no test deleted without replacement.
4. No new entry in `package.json` dependencies or devDependencies (`git diff package.json pnpm-lock.yaml` is empty).
5. `git diff -- ../../Backend ../../shared ../../AndroidClient` is empty; `Frontend/memes-frontend/src/types/generated/` unchanged.

Manual (owner or implementer, dev build on a free port; **do not use 5173-5175**, run `vite --port` on an unused one such as 5199 or use `vite preview`):
6. Explore page: no 2px black border; cards are the same height with and without tags; flagged card shows a red ring and a "Flagged" badge.
7. At rest the card shows only image and one tag line; Flag / Add-to-comics icons appear on hover, on Tab focus, and are always visible when the browser is in a touch/coarse-pointer emulation.
8. With the build flag simulated for prod (`VITE_STAGE=prod vite build`, then preview): no curator controls, no flag badge, no empty bar, on `/explore`, `/trending-memes`, and the similar-memes block.
9. Plain click opens the existing modal; Ctrl/Cmd-click and middle-click open `/memes/:id` in a new tab; Enter on a focused card opens the modal; "Open link in new tab" in the context menu works.
10. Keyboard: Tab order is image link, bar buttons, OCR, ... with a visible 2px focus ring everywhere; Esc/Enter closes the OCR overlay.
11. Contrast: the implementer lists computed ratios for fg, fg-muted, fg-subtle, brand-on-white, on-brand, danger and tone pairs (light) and the dark values in the PR description; all text >= 4.5:1, ring/icon >= 3:1. Touch targets: card action buttons measure >= 44x44 under coarse pointer.
12. Phone-width viewport (390px): two columns, no horizontal scroll, no overlapping text. After "Add to comics", the card height does not change.
13. Modal backdrop is now translucent (scrim fix).

## 8. Files expected to be touched (merge-conflict map)

The `multi-image-stories-epic` worktree edits comics UI around the card. Conflict-prone files are marked **!**.

New (no conflict): `src/ui/Button.tsx`, `Badge.tsx`, `Card.tsx`, `Skeleton.tsx`, `EmptyState.tsx`, `icons.tsx`, `index.ts`, their `*.test.tsx`, `tokens.test.ts`.

Modified:
- `src/index.css` (tokens; low risk, currently one line).
- **! `src/components/MemeCard.tsx`**: restructure of the render only (anchor, bar, footer). Kept contained: props type gains only `href?`; the existing prop names and their meaning are unchanged; the `selectable`/`similarity`/cluster blocks are moved but not edited; the comics line stays one line (`{comicsAction && comicsOn && <CardComicsAction ... compact />}`), now inside the bar. If the other epic changes this line or the props, the merge is a one-line resolution.
- **! `src/components/comics/CardComicsAction.tsx`**: adds one optional prop `compact` and a render branch; logic untouched. This is the highest-risk file: the stories epic most likely edits it. Mitigation: land this change as a separate commit so it can be rebased or reordered independently.
- `src/components/TagList.tsx`, `src/components/Tag.tsx` (small).
- `src/components/MemesList.tsx` (grid classes, loading and empty states; `COLUMNS` untouched). Medium risk if the stories epic edits `WorkingComicsBar` placement in this file; the touched lines are disjoint.
- `src/components/Modal.tsx` (one class).
- Tests: `MemeCard.test.tsx`, `MemesList.test.tsx`, `TagList.test.tsx`, `CardComicsAction.test.tsx` (the other epic probably touches the last).

Not touched: `features/visibility.ts`, `app/router.tsx`, `Backend/**`, `shared/**`, `AndroidClient/**`, `tailwind.config.js` (dead file, left alone; removing it is a separate cleanup).

Suggested commit order to ease rebasing: (1) tokens + `ui/*`, (2) `Modal` scrim + `Tag`/`TagList`, (3) `CardComicsAction` compact, (4) `MemeCard` + `MemesList` + tests.

## 9. Open questions for the owner

1. Do you want the "Flagged" red ring and badge visible on dev always (recommended, it is state, not clutter), or only inside `/flagged` and on hover?
2. Is the 2-column phone grid acceptable until the responsive rework (item 9), and should friends' phones be a Wave 1 target (the audit ranks the mobile pass late)? This changes how much responsive work should move into this step.
3. Alt text policy: OCR text as alt (recommended) is in Russian/Spanish/English; is that acceptable, or should alt be a generic "Meme" for now?
4. Dark mode: tokens only, as specified, or do you want a follow-up item to ship the toggle on the already-migrated surfaces?
