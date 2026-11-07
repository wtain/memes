# C7: Find neighbours in the comics editor

Status: done
Originates from: docs/superpowers/specs/2026-11-02-multi-image-comics/tracker.md (spike S3, idea a)
Follow-ups: none yet

## Intent

Panels of one story usually share a drawing style, so the images most similar to a panel are the best candidates for the next panel. The editor already has a search column; C7 lets the owner point it at "images similar to this panel" with one click, then add from the same list with the same controls.

## Decisions by the owner

- Build the "find neighbours" idea (2026-11-07). Everything below is my call.

## Decisions I made (my calls)

- **Entry point.** Each panel row of the draft editor's strip gets a "Neighbours" button (aria-label `Find neighbours of panel N`). It is disabled while the editor is busy and absent for a panel without an image (`imageId` null).
- **Mode.** The search column shows "Similar to panel N" with a "Back to search" button instead of the previous results. The result list, addability check, add button, and the states ("added", reasons) are the existing ones; only the source of the items differs. Submitting a text search or pressing "Back to search" leaves the mode. The id lookup of C6 does not apply in this mode. The mode is cleared when the pointed-at panel is removed.
- **Source and size.** CLIP image similarity (`/api/images/{id}/similar?source=image`), one request for `limit=60`, ordered by cosine distance as the API returns them, no paging (no "Show more" in this mode). The source image is not in its own neighbours (the existing query already excludes it); panels already in this comics show as "added".
- **No impressions.** The existing endpoint records the returned images as `similar` impressions. The editor must not feed popularity data, so `GET /api/images/{image_id}/similar` gets an optional `record` query parameter (default `true`, same meaning as on `GET /api/images`); the editor sends `record=false`. `backend_api.md` is updated. No other behaviour of the endpoint changes.
- **Failure.** A failed or 404 request (no embedding yet) shows the existing "Could not load results" alert.
- **Out of scope.** Description-based similarity sources, text-search filters, distance display, remembering the mode across page loads.

## Goals

- One click from a panel to its visually similar images, addable in place.
- No change to any recorded impression or popularity data.

## Done when

Backend: `record=false` records no impressions and the default still does (tests). Frontend: button, mode, back, panel-removed reset, text search leaves the mode, `record=false` sent (vitest); `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`, `cd Backend && pytest` pass; `backend_api.md` and the tracker updated; MemesApi `similarMemes` keeps working for its other caller.
