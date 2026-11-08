# C8: Add to a comics from result lists

Status: done
Originates from: docs/superpowers/specs/2026-11-02-multi-image-comics/tracker.md (spike S3, idea b)
Follow-ups: none yet

## Intent

Collecting panels today means opening each image's details page. When the owner scrolls a list and sees three panels of one story, each should join a comics in one click, from the list. Frontend only; no endpoint, schema or migration.

## Decisions by the owner

- Build the "add from result lists" idea (2026-11-08). Everything below is my call.

## Decisions I made (my calls)

- **Working comics.** The first "Add to comics" click on a card opens the existing picker (`ComicsPickerModal`: existing draft or new comics, which adds the image). The comics it ends in becomes the **working comics**, remembered in `localStorage` (key `comics.workingComics`, value `{id, name}`; every access in try/catch, the feature works without storage and then asks every time). From then on a click adds the image straight to it, with no dialog.
- **Where.** `MemeCard` gets an optional prop `comicsAction` (default false). Lists turn it on: `MemesList` (search, explore, untagged, no-OCR, recommendations; **not** the flagged list, flagged images cannot be added) and the similar-images strip of `MemeDetails`. Not on the duplicates review, the trending page, the concept page or the meme-from-text results.
- **Card behaviour.** A small button under the flag row: "Add to comics" (no working comics) or "Add to <name>". While adding it is disabled; afterwards the card shows "Added to <name>" (with a link to the editor) and no button. A refused add shows the error in words (`describeComicsError`, which already names the comics an image is in) and keeps the button. There is no addability pre-check, so a list page makes no extra requests.
- **Stale working comics.** If a direct add fails with `built` or `not_found`, the working comics is cleared and the message says to pick another; the next click opens the picker.
- **Working comics bar.** `MemesList` shows, above the grid and only while a working comics is set: "Adding to <name>" with "Open editor", "Change" (clears it, the next click asks again) and "Stop". Cards react to the change at once (a small external store read with `useSyncExternalStore`).
- **No new requests** beyond the add itself; the picker is the existing component, unchanged except for what the caller does with `onAdded`.

## Goals

- Add an image to a comics from a list in one click after the first.
- The owner always sees which comics receives the clicks.

## Non-goals

- No bulk selection, no addability pre-check, no new endpoint, no change to the editor or the details page block, no persistence of the card's "Added" state across a remount of the card.

## Done when

Vitest covers: the store (get/set/clear, storage failure), the card action (opens the picker without a working comics and sets it from the result, adds directly with one, shows the added state, shows an error and keeps the button, clears a stale working comics on `built`/`not_found`), the bar (Change, Stop, Open editor), and that the flagged list and other lists get or lack the action as specified; `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run` pass; tracker updated.
