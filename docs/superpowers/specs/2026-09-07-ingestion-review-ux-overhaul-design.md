# Ingestion Review UX Overhaul

status: done
Plan: docs/superpowers/plans/2026-09-07-ingestion-review-ux-overhaul.md
Originates from: developer workstation notes (`scratch_50-notes-and-work-tracker.txt`, "Ingestion UI/UX" list, 2026-09-07)

## Problem

The Ingestion Review page (`Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx`, route
`/ingestion`) is the human bottleneck of the ingestion pipeline: for every new-image batch, a
reviewer walks Tier A then Tier B cluster queues deciding keep/reject per member. In daily use
on the `general` environment it is slow and click-heavy:

1. **Images are cropped.** Tiles render `object-cover h-32` — the reviewer sees a 128px-tall
   center crop, not the whole meme. The text (usually the only difference between near-dupes)
   is `line-clamp-3`'d and often off-screen.
2. **Reading each image needs a modal round-trip.** To see a full image the reviewer clicks the
   tile (opens `Modal`), reads, clicks to close, repeats for every other member of the cluster.
3. **Opening the modal stalls ~2s.** Not a re-download (tile and modal use the same
   `max-age=1yr immutable` URL) — a full-resolution main-thread decode, because the tile only
   ever decoded the image at 128px.
4. **The whole page feels laggy.** Every cluster is rendered at once (no virtualization) and
   every full-resolution image downloads on mount (no `loading="lazy"`). Each `setDecision`
   re-renders the entire list.
5. **Buttons give no click feedback**, so the reviewer double-clicks or waits unsure.
6. **The "Submit all" button scrolls away** — it sits above the list, so bulk-submitting means
   scrolling back to the top.
7. **Submitted clusters don't visibly disappear.** After a submit the page refetches, but the
   resolved clusters appear to linger until a manual page refresh.
8. **OCR text shown looks like transliterated garbage, not Russian**, even though most
   `general` memes are Russian. Investigation (`scratchpad/ru-ocr-investigation.md`, 2026-09-07)
   found this is **not** an OCR-coverage problem — Tesseract `rus` ran in the ingestion prepass
   for 97% of Tier A members, stored Cyrillic text at confidence 0.85, and none of it is
   dropped. The cause is `get_ocr_texts` (`Backend/app/repositories/ingestion_repository.py:86`):
   it space-joins the `ru` + `en` + `es` OCR rows with **no ordering, no `lang_score` gate, no
   dedup**. The `en`/`es` EasyOCR readers run on every image regardless of script and emit
   transliterated-Latin noise (`"Haka3aha 40 cpepbl"`) that is ~57% of the concatenated blob,
   and because there is no `ORDER BY`, ~8% of members show only that noise in the 3-line clamp.

The queue can hold **hundreds of clusters** at once.

## Goals

- Show every cluster member's whole image and full OCR text without a modal round-trip.
- Make the page responsive with hundreds of clusters loaded.
- Give immediate visual feedback on every click.
- Keep bulk-submit reachable at any scroll position, and make resolved clusters leave the list
  immediately.
- Make the OCR text shown in review lead with the language actually on the image (Russian for
  `general`), by fixing how `get_ocr_texts` orders and filters rows.

## Non-goals

- Improving the OCR *recognizer* (Tesseract `rus` tuning, a vision-model OCR fallback). The
  RU text is already there and usable; only its presentation is broken. A better recognizer
  matters only for downstream search/tag quality and is tracked separately.
- Returning OCR text per-language in the cluster payload (a schema change). The `lang_score`
  ordering fix below makes the RU-first text good enough for review; a labelled per-language
  field can be a later follow-up if reviewers still want the EN/ES text visible and separated.
- Any change to the ingestion batch pipeline, the `resolve` semantics, or Tier A/B banding.
- Server-side image resizing / a thumbnail endpoint (rejected — would add Pillow to the backend
  image and Dockerfile; the hybrid layout below makes tiles near-full-resolution anyway, so the
  docked/modal view is a cache hit).
- Backward-scrolling / URL-resumable windowing (the duplicates explorer has it; this queue is
  worked strictly front-to-back and items leave as they're resolved).

## Design

### 1. Backend — paginate `GET /api/ingestion/clusters/{tier}`

**New query params:** `cursor` (opaque string, optional) and `limit` (int, default `40`,
max `200`).

**Response shape changes** from `Cluster[]` to:

```json
{
  "items": [ { "members": [...], "edges": [...] }, ... ],
  "next_cursor": "0.042100|b3f1c2a4-...",
  "has_next": true
}
```

`next_cursor` is `null` when the page is the last one.

**Implementation** (`IngestionService.list_clusters`, `Backend/app/services/ingestion_service.py`):

- The full union-find still runs on every call. `get_tier_candidate_rows` returns every
  in-band, not-yet-reviewed pair for the batch — at "hundreds of clusters × ~2–5 members"
  that is ~1–2k rows; rebuilding the union-find per page is cheap and avoids inventing a
  DB-level clustering pagination scheme.
- **Deterministic cluster ordering:** sort clusters by `(min edge distance in cluster,
  min member image_id)` ascending. Tightest matches — the most likely true duplicates —
  surface first, and the tiebreak on `image_id` makes the order total and stable.
- **Cursor** encodes the last-returned cluster's sort key `(min_distance, min_image_id)` as
  `f"{min_distance!r}|{min_image_id}"` — `repr()` of the float round-trips exactly through
  `float()`, so the tuple comparison stays exact and no boundary cluster is skipped or
  repeated (a `:.6f`-style truncation can drop a cluster whose distance agrees with its
  neighbour's to 6 decimals but differs beyond). Same field order as the sort so the
  comparison is a plain tuple `>`. Decoding is tolerant: an unparseable/blank cursor is
  treated as "from the start" (no 400 — a stale bookmark just restarts the queue). Slice is
  "first `limit` clusters whose `(min_distance, min_image_id)` is strictly greater than the
  decoded cursor tuple".
- `has_next` = there is at least one cluster after the slice.
- No change to `edges` / `members` payload per cluster, and no new columns selected —
  `distance` is already in the rows `get_tier_candidate_rows` returns.

**Types** — this repo keeps hand-written JSON schemas in `shared/schemas/` as the source, with
matching hand-written Pydantic models in `Backend/app/api/ingestion.py` and generated
TS / Kotlin from the schemas:

- New `shared/schemas/ingestionclusterpage.schema.json` (`items` → array of
  `ingestioncluster.schema.json`, `next_cursor` → `["string","null"]`, `has_next` → boolean);
  register it in `shared/schemas/all.schema.json`.
- New Pydantic `ClusterPage` in `Backend/app/api/ingestion.py`
  (`items: list[Cluster]`, `next_cursor: str | None`, `has_next: bool`); `list_clusters` route
  gains `cursor: str | None = None`, `limit: int = Query(40, ge=1, le=200)` and
  `response_model=ClusterPage`.
- Regenerate TS: `bash Frontend/generate-types.sh`. Regenerate Kotlin:
  `./AndroidClient/scripts/generate_dtos.py` (Android has no ingestion screen but the DTO
  generator consumes every schema — keep it in the same commit so CI's generated-file diff
  gate stays green).

**`backend_api.md`:** update the "List Clusters" section — new params, new response model,
new example.

**Tests** (`Backend/tests/test_ingestion_endpoints.py`, `test_ingestion_service.py`):

- First page returns `limit` clusters, `has_next: true`, a non-null cursor.
- Passing that cursor returns the next disjoint set, correct `has_next` on the final page.
- Ordering is by tightest edge distance.
- Blank / malformed cursor ⇒ starts from the beginning, no error.
- Empty queue ⇒ `{ items: [], next_cursor: null, has_next: false }`.

### 2. Backend — fix `get_ocr_texts` ordering and filtering (#2, RU OCR)

`Backend/app/repositories/ingestion_repository.py::get_ocr_texts` today:

```python
select(OCRText.image_id, OCRText.text, OCRText.confidence).where(OCRText.image_id.in_(image_ids))
# ... drop confidence < 0.3, then " ".join(parts) in arbitrary row order
```

Change it to:

- **Also select `OCRText.lang_score` and `OCRText.language`.**
- **`ORDER BY lang_score DESC NULLS LAST, confidence DESC`** in the query, so the most
  language-plausible block leads the concatenated text regardless of which OCR run inserted
  it. (RU rows average `lang_score` 0.87; the EN/ES transliteration rows average 0.36.)
- **Gate on `lang_score`** in addition to confidence. `get_ocr_texts` gains two parameters —
  `confidence_min: float` and `lang_score_min: float` — and drops a row when
  `confidence is not None and confidence < confidence_min` **or**
  `lang_score is not None and lang_score < lang_score_min`. The **service** (`list_clusters`)
  passes `settings.OCR.CONFIDENCE_MIN` (0.4) and `settings.OCR.LANG_SCORE_MIN` (0.3) — the
  same keys `build_bow` / `build_tags_from_ocr` use — keeping the repository config-agnostic,
  exactly as `get_blocked_pending_ids` already does with its threshold args.
  **Do not** import `rules.lang_plausibility.passes_language_filter` — that module imports
  `wordfreq`, which is not a backend dependency and must not become one. Inline the two
  comparisons. `lang_score` is a stored column (computed at batch time); no scoring at request
  time.
- **De-dupe** exact-duplicate block texts before joining (a `seen: set[str]` per image) — the
  same line often lands in multiple language rows.
- The old hardcoded `confidence < 0.3` cut is replaced by the passed `confidence_min` (0.4).
  If that shifts the "kept OCR" set materially the service/repository tests will catch it —
  acceptable, 0.4 is the documented project threshold.

Behaviour for `metal` / `IT` (English corpora) is unchanged in practice — English rows there
already score high on both axes.

**Tests** (`test_ingestion_service.py` / a new `ingestion_repository` unit test): given mixed
ru/en/es rows with the real-world score profile, the returned string leads with the RU text,
omits the low-`lang_score` Latin rows, and has no duplicate lines.

### 3. Frontend — API client + types

`IngestionClusterPage` is now in `types/generated/all.d.ts` (from §1's schema work). Then:

- `MemesApi.getIngestionClusters(tier, cursor?)` → `Promise<IngestionClusterPage>`
  (breaking signature/return change — only caller is `IngestionReviewPage` + tests + `mockApi`).
- `HttpMemesApi.getIngestionClusters`: append `?cursor=&limit=` , return the parsed page.
- `test/mockApi.ts`: return a `{ items, next_cursor: null, has_next: false }` page; add a
  paginating variant for the new pagination test.

### 4. Frontend — virtualized infinite list

`IngestionReviewPage` renders the cluster list through `<Virtuoso>` (`react-virtuoso`, already
a dependency; simpler use than `MemesDuplicatesList` — no `useWindowedPagination`, no backward
load, no `firstItemIndex` juggling):

- Local state `clusters: IngestionCluster[]`, `nextCursor: string | null`, `hasNext: boolean`,
  `loadingMore: boolean`.
- Initial `load()` fetches page 1 (cursor `undefined`), replaces `clusters`.
- `<Virtuoso useWindowScroll data={clusters} endReached={loadMore} increaseViewportBy={{ top: 400, bottom: 1200 }} />`.
  `useWindowScroll` matches `MemesDuplicatesList` and composes with AppLayout's existing
  `sticky top-0` header and the fixed action bar (§6) with no height math. `loadMore` is a
  no-op while `loadingMore` or `!hasNext`; otherwise fetches `nextCursor`, **appends** to
  `clusters`, updates cursor/flag.
- `itemContent` renders one `<ClusterRow>` (below).
- Footer component shows "Loading more…" while `loadingMore`, "N clusters reviewed — queue
  empty" when `!hasNext && clusters.length === 0` after load.
- Per-image `loading="lazy"` + `decoding="async"` on every `<img>` so only on-screen tiles
  fetch/decode. This alone removes the "download every full-res meme on mount" cost.

**Decision state.** Keep `decisions: Record<imageId, "keep" | "reject" | undefined>`. The two
existing housekeeping effects stay, adapted:

- The `[tier]`-keyed "wipe decisions when the tier changes" effect is unchanged.
- The `[clusters]`-keyed "prune decisions whose target is no longer pending" effect stays but
  must not fight infinite-scroll: pruning is keyed on membership in the **currently loaded**
  cluster set, which now grows as you scroll. That's fine — a decision is only pruned when its
  image drops out of a cluster that is still loaded, which is exactly the post-resolve /
  post-reload case it exists for. A cluster scrolling out of the virtual viewport does **not**
  remove it from `clusters` (Virtuoso only unmounts the DOM), so decisions are not lost by
  scrolling.

### 5. Hybrid image presentation (#1, #3, #8)

**`<ClusterRow>`** — one card per cluster:

- Members laid out in a horizontal `flex` strip inside an `overflow-x-auto` container (wide
  clusters scroll horizontally, the page body never does).
- Each **`<MemberTile>`**:
  - `<img>` rendered `object-contain` at `max-h-[55vh] w-auto` — the whole image, uncropped.
  - Filename (unchanged, `truncate` + `title`).
  - Status pill (unchanged).
  - **Full OCR text**, no `line-clamp` — `whitespace-pre-wrap break-words text-[13px]` in a
    `max-h-40 overflow-y-auto` box so a pathological OCR dump can't blow out the row height.
  - Edge labels (unchanged).
  - Keep / Reject buttons for `pending` members (unchanged logic, restyled per §6).
  - `tabIndex={0}` + `onMouseEnter` / `onFocus` → open the docked pane for this member;
    `onMouseLeave` / `onBlur` → close it (small close delay so moving the cursor into the
    pane doesn't dismiss it). `Enter` on a focused tile opens the existing pixel-peek `Modal`.

**Docked detail pane** — a `position: fixed` right-side panel (`w-[40vw]`, full viewport height,
`z-40`, slide-in transition on `translate-x`), rendered only when a member is hovered/focused:

- The **same image URL** already rendered in the tile (`getImageUrlById(memberId)`) shown at
  natural size in a scroll container — a browser cache hit, and with `decoding="async"` no
  main-thread stall. `onMouseEnter` of a tile also fires a `new Image().src = url` preload as
  belt-and-suspenders for the first hover.
- Full OCR text, all edge labels, and (for pending members) large Keep / Reject buttons
  wired to the same `setDecision`.
- Does not push page layout (overlays the right edge); the list keeps a `pr-[40vw]` gutter on
  wide viewports so nothing important sits under it. On narrow viewports (< `lg`) the pane is
  suppressed and the inline `object-contain` view + `Modal` fallback carry the load.

The existing click-to-enlarge **`Modal`** stays exactly as-is as the true 100%/pan fallback.

### 6. Sticky action bar, optimistic removal, click feedback

**Persistent action bar (#6).** A `position: fixed` bar pinned to the bottom-centre of the
viewport (`fixed bottom-4 left-1/2 -translate-x-1/2 z-40`, pill-shaped, shadowed), rendered
whenever there is at least one undecided-but-marked cluster. It holds the **Submit all
decisions** button with its live `(N clusters, M images)` count and the existing 3-second
`confirmingAll` two-step, plus a compact run/stage indicator. Fixed-position sidesteps any
sticky-offset math against AppLayout's own `sticky top-0` header. The full `StatusBanner`
stays at the top of the page (scrolls away — it's reference info, not an action); the
per-cluster "Submit decisions" button stays on each card.

**Optimistic removal (#7).** `submitCluster` / `submitAll`:

1. Compute the decided pending image-ids being submitted and which cluster indices they belong
   to.
2. **Immediately** remove those fully-resolved clusters from `clusters` (a cluster is
   "fully resolved" when every one of its `pending` members now has a decision in this
   submit). Clear their entries from `decisions`. Stash the removed clusters keyed by a temp
   id.
3. Fire the `resolveIngestionCluster` call.
4. On success: if the response has `failed` / `move_failed` entries, **re-insert** the
   affected clusters at their original position and surface the existing
   `formatResolveSummary` message. Otherwise drop the stash. Do **not** call the full
   `load()` on the happy path — the list already reflects reality; a background
   silent refetch of page 1 is acceptable only if we later find drift, but the default is no
   refetch, no scroll jump.
5. On throw: re-insert everything, show the error.

This also sidesteps the "clusters linger until refresh" bug regardless of its root cause
(likely: `load()`'s promise chain replacing `clusters` wholesale interacts badly with an
in-flight `endReached` append, or a partially-resolved cluster legitimately staying). A short
note goes in the code; a deeper fix isn't in scope because optimistic removal makes it moot.

**Click feedback (#4).** All decision + submit buttons get:

- `transition-colors` + an `active:scale-[.97] active:brightness-95` press state.
- The instant a submit starts, the button shows its `Submitting…` label and `disabled`
  (already the case) — plus a small inline spinner so it reads as "working", not "stuck".
- Keep/Reject selection already flips synchronously; once the list is virtualized (§4) that
  re-render is cheap and the highlight is instant. No separate animation library
  (`framer-motion` etc.) — Tailwind transitions only.

## Data flow (happy path)

```
mount
  → getIngestionRunStatus()  ── null ─→ "no run" view
        │ run + stage
        ▼
  tierForStage(stage) = tier
  getIngestionClusters(tier, undefined)
        → { items, next_cursor, has_next }
  setClusters(items); setNextCursor; setHasNext

scroll to bottom
  → Virtuoso endReached → loadMore()
        getIngestionClusters(tier, nextCursor)
        → setClusters(prev => [...prev, ...items])

hover a tile
  → setActiveMember({ clusterIdx, memberId }) → docked pane renders (cached img)

click Keep/Reject
  → setDecision(memberId, d)   (synchronous, cheap re-render)

click "Submit all"  (2-step confirm)
  → remove fully-resolved clusters from `clusters` NOW
  → resolveIngestionCluster(tier, decisions)
        ok, no failures   → drop stash
        ok, with failures → re-insert failed clusters + summary msg
        throw             → re-insert all + error msg
```

## Testing

- **Backend:** `cd Backend && pytest tests/test_ingestion_endpoints.py tests/test_ingestion_service.py`
  — pagination (cursor round-trip, ordering by tightest edge, `has_next`, malformed cursor,
  empty), and `get_ocr_texts` ordering/`lang_score` gate/dedup (§2). Confirm the server still
  boots (`/api/diagnostics/health`, `/api/images?limit=1`). Also run the wider ingestion test
  surface if `get_ocr_texts`'s threshold change ripples: `pytest tests/ -k ingestion`.
- **Frontend:** `IngestionReviewPage.test.tsx` rewritten for: paginated load + `endReached`
  append; hybrid layout (whole image, full OCR present, no `line-clamp`); docked pane on
  hover reuses the tile URL; sticky bar present; optimistic removal on submit + re-insert on
  `failed`/`move_failed`/throw; button press/disabled states.
- `tsc -b`, `eslint src/` (0 warnings), `vitest run`.
- Regenerate types: `bash Frontend/generate-types.sh` then
  `git diff --exit-code Frontend/memes-frontend/src/types/generated/`.
- Manual: run the three checks against a real `general` ingestion queue if one is active
  (never bind the always-occupied env ports — use the dev servers already running).

## Rollout / risk

- The clusters endpoint response shape is **breaking**. Only consumer is this page (+ Android
  has no ingestion screen — confirmed: `grep -ri ingestion AndroidClient/` is empty). Ship
  backend + frontend + `backend_api.md` + generated types in one branch.
- `<Virtuoso useWindowScroll>` (as `MemesDuplicatesList` uses it) — but this page needs **none**
  of that component's `firstItemIndex` / `useWindowedPagination` / `rangeChanged`-cursor
  machinery, which exists only for URL-resumable scroll position. Here it's a plain
  append-on-`endReached` list. Don't copy `MemesDuplicatesList` wholesale; it's a much smaller
  use of Virtuoso.
- Docked pane + `pr-[40vw]` gutter is desktop-review ergonomics; the narrow-viewport path
  falls back to inline `object-contain` + `Modal`, no functionality lost.
