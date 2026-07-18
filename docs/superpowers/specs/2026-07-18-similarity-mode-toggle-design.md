# Similarity Mode Toggle — Design

Status: Draft

## Context

`docs/superpowers/specs/drafts/2026-07-17-descriptions-visualisation.md` proposed
three things; spec 1 (`2026-07-18-image-descriptions-display-design.md`,
implemented and merged) covered showing description text to users. **This
spec covers item (2): wiring the already-implemented
`GET /api/images/{id}/similar?source=image|description` semantic-similarity
mode into the Web and Android UIs.** Item (3) (approve/reject feedback) is a
separate, later spec.

The backend side of this needed nothing new — the `source` query parameter
was added in
`docs/superpowers/specs/2026-07-16-image-description-embeddings-similarity.md`
and is already documented in `backend_api.md`. Both clients today only ever
call it with the default (`source=image`, CLIP visual-embedding similarity);
neither has any UI to request `source=description` (LLM-description
text-embedding similarity). This spec is pure client-side wiring.

## Goals

- A per-view toggle ("Visual" / "Semantic") on both Web and Android's image
  detail view, switching which similarity mode the existing similar-images
  section/strip queries.
- Default to Visual on first load of a detail view (no behavior change for
  existing users until they interact with the toggle).
- A quiet, mode-specific message when the selected mode has nothing to show
  (today's similar-images section shows nothing at all in this case).
- Switching to a different image via the similar-images results carries the
  selected mode forward (deliberately, even though this costs a small
  navigation-argument addition on Android — see the Android section).

## Non-goals

- No backend changes — the API surface already exists.
- No global/persisted user preference (e.g. localStorage, DataStore) — this
  is per-view, in-memory state only.
- No distinction in the UI between "this image has no description embedding
  yet" (404) and "this image has an embedding but no similar candidates
  exist" (200, empty `items`) — both present identically as an empty result,
  and both platforms already collapse any fetch failure into "empty list"
  via existing infrastructure (see Design).
- No changes to the descriptions feature (spec 1) or approve/reject feedback
  (a future spec 3).

## Design

### Shared behavior

Both platforms already silently degrade *any* fetch failure for the
similar-images call to "empty list, no crash" — Web via `useFetchById`'s
swallowed `.catch` (no `onError` passed), Android via `runCatching` +
`.onFailure { }` in `MemeDetailViewModel.loadSimilar`. A `404` (no embedding
for the requested mode) already lands here today; nothing new is needed at
the fetch layer to handle it. The only gap is that neither UI currently
renders anything when the result is empty — this spec adds a mode-specific
message in that case:

- Visual, empty: **"No similar images found"**
- Semantic, empty: **"No semantic similarity available for this image yet"**

### Web (React)

- **`MemesApi`/`HttpMemesApi`**: `similarMemes(id: string, source?: "image" | "description"): Promise<MemeSearchResponse>`.
  `HttpMemesApi`'s implementation appends `?source=...` to the request URL
  only when `source` is passed, so an omitted `source` behaves exactly as
  today (backend defaults to `image`).
- **`MemeDetails.tsx`**: new state,
  `const [similarSource, setSimilarSource] = useState<"image" | "description">("image")`.
  The existing similar-images `useFetchById` call changes to use a
  **composite key** for its change-detection argument:
  ```ts
  useFetchById(
    `${meme.id}:${similarSource}`,
    () => memesApi.similarMemes(meme.id, similarSource),
    resp => setSimilarMemes(resp.items ?? []),
  )
  ```
  `useFetchById` itself is unmodified — it already treats any new key as
  "needs a fetch"; the fetcher closure ignores the key argument it's handed
  and reads `meme.id`/`similarSource` from closure instead.
- Because `similarSource` lives in the same component instance's state and
  is not reset when the `meme` prop changes, navigating to a different image
  through the similar-images grid **naturally carries the selection
  forward** — no extra code needed on Web.
- **UI**: two small tab/pill buttons, "Visual" and "Semantic", rendered
  directly above the existing "Similar images" grid, styled consistently
  with the rest of the detail view (plain buttons, active one visually
  distinguished — no new dependency). Clicking switches `similarSource`.
  When `similarMemes` is empty, render the mode-specific message from
  "Shared behavior" above in place of the grid.
- **Tests**: extend `MemeDetails.test.tsx` — switching tabs triggers a new
  `similarMemes` call with the corresponding `source`; the correct
  empty-state message renders per active tab; the selection persists across
  a simulated `meme` prop change (rerender with a different meme, same
  component instance).

### Android

- **`MemeApiService`**: `getSimilarMemes(@Path("id") id: String, @Query("source") source: String = "image"): MemeSearchResponse`.
- **`MemeRepository`**: `getSimilarMemes(id: String, source: String): Result<List<Meme>>`,
  passing `source` straight through. `source` stays a plain `String`
  end-to-end (API param, ViewModel state, nav argument) rather than a
  dedicated enum — it's a two-valued wire value used in one place; a
  wrapper type would be pure ceremony here.
- **`MemeDetailViewModel`**: reads an optional `source` argument from
  `SavedStateHandle` (default `"image"`) alongside the existing `memeId`,
  used as `DetailUiState.similarSource`'s initial value. New public
  `fun setSimilarSource(source: String)` updates state and re-triggers
  `loadSimilar()`, which now reads `source` from the current state when
  calling `repo.getSimilarMemes`.
- **Carry-over via navigation argument**: `MemeDetailScreen`'s
  `onNavigateToMeme` callback signature changes from `(String) -> Unit` to
  `(id: String, source: String) -> Unit`. Its only wiring site is
  `NavGraph.kt`'s `"detail/{memeId}"` composable (reached when tapping a
  similar-image thumbnail to open another detail screen) — the route
  becomes `"detail/{memeId}?source={source}"` with a `NavType.StringType`
  argument defaulting to `"image"`, and the navigate call passes the
  current screen's live `state.similarSource`. The other three entry points
  into `MemeDetailScreen` (Search, Flagged, Recommendations lists) are
  unaffected: they have no prior selection to carry, so they keep
  navigating to plain `"detail/$memeId"` and pick up the route's default.
- **UI**: two small chip/tab buttons ("Visual" / "Semantic") added directly
  above the existing thumbnail `LazyRow` inside `BottomActionBar`, wired to
  `viewModel.setSimilarSource`. When `similarMemes` is empty and not
  loading, render the mode-specific message from "Shared behavior" in place
  of the `LazyRow`.
- **Tests**: extend `MemeDetailViewModelTest` — `setSimilarSource` triggers
  a new `getSimilarMemes` call with the right source; a non-default `source`
  `SavedStateHandle` argument is honored as the initial state (carry-over
  from navigation).

## Error handling

Unchanged from today's existing similar-images behavior on both platforms:
any fetch failure (network error, 404, 5xx) degrades silently to an empty
list, which this spec makes visible via the mode-specific empty-state
message rather than rendering nothing.

## Testing

- **Web**: `MemeDetails.test.tsx` — tab switch behavior, per-mode empty
  state, selection carry-over across a meme change.
- **Android**: `MemeDetailViewModelTest` — `setSimilarSource` behavior,
  initial-state carry-over from a `source` nav argument.
- No backend test changes — the `source` query parameter and its 404
  behavior are already covered by
  `docs/superpowers/specs/2026-07-16-image-description-embeddings-similarity.md`'s
  implementation.
- **Manual**: visually confirm the toggle on both platforms against a real
  image that has a description embedding (via
  `batch/build_image_description_embeddings.py`) and one that doesn't, to
  see both the populated and empty-state paths on the Semantic tab.

## Rollout

No migration, no config change, no feature flag. Safe to ship as soon as
implemented — Semantic mode will show the empty state for any image whose
description hasn't been embedded yet, which is expected and handled.
