# UI/UX audit and idea backlog

Status: implementation
Originates from: user request 2026-11-18 (design/UI/UX audit epic; branch `worktree-ux-design-audit`)

Audience today: the owner and their partner. Planned: a public read-only prod stage (`VITE_STAGE=prod`) used by a few friends.
Method: code read of `Frontend/memes-frontend/src` plus a live walk of the `general` dev frontend (port 5174, viewport 1262x541).
Limit: the browser tool's viewport could not be resized, so mobile and ultra-wide were NOT observed; those findings are from code only.

## Findings (observed)

| # | Area | Finding |
|---|------|---------|
| F1 | Layout | Everything sits in a `max-w-6xl` column; wide screens waste both margins. Gutters differ slightly between pages (content starts at x=64 on Explore/Statistics, x=87 on Trending/Reply/Upload) because page-level wrappers differ. |
| F2 | Home | `/` redirects to `/explore`. No landing, no "what's new", no entry point per intent (find / reply / browse). |
| F3 | Nav | Four text dropdowns, no brand, no header search, no environment badge (metal/general/it look identical). Cleanup/Admin groups are two of four top-level items. |
| F4 | Cards | Every card shows a "Flagged" checkbox and "Add to comics" button, plus a thick black 2px border, even in plain browsing. Curator controls dominate the card; tags are hidden behind "+N more". Cards in a row have unequal heights (blank white space under short ones). |
| F5 | Card action | No copy/share/download action on a card, which is the core job of a meme app. A card has no anchor link; opening is a click handler (no middle-click / open in new tab). |
| F6 | Detail | Details open as a modal titled "Meme Details" (and a page titled "Meme {uuid}"). The image takes the whole first screen; tags, description, popularity, saves, "shown" counters, flag toggle and similar memes follow in one long stack. An empty ID/Name table header renders when there are no concepts. Owner analytics (popularity, saves, shown) sit between the image and similar memes. |
| F7 | Search | Facet sidebar scrolls independently of the page (nested scroll area), mixes English and Russian facet names, and every facet is a dropdown showing "(none)". No result count, no active-filter chips, no sort control. |
| F8 | Reply | Strong concept, plain execution: a large empty page under one text box with no examples or recent messages. |
| F9 | Trending | With one trending meme the page is almost empty and gives no explanation of what "trending" means or what to do. |
| F10 | Tables | Concepts, Trends, Admin batches are bare bordered tables: no search, no sorting, no row hover; Admin has horizontal overflow and overlapping text ("build_description_note_embeddings" overlaps its status). |
| F11 | Statistics | Cards look good (best page), but "Rejected (ingestion)" orphan card leaves an empty grid slot; long page with no section nav. |
| F12 | States | "Loading..." text, no skeletons; empty/error states are plain text. |
| F13 | Tokens | No shared design tokens or component set (Button, Badge, Card, Tabs...). Each page restyles buttons: black outline, grey outline, solid blue, amber pill. |
| F14 | Theming/a11y | No dark mode. Many `text-xs`/gray-400 texts; contrast and focus visibility not verified. |

## Idea long-list

Fill empty space without clutter
- I1 Home page: big search, Recently added, Trending, Continue where you left off, Reply shortcut (max 3-4 widgets).
- I2 Full-width responsive grid; sticky filter column.
- I3 Detail as two columns: large image left, sticky tabbed panel right (Info, Tags, Similar, Curate).
- I4 Quick-look lightbox with arrow keys, no navigation away from the grid.
- I5 "Related" rail on the detail view.

Browsing feel
- I6 Card redesign: no heavy border, curator controls only for curators and on hover, tags on hover.
- I7 Skeleton loaders, real empty/error states.
- I8 One-click Copy image / Copy link / Download.
- I9 Dark mode plus design tokens.
- I10 Masonry (true aspect ratio) option.

Navigation
- I11 Persistent header search and Ctrl/Cmd-K palette.
- I12 Sidebar nav or simplified top nav; prod shows only what friends need; environment badge on dev.
- I13 Breadcrumbs and "back to results" preserving scroll and filters.

Admin
- I14 Admin dashboard of queues that need attention.
- I15 Shared review layout with keyboard shortcuts (ingestion, duplicates, recut, comics).
- I16 Statistics with charts over time.

Cross-cutting
- I17 Accessibility pass.
- I18 Mobile/responsive pass.
- I19 Small shared component set.

Public-readiness (new, from "friends will use it")
- I20 First-visit orientation: one line explaining what the site does and 3 example searches.
- I21 Browsing vs curating split: curator UI strictly gated (already a `curator` feature; verify cards honour it on prod).
- I22 Shareable meme URLs with a proper page, title and preview image (Open Graph) so a link pasted in a chat shows the meme.
- I23 Language handling: facet names and UI copy are mixed RU/EN; decide a UI language policy.
- I24 Perceived performance on a public connection (image thumbnails, lazy loading).

## Ranking

Criteria: impact for the public launch and daily use, effort (S under 1 day, M 1-3 days, L a week or more), risk of regressions. Foundations come first because later items build on them.

### Wave 1: foundations and quick wins (do first)
| Rank | Idea | Fixes | Effort | Why now |
|------|------|-------|--------|---------|
| 1 | I19+I9 Tokens and shared components (Button, Badge, Card, Skeleton, EmptyState), dark mode later | F13, F14, F1 | M | Everything else reuses it; cheap now, costly to retrofit |
| 2 | I6+I21 Card redesign, curator controls hidden unless curator | F4 | S | Public blocker: friends must not see Flag / Add to comics |
| 3 | I8 One-click Copy / Download / Open link, cards as real links | F5 | S | The core job of the app |
| 4 | I7 Skeletons, empty and error states | F9, F12 | S | Fills the "empty" screens with guidance |
| 5 | I22 Shareable meme page with preview image | F6 | M | Needed the moment friends paste links |

### Wave 2: structure (the big visible change)
| Rank | Idea | Fixes | Effort | Why |
|------|------|-------|--------|-----|
| 6 | I1+I20 Home with search, Recent, Trending, Reply shortcut, first-visit line | F2, F8 | M | Answers "screen is empty" directly, max 3-4 widgets |
| 7 | I3+I4 Detail as two-column layout and quick-look lightbox | F6 | M-L | Largest UX gain for browsing |
| 8 | I11+I12 Header search, Ctrl-K, simpler nav, env badge | F3 | M | Public nav should show 3 items, not 4 menus |
| 9 | I2 Full-width responsive grid, sticky filters, result count, chips | F1, F7 | M | |
| 10 | I13 Back to results with scroll kept | | S | |

### Wave 3: later
I18 mobile pass (move earlier if friends will use phones), I17 accessibility, I24 performance, I23 language policy (needs your decision), I5 related rail, I10 masonry, I14-I16 admin dashboard / review layout / charts (owner-only, no public impact).

### Dependencies and risks
- Wave 1 item 1 blocks the rest; do it first.
- Any route/menu change needs a `ROUTE_POLICY` line (stage policy) and `visibility.ts` entry; Android has a parallel `FeatureVisibility.kt`.
- Card and modal changes touch many tests (MemeCard, MemesList, MemeDetails).
- `multi-image-stories-epic` worktree also edits comics UI (Add to comics on cards); coordinate to avoid conflicts on MemeCard.

## Progress (2026-11-19)

Done on branch `worktree-ux-design-audit` (reviewed by a separate agent, findings fixed, visually checked on a throwaway server):
- Ranking items 1-2: design tokens and shared components (`src/ui/`), card redesign with curator controls in a hover/focus action bar. Spec: `2026-11-18-design-system-and-card-redesign.md`.
- Ranking item 6: Home page at `/`. Spec: `2026-11-19-home-page-design.md`.

Follow-ups (owner-approved direction, not started):
- F-a Backend: exclude flagged images from the unfiltered `GET /api/images` feed (today only trending excludes them; Home filters them client-side on its Recently added rail). Changes Explore too; needs `backend_api.md` and tests. Separate small change.
- F-b Card edge: white-background memes lose their card edge without a border (hairline or grey card background).
- F-c Home rail shows a native horizontal scrollbar and a clipped last card; consider hiding the bar and adding arrow buttons.
- F-d Not yet seen in a browser: prod build (`VITE_STAGE=prod`) hiding curator controls, 390px phone layout, the comics status keeping the action bar visible. Unit tests cover the first and third.
- F-e A page froze the tab when its API fetch failed (observed once, cause not investigated); check error handling of the list fetch before a public launch.
- Ranking item 3 done (2026-11-20, branch `worktree-card-copy-download`): copy image, download, copy link and share on cards and in the details row. Spec: `2026-11-20-copy-download-actions-design.md`. Not yet checked in a real browser (clipboard, Safari, LAN-IP fallback): see that spec's manual acceptance list.
- Remaining ranking: items 4-5 (empty states beyond cards, shareable meme page), 7-10 (detail view, header search and nav, full-width grid, back-to-results).
