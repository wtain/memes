# C6: Finding images for a comics

Status: done
Originates from: docs/superpowers/specs/2026-11-02-multi-image-comics/README.md (tracker project C6)
Follow-ups: none yet

## Intent

After the rollout the owner found a real story but no easy way to start a comics from it: the details page only offers "Add to comics…", which asks for a name or an existing draft, and the editor's search finds memes by text only, so an image seen elsewhere (details page, duplicates, ingestion) cannot be looked up by its id. C6 removes both frictions. Frontend only; no migration, no endpoint.

## Decisions by the owner (2026-11-07)

- A **Create comics** action on the image details view creates a new draft comics holding that image.
- The editor's meme search **treats the input as an id as well** and unions that image with the text results.

## Decisions I made (my calls)

- **Where.** `MemeComicsBlock` shows a second button, "Create comics", next to "Add to comics…", under the same conditions (image in no comics and addable). It is hidden when the image is in a comics or cannot be added.
- **Name.** Not asked. The draft is named `Comics <stem>` where `<stem>` is the original file name without extension, trimmed to fit the 80-character limit with forbidden characters removed (rules in `repository/comics_name.py`: `<>:"/\|?*` and control characters are invalid); when there is no usable stem the name is `Comics <first 8 characters of the image id>`. If the name is taken (409 name conflict), retry once with ` <first 8 characters of the image id>` appended. The owner renames it in the editor.
- **After creating** the page navigates to the editor (`/admin/comics/<id>`), where the owner adds the other panels with the search.
- **Failure.** If the draft is created but adding the image fails, the new empty draft is deleted again (best effort) and the error is shown on the details page; the button stays usable.
- **Id search.** A search input that contains a UUID (anywhere, so a pasted details-page URL works; case-insensitive) also calls `getMeme(id)`; the image is put first in the result list, de-duplicated against the text results, and goes through the same addability check (so a flagged, inactive or already-used image shows its reason instead of being hidden). A failed lookup (404) adds nothing and never turns the text search into an error. An input that is only a UUID still runs the text search for it (it just finds nothing). The result paging (Show more) is unchanged.

## Goals

- From any details page, one click puts the owner in a new draft with that image.
- In the editor, pasting an id or a details URL finds that image.

## Non-goals

- No bulk or multi-select creation, no suggestion of candidate stories (S2), no change to the build, the backend or the schemas.
- No partial-id (prefix) search.

## Done when

Both behaviours work with vitest coverage (name derivation including the empty-stem, forbidden-character and too-long cases, the name-conflict retry, the cleanup on failure, id extraction from a URL, union and de-duplication, a failed lookup); `tsc -b`, `eslint src/ --max-warnings 0` and `vitest run` pass; the tracker marks C6 done.
