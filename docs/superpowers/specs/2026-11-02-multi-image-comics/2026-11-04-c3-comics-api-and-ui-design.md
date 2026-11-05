# C3: Comics management API and UI

Status: done
Plan: docs/superpowers/plans/2026-11-05-c3-comics-api-and-ui.md
Originates from: docs/superpowers/specs/2026-11-02-multi-image-comics/README.md (tracker project C3)
Follow-ups: none yet

## Intent

Give the owner a place to assemble a comics: create and name it, add images to it (from the meme page or by searching inside the
comics page), put the panels in order, see the tiled result before committing, and find the comics again later. C3 delivers the
HTTP API over the C1 repository, the C2 preview, and the web pages. It does **not** build: the Build button is present but
disabled until C4 (see "Handoff to C4"). Nothing in C3 moves a file, flags an image or changes an image's status.

## Decisions by the owner (brainstorming, 2026-11-04)

- **Finding comics.** The comics list has its own search, by name. Finding *panels* happens inside the comics page: the page has a
  search column that uses the existing meme search.
- **Layout A, two columns.** Left: the comics (name, state, panel strip with order controls, live preview of the tiled result, Build).
  Right: search box and results, each with a "+" that adds the image as the last panel. On a narrow window the columns stack.
- **Unavailable results are shown, disabled, with the reason.** An image already in another comics, a flagged image, an image with a
  live recut cut: the "+" is greyed out with the reason, and "in comics X" links to that comics. Hiding them was rejected.
- **Entry points.** A new admin page `/admin/comics` (searchable list, "New comics"), and an "Add to comics…" action on the meme
  details page that opens a picker (existing draft comics, or create new) and adds that meme as the last panel.
- **Built comics stay forever.** A draft can be deleted at any time after a confirmation (its images stay in the library
  untouched). A built comics is a permanent record: shown in the list with the result image, the panel provenance and a link to the
  built meme; it cannot be deleted or have its panels changed.
- **Optional keeping of sources at build time** (owner request, affects C4, recorded here because the Build dialog is entered from
  this page): the Build dialog lists every panel with a "keep this image in the library" checkbox, all unchecked by default. A kept
  image stays active and is not flagged or moved; it passes no popularity to the built image; after the build it is **released**:
  the comics keeps a provenance record of the panel, but the image is free to join another comics. See "Handoff to C4".

## Decisions I made (my calls, to check)

- **Rename of a built comics is refused** (the C1 repository already raises `built`). I earlier offered "rename allowed"; the repository
  rule is stricter and ties the name to the `comics-sources/<name>/` directory, so I kept it. Overrule this and the repository, not
  only the UI, has to change.
- **Reorder** uses up/down buttons on each panel. Drag and drop is a later extra, not part of C3.
- **Search from the comics page records nothing:** no impressions and no search outcome. `GET /api/images` gets an optional query
  parameter `record=false` (default true, behavior unchanged); the comics page always sends it. Rationale: an admin tool must not
  inflate the popularity signals.
- **Which search.** The search column calls `MemesApi.searchMemes`, which is `GET /api/images` (smart search included: it is one
  endpoint and one service path). `record=false` suppresses both the impressions and the `searchId`. A result carries `id`,
  `imageUrl` and `flagged`, enough for the cover and for the `addability` call.
- **No access control to add.** The existing admin routers (recut, batches) have no auth dependency; `/api/comics` adds none.
- **Previews of small drafts.** With 0 panels the editor does not call the preview and shows an empty state. One panel previews
  fine (the panel with its margin). The endpoint itself answers 422 `compose_failed` with reason `no_panels` for an empty draft.
  `ComposeError.position` is 0-based; the service converts to 1-based for the response.
- **Locks.** `add_image`, `move_image`, `remove_panel` and `reorder` already take the comics row lock inside the repository; the
  service adds nothing. A reorder carrying the wrong set of panel ids is 422 `reorder_mismatch`; the same ids in a different order
  from a stale view is last write wins (no version token).
- **`addability`** runs a fixed number of batched queries for up to 100 ids (not one per id), with the same precedence as
  `_check_addable` (not_found, not_active, flagged, live_cut, then in_comics), through a shared helper. `by-image` looks at panel
  rows only, so a released kept panel (no `image_id`) and a retired panel both give `null`.
- **Docker.** `batch/utils/comics_compose.py` is not yet in `Dockerfile.backend` or `.dockerignore`; C3 adds it (and anything it
  imports) in both, and the `tests/docker` smoke test imports it.
- **An image already in the open comics** shows "added" in the results instead of a "+".
- **The preview refreshes** after each change that alters the panels (add, remove, reorder), debounced by 400 ms, and replaces the
  previous image when it arrives. It is a plain `<img>` pointing at the preview endpoint with a cache-busting `v` parameter.
- **The list has no total.** The service asks the repository for `limit + 1` rows and returns `hasMore`; the page shows a "Show more"
  button. (The repository's `search` has no count and C3 does not add one.)
- **Cover image of a list row:** the built image for a built comics, else the first panel's image, else none.
- **Preview concurrency** is capped by a process-wide semaphore of 2 in the preview service; a third concurrent preview waits.
- **Panel images** in the strip and the picker use the existing image URL (`/api/images/{id}`); no new thumbnail machinery.

## API

All endpoints live in a new router `Backend/app/api/comics.py` (`/api/comics`), a service `Backend/app/services/comics_service.py`
(maps `ComicsError` and shapes responses; builds the preview) over `repository/comics.py` (C1). Static paths are declared before
`/{comicsId}`. Response and request models come from new shared schemas in `shared/schemas/` with generated TypeScript, Kotlin and
Python types (the full chain, per the repo rules). `backend_api.md` documents every endpoint.

| Endpoint | Purpose |
|---|---|
| `GET /api/comics?q=&limit=&offset=` | list, newest first; `q` is the name search (C1 `search`). Items: id, name, state, panelCount, coverImageId, createdAt, builtAt. Returns `{items, hasMore}` |
| `POST /api/comics` `{name}` | create a draft. 201 with the detail |
| `GET /api/comics/{id}` | detail: id, name, state, builtImageId, builtAt, panels in order (panelId, position, imageId or null, sourceFilename) |
| `PATCH /api/comics/{id}` `{name}` | rename (draft only) |
| `DELETE /api/comics/{id}` | delete a draft. 204 |
| `POST /api/comics/{id}/panels` `{imageId, move=false}` | add as last panel. `move: true` takes the image out of the comics that holds it (C1 `move_image`); without it an image in another comics is a 409 |
| `DELETE /api/comics/{id}/panels/{panelId}` | remove a panel; positions are renumbered |
| `PUT /api/comics/{id}/panels/order` `{panelIds}` | reorder; the list must be exactly the comics' panels |
| `GET /api/comics/{id}/preview?maxWidth=1200` | `image/jpeg`, `Cache-Control: no-store`, composed by C2 `compose_preview`; `maxWidth` 200 to 2400. Draft only |
| `GET /api/comics/addability?imageIds=a,b,...` | for up to 100 ids: per id `{addable, reason?, comicsId?, comicsName?}`; reasons `in_comics`, `flagged`, `live_cut`, `not_active`, `not_found`. New repository method `addability(image_ids)` sharing the checks of `_check_addable` |
| `GET /api/comics/by-image/{imageId}` | `{comics: {id, name, state, position} \| null}`, for the meme page |

**Errors** are `HTTPException` with `detail` an object `{code, ...}`: `not_found` 404; `name_taken`, `built`, `image_in_comics`
(with `comicsId`, `comicsName`), `image_not_addable` (with `reason`) 409; `name_invalid` (with `reason`), `reorder_mismatch` 422.
A preview that cannot be composed is 422 `{code: "compose_failed", reason, position}` (position 1-based for display; the C2 reasons
`unreadable`, `animated`, `unsupported_format`, `too_large`, `no_panels`, `write_error`); a panel row whose image is missing from the
library cannot occur in a draft (the row is deleted with the image), a missing *file* is `unreadable`.

**Transactions.** Repositories never commit; `get_async_db` commits at the end of the request and rolls back on an exception. The
preview opens no write. The preview runs `compose_preview` in a worker thread (`asyncio.to_thread`) because it reads files and
resamples; file paths are resolved from `BASE_PATH` the way `recut_files.py` does. The backend image therefore needs Pillow and
`batch/utils/comics_compose.py` (`Dockerfile.backend`, `.dockerignore`), as recut did for `recut_cut.py`.

## UI

- **`/admin/comics`** (`ComicsListPage`): search box (debounced, by name), "New comics" (inline name field; a 409 `name_taken` or a 422
  `name_invalid` shows its reason in words), a grid of rows (cover, name, state badge, panel count), delete on drafts with a
  confirmation, "Show more". Clicking a row opens the editor.
- **`/admin/comics/:id`** (`ComicsEditorPage`), draft layout A as above. Left column: name with an inline rename; panel strip (each
  panel: thumbnail, 1-based number, up, down, remove); the preview area with its loading state and, on a compose error, the reason
  in words and the 1-based panel number; the Build button (disabled, with the explanation, in C3). Right column: the search column.
  A built comics shows the result image, the panel provenance list (position and source filename; whether the source image
  still exists in the library is left to C4, see D10) and a link to the built meme; no controls.
- **Search column.** Uses the existing search function of `MemesApi` with `record=false`. After each page of results arrives, one
  `addability` call for those ids decides each "+"'s state. Adding calls `POST .../panels`, then refreshes the comics and the
  addability of the visible results. An `image_in_comics` 409 is not shown as an error: the "+" is already disabled with a link.
- **Meme page.** A small block in `MemeDetails` (or the curator panel, where the other owner-only actions live; the builder picks the
  closer fit) calls `by-image`: if the image is in a comics it shows "In comics X (panel n)" with a link; otherwise "Add to comics…"
  opens a modal listing draft comics with a name search and a "New comics" option. Choosing one calls `POST .../panels` and shows
  the result with a link to the editor. Adding an image that is in another comics offers "Move from X" (`move: true`).
- All three pages follow the existing conventions: strict-mode safe effects, React Compiler lint rules, pure helpers and a reducer
  where state is non-trivial, generated types only.

## Handoff to C4

C3 leaves the Build button and nothing behind it. C4 owns: the build endpoint (`POST /api/comics/{id}/build` with `keepPanelIds`),
the Build dialog with the keep checkboxes, the file moves, the status changes and the derived data. Two things C3 asks of C4:
(1) take `ComicsRepository.get(lock=True)` first; (2) decide how a released kept panel is recorded on a built comics so the built
page can tell "kept in the library" from "retired" (today both would have `image_id` NULL; the C3 built view lists position and
source filename only, because `ComicsPanel` does not expose `source_image_id`). Tracker D10. Two more things become reachable
once the build exists (tracker D11): the editor must refresh when an operation fails with `built` or `not_found`, and "Move from X"
should say that X is built when that is the reason it was refused.

## Non-goals

- Building, the keep dialog, file moves, derived data for the built image (C4).
- Drag-and-drop reordering, per-panel cropping, editing a built comics, un-building, merging comics.
- Comics-level popularity, tags or search results (the built image is an ordinary meme).
- Android client screens (the API is documented and the types generated, so a client can follow later).
- A total count in the list, sorting options beyond newest first.

## Tests

- Repository: `addability` for each reason, batching, and the not-found id.
- API (mocked or integration like the neighbouring routers): each endpoint's success path; each error code and its status;
  `move: true` versus the 409; `reorder_mismatch`; a built comics refuses rename, delete, add, remove, reorder and preview;
  `GET /api/images?record=false` records no impression and no outcome while the default still records; the preview returns JPEG
  bytes, a compose failure maps to 422 with the 1-based position, and `maxWidth` bounds are enforced.
- Frontend (vitest): list search and "Show more"; the editor's add, remove, up/down (order sent to the API), disabled "+" with
  reason and link, "added" state, compose-error message; the meme-page block in its three states (in a comics, not in one, move
  offer); name errors in words.
- Docs guard: `backend_api.md` lists the new endpoints; generated types have no diff.

## Review focus (inputs most likely to bite)

- Two admins editing one comics: a reorder whose panel ids no longer match (a panel was added or removed meanwhile) must be a clean
  422, not a half-applied order.
- A very large panel (near the 16,000 px limit) or a corrupt file in the preview: a readable message, no 500, no worker left busy.
- Names with Cyrillic, emoji, trailing dots and different case: the same rules as C1 in the UI's messages.
- A search result whose image was flagged or moved into a comics between the search and the click: the 409 refreshes the state and
  does not leave a "+" that fails silently.
- The meme page for an image that was a panel of a built comics (its row is gone): `by-image` returns `null`, not an error.
