# S2: Save count on the meme details page

Status: planned
Plan: `docs/superpowers/plans/2026-10-30-s2-save-count.md`
Originates from: `docs/superpowers/specs/2026-10-26-meme-popularity/README.md` (epic; signals tracker row S2)
Follow-ups: none yet

## Purpose

The owner saves a meme from the Android app almost always because they are about to use it. A save is therefore the
strongest "this meme was useful" signal the app can see. Today the save only writes a file to the phone
(`Pictures/MemesBrowser-<ENV>`) and the server never hears about it. S2 makes the server count saves and shows the
count on the web meme details page. Ranking, lists and trending by saves come later; this phase only collects the
signal and shows it.

## Decisions made with the owner

| # | Decision |
|---|---|
| M1 | **Every save counts, repeat saves included.** The owner sometimes cannot find the copy they already saved and saves it again; that is a second use of the meme, so it is a second count. Nothing is de-duplicated. |
| M2 | **First use: show the save count on the meme details page.** Nothing else reads the number yet. |
| M3 | **Saves of discarded duplicates count for the surviving meme.** If a saved image is later folded into another (it becomes a duplicate loser in the popularity log), its saves belong to the survivor, found the same way popularity and S3 find a tree. Un-folding (an unflag, an undone reject) hands the saves back, because only open edges count. |

## Context the owner gave

- A save happens only when the app can reach the server (it downloads the file from there), so the app can report it
  at that moment.
- How often the saved copy is used afterwards cannot be seen until Android has a share integration into other apps.
  That is a separate, larger project and S2 does not depend on it.
- Searches that fail (no text, handwriting, odd fonts, Google instead) are the S4 question. S2 only sees successes.

## Planner's calls (not stated by the owner; open to veto)

| # | Call |
|---|---|
| N1 | **Separate number from popularity.** Popularity stays "duplicates folded into this meme". Saves are "how often the owner needed it". They are never added together and the popularity log is not touched. |
| N2 | **Storage: one append-only table `meme_saves`** with `id` (uuid, primary key), `image_id` (uuid, not null), `created_at` (timestamptz, not null, default `now()`), and an index on `(image_id)`. Like `popularity_absorptions`, `image_id` has **no foreign key** to `images`, so a save outlives the deletion of its image and the history is intact. A migration (the owner runs it on live; never an agent). Rows are never updated or deleted by the application. The timestamp is what a later "saves in the last 30 days" would use (out of scope here). |
| N3 | **Recording endpoint:** `POST /api/images/meme/{image_id}/saves`, no body, answers `204`; `404` when the image does not exist; any status of existing image is accepted (the owner saved what they were shown). It inserts one row. No de-duplication, no idempotency key: a retry would double count, so the app does not retry. Declared with the other `/meme/{image_id}/...` routes. |
| N4 | **Read endpoint:** `GET /api/images/meme/{image_id}/saves` returns `{ "imageId", "count", "lastSavedAt" }` (`lastSavedAt` null when the count is 0). The count is the number of `meme_saves` rows whose `image_id` is the requested image **or any image in its duplicate tree** (the requested image plus every loser reachable through open `absorb` edges, the same tree `popularity.py` walks). For an image that is itself discarded the answer is the count of its own subtree, as popularity does for it; the page does not hide this. `404` for an unknown image. One recursive statement, read only, never commits; it reuses the open-edge predicate `OPEN_EDGE_SQL` from `repository/popularity.py`. |
| N5 | **Recording is best effort in the app.** After the file is written to the gallery the app sends the POST in a try/catch; any failure (server unreachable, 404, 5xx) is logged and ignored. A save never fails, changes its snackbar or delays the success state because of the report. The cost of a lost report is one missing count. |
| N6 | **Android scope:** only the capture. Android does not display the count (it does not display popularity either). The `MemeDetailViewModel` constructor does not change; a `recordSave(id)` call is added to the existing repository and API interface. |
| N7 | **Web display:** a small `SaveCountBlock` next to the popularity block in `MemeDetails`: "Saves" and the number, and "last saved <date>" when there is one. Like `PopularityBlock` it renders nothing while loading and on any read error, so a slow or failing read never hides the meme. It is keyed by image id. The count starts at 0 for every meme at rollout: saves made before the feature cannot be recovered. |
| N8 | **No manual adjustment.** Unlike popularity there is no control to set or delete a save count. |
| N9 | **Schema:** `shared/schemas/memesaves.schema.json` for the read response; the 204 POST needs none. Regenerate the TypeScript, Python and Kotlin types with the generators. |

## Behaviour

**Repository.** A small `repository/meme_saves.py` (or methods on `PopularityRepository`'s neighbour; the builder
chooses by where the tree statement reads best) with `record(image_id)` and `summary(image_id)` returning `(count,
last_saved_at)`. It checks the image exists in the endpoint's service layer, not in SQL.

**Service and router.** Both endpoints sit in `Backend/app/api/images.py` next to the popularity routes, before `/{image_id}`.
A service function maps "no such image" to 404.

**Docs.** `backend_api.md` (both endpoints and the best-effort rule), `docs/image-derived-data.md` (a row for
`meme_saves`: no foreign key, not copied by a recut), the epic README (S2 row designed, then done). `CLAUDE.md` is
touched only if it needs a line on the new table.

## Out of scope

- Showing the count in lists, sorting by it, a "most saved" page, or using it in search ranking.
- A window ("saves in the last 30 days") and a per-day history.
- Android display of popularity or saves.
- Detecting that a meme is already saved on the phone (the repeated-save duplicates problem), and any share integration.
- Counting downloads or views from the web, which has no save action.
- S1 (impressions), S4 and S5.

## Tests

- Repository (integration, `ocrdb_test` or a private scratch database): `record` inserts one row each call (two calls,
  count 2); `summary` of a meme with no saves is `(0, None)`; saves of a loser count for its winner and transitively for the
  final root; a reversed edge hands the saves back; a deleted image's saves still count; `lastSavedAt` is the newest.
- API: POST answers 204 and a following GET shows the increment; POST and GET on an unknown id answer 404; the routes
  resolve before `/{image_id}`.
- Frontend: `SaveCountBlock` renders the count, "last saved" only when present, nothing on error or while loading;
  `MemeDetails` shows it.
- Android unit test: a failing `recordSave` leaves the save successful; a successful one is called once with the meme id,
  after the gallery write.
- Docs guards (`tests/docs/`): the new table is classified in `docs/image-derived-data.md`.
