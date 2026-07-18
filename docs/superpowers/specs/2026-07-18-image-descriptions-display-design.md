# Image Descriptions Display — Design

Status: Draft

## Context

`docs/superpowers/specs/drafts/2026-07-17-descriptions-visualisation.md` proposed
three things: (1) showing AI-generated image descriptions to users, (2) a
semantic-similarity (`source=description`) toggle in the UI, and (3)
approve/reject feedback on descriptions. This session decomposed that draft
into three independent specs, built in dependency order. **This spec covers
only (1).** Specs for (2) and (3) will be brainstormed separately once this
one ships, since both build on the UI surface this spec introduces.

Prerequisite work is already implemented:
- `image_descriptions` table (`Storage/models.py`'s `ImageDescription`) — one
  row per `(image_id, prompt_key)` pair, with `model_used` and `text`. See
  `docs/superpowers/specs/2026-07-13-multi-prompt-image-descriptions-design.md`.
- Only one prompt is configured today (`batch/data/image-description-prompts.general.yaml`:
  `general_description`), but the schema already supports multiple prompts
  per image.
- `docs/superpowers/plans/2026-07-16-image-description-embeddings-similarity.md`
  added `GET /api/images/{id}/similar?source=image|description` — already
  implemented and documented, but **not yet wired into either client's UI**
  (out of scope here; that's spec (2)).

Current gap this spec closes: **no endpoint exposes `ImageDescription` text
to any client**, and neither the web app nor the Android app renders
description text anywhere.

## Goals

- A new read-only endpoint that returns an image's description rows.
- Web (`MemeDetails.tsx`) and Android (`MemeDetailScreen.kt`) both render
  description text in the image detail view.
- Ship both platforms together (shared backend contract, small UI change on
  each side).

## Non-goals

- Semantic-similarity UI toggle (`source=description` on `/similar`) — separate spec.
- Approve/reject feedback on descriptions — separate spec.
- Editing descriptions, multi-model comparison — explicitly out of scope per the original draft.
- Any admin/auth gating — this is read-only display of existing data, same trust level as OCR text/tags already shown today.

## Design

### Backend

**New endpoint:** `GET /api/images/{image_id}/descriptions`, added to
`Backend/app/api/images.py` next to the existing `/{image_id}/similar` route
(same URL-shape convention — a sub-resource directly under the plain image
ID, not the `/meme/{id}/...` convention used for flagged actions).

- **Repository** — new method on `Backend/app/repositories/image_repository.py`:
  `get_descriptions(image_id: str) -> Sequence[Row]`, a direct SQLAlchemy
  query against `ImageDescription` filtered by `image_id`, ordered by
  `prompt_key` ascending for deterministic output. This follows the existing
  pattern of `get_meme_data`/`get_is_flagged` living directly in this
  repository, rather than delegating to the batch-oriented
  `repository/image_descriptions.py`.
- **Service** — `ImageService.get_descriptions(image_id)`: thin pass-through
  mapping rows to the response type.
- **Response shape** — a new shared schema, `shared/schemas/imagedescription.schema.json`:

  ```json
  {
    "promptKey": "string",
    "text": "string",
    "modelUsed": "string",
    "createdAt": "string"
  }
  ```

  The endpoint returns a bare `List[ImageDescription]` (no envelope),
  matching the existing `GET /api/concepts/for-image` convention
  (`Concept[]`) rather than wrapping in `MemeSearchResponse`. `modelUsed` and
  `createdAt` are included in the payload for future admin tooling even
  though neither client renders them in this spec.
- **Empty case** — an image with zero description rows returns `200 []`, not
  `404`. The "nothing to show yet" state is a client rendering concern, not
  a server error condition (batch pipeline coverage is incremental and eventually-consistent by design).
- `backend_api.md` gets a new `#### Get Image Descriptions` entry.
- Regenerating types (`Frontend/generate-types.sh`,
  `AndroidClient/scripts/generate_dtos.py`) picks up the new
  `ImageDescription` TS type / Kotlin DTO automatically once the schema file exists.

### Web (React)

- **`MemesApi`** (`src/api/MemesApi.ts`) + `HttpMemesApi`
  (`src/api/http/HttpMemesApi.ts`): add
  `getDescriptions(id: string): Promise<ImageDescription[]>`, calling
  `GET /api/images/{id}/descriptions` — same shape as the existing
  `similarMemes`/`getTopConceptsForImage` methods.
- **`MemeDetails.tsx`**: add `descriptions` state populated via the existing
  `useFetchById(meme.id, id => memesApi.getDescriptions(id), setDescriptions)`
  hook — the same lazy-fetch-on-mount pattern already used for similar
  images, concepts, and flagged status.
- **Placement**: a new "Descriptions" block positioned after "Text Lines"
  (OCR) and before "Tags" — grouping the two forms of extracted/generated
  text together, ahead of the tags derived from them.
- **Rendering**: each entry renders as a small label humanized from
  `promptKey` by replacing underscores with spaces and capitalizing only the
  first letter (`general_description` → "General description") followed by
  the description text. With one prompt configured today this renders as a
  single labeled paragraph; adding a second prompt later requires no UI
  change. An empty array renders one quiet line: "No description available."
- **Failure handling**: if the fetch itself fails (network/5xx), leave
  `descriptions` empty and let it render the same quiet empty state — no
  separate error UI, consistent with how `similarMemes`/`concepts` already
  degrade silently through `useFetchById`.
- **Tests**: extend `MemeDetails.test.tsx` with cases for populated
  descriptions, multiple entries, and the empty state.

### Android

- **`MemeApiService`**: add
  `@GET("api/images/{id}/descriptions") suspend fun getDescriptions(@Path("id") id: String): List<ImageDescription>`.
- **`MemeDetailViewModel`**: add a third parallel coroutine,
  `loadDescriptions()`, alongside the existing `loadMeme()`/`loadSimilar()`,
  populating a new `descriptions: List<ImageDescription>` state field. Same
  silent-fail convention as `loadSimilar` — no error snackbar on failure,
  since this is supplementary content, not the core image view.
- **UI**: a new info icon added to `BottomActionBar`'s icon row (alongside
  save/share/flag). Tapping it opens a Material3 `ModalBottomSheet`
  containing the description text(s), using the same label-humanization rule
  as web (underscores → spaces, first letter capitalized). If `descriptions`
  is empty when opened, the sheet shows the
  same "No description available" line rather than the icon being hidden or
  disabled — behavior stays predictable regardless of data state.
- **New file**: `DescriptionsBottomSheet.kt` — a small, focused composable,
  rather than growing `MemeDetailScreen.kt` further.
- **Navigation**: the bottom sheet is local UI state
  (`remember { mutableStateOf(false) }`) inside `MemeDetailScreen`, not a
  `NavGraph` destination — it's a transient overlay, not a new screen, so it
  doesn't participate in back-stack navigation.
- **Tests**: unit test for `MemeDetailViewModel.loadDescriptions()`,
  following whatever precedent exists for `loadSimilar()`'s test.

## Data flow

No new tables or batch changes — this spec is purely a read path over the
existing `image_descriptions` table. Request flow:

```
Client → GET /api/images/{id}/descriptions
       → ImageService.get_descriptions
       → ImageRepository.get_descriptions (SELECT ... WHERE image_id = ? ORDER BY prompt_key)
       → [ImageDescription] JSON (Web) / DTO list (Android)
       → rendered in MemeDetails.tsx / DescriptionsBottomSheet.kt
```

## Error handling

- Zero description rows → `200 []`, rendered as a quiet empty-state line on
  both platforms (never a 404 or visible error).
- Unreachable server / non-2xx response → both clients treat it identically
  to a zero-row response (silent degrade), matching the existing
  `similarMemes`/`loadSimilar` convention. No retry logic in this spec.
- Malformed `image_id` → follows whatever existing validation the sibling
  `/similar` and `/meme/{id}` routes already apply (no new validation logic
  needed here).

## Testing

- **Backend**: integration test for `ImageRepository.get_descriptions`
  (pattern: `tests/integration/test_backend_image_repository.py`), router
  test in `Backend/tests/test_images_endpoints.py` (mocked service, asserts
  the empty-list-not-404 behavior explicitly).
- **Web**: `MemeDetails.test.tsx` — populated, multi-entry, and empty-state
  cases.
- **Android**: `MemeDetailViewModel` unit test for `loadDescriptions()`
  success/failure, matching the existing `loadSimilar()` test if one exists.
- **Manual**: per this repo's pre-commit checklist — confirm the Backend
  server starts without import errors, hit
  `GET /api/images/{some_id}/descriptions` manually and verify the response
  shape, and visually confirm the new UI section/sheet on both platforms
  against a real image that has a description.

## Rollout

No migration, no config change, no feature flag — this is additive read-only
surface over existing data. Safe to ship to all three environments
(metal/general/IT) as soon as it's implemented, though only `general` has a
populated `image_descriptions` table today per the current prompts-file
config.
