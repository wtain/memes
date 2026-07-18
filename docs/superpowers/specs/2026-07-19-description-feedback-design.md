# Description Approve/Reject Feedback — Design

Status: Draft

## Context

`docs/superpowers/specs/drafts/2026-07-17-descriptions-visualisation.md`
proposed three things. Spec 1 (`2026-07-18-image-descriptions-display-design.md`)
covered showing description text to users; spec 2
(`2026-07-18-similarity-mode-toggle-design.md`) covered the Visual/Semantic
similarity toggle. **This spec covers item (3): the ability to Approve or
Reject an individual AI-generated description, on both Web and Android.**

The draft's stated rationale: a binary feedback signal helps future
re-assessment of description quality and prompt/model tuning. No quality
strategy exists yet — this spec only captures the signal, it does not act on
it.

## Goals

- Per-description Approve/Reject buttons, inline next to each description
  entry, on both Web (`MemeDetails.tsx`) and Android (`DescriptionsBottomSheet.kt`).
- Tri-state per description: no feedback / approved / rejected. Clicking the
  currently-active button clears back to no feedback; clicking the other
  button switches directly.
- Feedback state is visible immediately on load (no extra round trip beyond
  the existing descriptions fetch).
- Basic aggregate counts (approved / rejected / total) surfaced in the
  existing `/api/diagnostics/statistics` endpoint.

## Non-goals

- No effect on anything else: a rejected description stays visible in the UI
  and continues to participate in semantic-similarity search
  (`source=description`, spec 2) exactly as before. This is pure data
  collection for future use — no quality-driven filtering exists yet.
- No per-user attribution. The app has no auth/user system today (single
  implicit user); feedback is a single global value per description, matching
  the existing `flagged` boolean's precedent.
- No description-quality dashboard, re-tuning tooling, or automated
  retraining loop — only the raw counts described above.
- No changes to the descriptions feature itself (spec 1) or the similarity
  toggle (spec 2).
- No editing of description text — mentioned as a further-out idea in the
  original draft, explicitly deferred there too.

## Design

### Data model

New table, one optional row per description (absence = no feedback given):

```python
class ImageDescriptionFeedback(Base):
    __tablename__ = "image_description_feedback"

    image_description_id = Column(
        UUID(as_uuid=True),
        ForeignKey("image_descriptions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    approved = Column(Boolean, nullable=False)  # True = approved, False = rejected
    created_at = Column(DateTime, server_default=func.now())
```

Added to `Storage/models.py`, with an Alembic migration
(`alembic revision --autogenerate -m "add image_description_feedback"`).

`image_descriptions` rows are not deleted/recreated on normal batch reruns —
`build_image_descriptions.py` only processes `(image, prompt)` pairs missing a
description, skipping existing ones (see `_images_missing_prompts` /
`_load_existing_pairs`). Rows are only wiped on an explicit `--reset`, which
cascade-deletes any feedback along with the description it was about — losing
feedback tied to text that no longer exists is correct, not a bug.

### Backend API

**Repository** (extends `Backend/app/repositories/`, following the existing
`flagged` mark/unmark pattern): given `(image_id, prompt_key)` — already
unique per the `image_descriptions` table's own constraint — resolve the
description row (404 if it doesn't exist), then upsert or delete the
feedback row to implement the toggle.

**Endpoints**, added to the existing images/descriptions router:

- `PUT /api/images/{image_id}/descriptions/{prompt_key}/approve`
- `PUT /api/images/{image_id}/descriptions/{prompt_key}/reject`

Each toggles: calling `approve` when the description is already approved
clears the feedback (deletes the row) instead of re-approving; calling
`reject` while approved switches directly to rejected (and vice versa). Both
return the resulting state:

```json
{ "feedback": "approved" | "rejected" | null }
```

404 if the `(image_id, prompt_key)` pair has no matching description.

**`GET /api/images/{id}/descriptions`** response gains a `feedback` field per
entry — `"approved" | "rejected" | null` — via a join against the new table.
This is a `shared/schemas/imagedescription.schema.json` change, requiring
regeneration of both the Web (`Frontend/generate-types.sh`) and Android
(`AndroidClient/scripts/generate_dtos.py`) generated types.

`backend_api.md` is updated for the new `feedback` field and the two new
endpoints, per this repo's API-contract convention.

### Statistics

`Backend/app/repositories/diagnostics_repository.py`'s existing single
combined-subquery statistics call gains three more scalar subqueries,
matching its current style exactly:

```python
select(func.count()).select_from(ImageDescriptionFeedback)
    .where(ImageDescriptionFeedback.approved == true())
    .scalar_subquery().label("descriptions_approved"),
select(func.count()).select_from(ImageDescriptionFeedback)
    .where(ImageDescriptionFeedback.approved == false())
    .scalar_subquery().label("descriptions_rejected"),
select(func.count()).select_from(ImageDescriptionFeedback)
    .scalar_subquery().label("descriptions_feedback_total"),
```

Surfaced as three new keys under `StatisticsResponse`'s existing `content`
block. `backend_api.md`'s documented response shape is updated to match.

### Web (React)

- `MemesApi`/`HttpMemesApi` gain
  `setDescriptionFeedback(imageId: string, promptKey: string, action: "approve" | "reject"): Promise<{ feedback: "approved" | "rejected" | null }>`,
  calling the corresponding `PUT` endpoint.
- `ImageDescription`'s regenerated type gains `feedback: "approved" | "rejected" | null`.
- `MemeDetails.tsx`: each description `<li>` gets an inline Approve/Reject
  button pair. On click, calls `setDescriptionFeedback`, **awaits the
  response** (this codebase's existing convention — e.g. `toggleFlagged` —
  is to wait for the server response before updating UI state, not update
  optimistically), then replaces that one description's `feedback` field in
  local `descriptions` state from the response.
- Buttons visually indicate the active state (e.g. highlighted when
  `d.feedback === "approved"` / `"rejected"`).
- **Tests**: extend `MemeDetails.test.tsx` — clicking Approve/Reject calls the
  right endpoint with the right action; UI reflects the returned state;
  clicking the already-active button clears it back to neutral; per-entry
  independence (approving one description doesn't affect another's buttons).

### Android

- `ImageDescription` Kotlin DTO (regenerated) gains `feedback: String?`.
- `MemeApiService.kt` gains two `@PUT` Retrofit methods for the `approve` and
  `reject` endpoints, returning the feedback-state response.
- `MemeRepository.kt` gains
  `setDescriptionFeedback(imageId: String, promptKey: String, action: String): Result<...>`,
  passing through to the API.
- `MemeDetailViewModel.kt` gains
  `setDescriptionFeedback(promptKey: String, action: String)`: calls the
  repo, and on success replaces the matching entry's `feedback` in
  `state.descriptions` (find by `promptKey`, copy with the updated field) —
  same wait-for-response pattern as Web.
- `DescriptionsBottomSheet.kt`: each description block gets an inline
  `IconButton` pair (`Icons.Filled.ThumbUp` / `ThumbDown`, already available
  in this project's compose-bom), highlighted when `description.feedback`
  matches, wired through a new `onFeedback: (promptKey: String, action: String) -> Unit`
  parameter threaded from `MemeDetailScreen`.
- **Tests**: extend `MemeDetailViewModelTest.kt` — same cases as Web,
  Kotlin-side. No new instrumented (`androidTest`) coverage strictly
  required, consistent with how spec 2's Task 4 handled UI-only additions in
  this sandboxed environment (compile-check only, no connected device
  available).

## Error handling

A failed approve/reject request (network error, unexpected 5xx) leaves the
button's state unchanged (since the UI only updates from a successful
response) — no optimistic state to roll back. A 404 (description doesn't
exist — shouldn't happen in practice since buttons only render next to an
already-fetched description) surfaces as a generic error consistent with how
this codebase already handles other unexpected-error cases in these
components.

## Testing

- **Backend**: `pytest` — repository toggle behavior (approve → approve again
  clears; approve → reject switches directly; reject → reject again clears;
  404 for an unknown `prompt_key`), the two new endpoints (mocked DB,
  matching `tests/test_images_endpoints.py` conventions), and the three new
  statistics subqueries.
- **Web**: `vitest run` — see Web section above.
- **Android**: `:app:testDebugUnitTest` — see Android section above.
- **Manual**: visually confirm the toggle behavior on both platforms against
  a real image with multiple descriptions, including the clear-on-re-click
  case.

## Rollout

Requires one Alembic migration (new table, additive only — no existing data
affected) and the shared-schema type regeneration on both clients. No feature
flag needed: the new `feedback` field defaults to `null` for every existing
description until a user interacts with the new buttons, so there's no
behavior change for anyone until they click Approve/Reject.
