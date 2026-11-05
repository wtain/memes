# C3 comics management API and UI: review

Spec: `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-04-c3-comics-api-and-ui-design.md`
Plan: `docs/superpowers/plans/2026-11-05-c3-comics-api-and-ui.md`
Range reviewed: `0eb862a..f1afbc5` (nine tasks, one fix round on the editor page, one final fix wave).

Process: a task review after each of the nine tasks, one fix round on the editor page (Task 8) with a scoped re-review, a final
whole-branch review on the most capable model, one fix wave covering its findings and every parked minor, and one scoped
re-review. The full suites were run once at the end: Backend 745, `tests/docs/` 15, the whole `tests/integration/` root 1419,
frontend `tsc -b`, `eslint src/ --max-warnings 0` and vitest 985, and `tests/docker/` (a daemon was available) including the
new `batch.utils.comics_compose` import check.

## What was fixed

- Search thumbnails in the editor used the raw relative image URL and would break with a cross-origin backend (Task 8 review);
  they now go through the API object, with a test that cannot pass on the raw URL.
- The editor's add, move, remove and rename share one busy gate, so a double click or a reorder during an add cannot send a stale
  panel list; the editor remounts per comics id so preview and rename state do not leak between comics.
- The search column re-checked addability for all loaded results in one request, and the backend caps a request at 100 ids; after
  four "Show more" clicks the "+" of a just-added image never updated (final review, Important). Now chunked to 100.
- The meme-page block offered "Add to comics…" for images that cannot be added (flagged, pending, live cut), and "Create and add"
  left an empty comics behind with a misleading retry error (final review, Important). The block now checks addability and shows
  the reason; the picker keeps the created id on retry.
- Picker: auto-continues through pages with no drafts (bounded), clears conflict and error when the mode changes.
- List page: reloads after create instead of prepending a fabricated row; delete confirmation has a named button; race tests.
- Backend: a real-session preview test (real PNGs, a deleted file gives `unreadable` with the right position), a non-vacuous cover
  assertion, `record=0` and `record=maybe` endpoint tests, two whitespace slips, a wrong comment.
- Mock defaults for the comics API methods now have the right shapes.

## Accepted, not fixed (with reason)

- A failed preview has no retry button until the next panel change; re-submitting the same search query does not re-run it; the
  first preview of a draft is requested twice on the 0 to 1 panel transition. All cosmetic.
- A panel filename from the database is joined to `BASE_PATH` without a containment check; the recut services do the same and
  filenames come from the app's own ingestion.
- The preview holds the request's read transaction while it waits for the semaphore and composes; acceptable for a single admin.
- The Kotlin generator types the two inline objects (`ComicsAddability.items`, `ComicsByImage.comics`) as `JsonObject`; Android has
  no comics screens. Extract named schemas when it gets them (ruling R3).
- `ComicsPreview` re-implements `readDetailObject` and has no loading state.
- `move=true` on an image in no comics is a 404 `not_found`; a move whose source comics is built reports plain `built` (reachable
  today only by a race; becomes normal after C4, tracked as D11).

## Handoff to C4 (tracker D10, D11)

The built view cannot show whether a retired panel's source image still exists: `ComicsPanel` does not expose `sourceImageId`.
C4 decides how a kept panel is recorded (D10). After C4 the editor must also refresh after a `built` or `not_found` error and
say clearly when "Move from X" fails because X is built (D11).
