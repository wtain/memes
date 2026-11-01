# S1: Search impressions on the meme details page

Status: draft
Originates from: `docs/superpowers/specs/2026-10-26-meme-popularity/README.md` (epic; signals tracker row S1)
Follow-ups: none yet

## Purpose

A meme that is shown in search results again and again but never saved is a ranking problem; one that is never shown
is invisible. Today the server cannot tell either apart: `search_history` records each query and its result count, but
not which images came back. S1 makes the server record every image it returns from a search, and shows the count on
the web meme details page next to the save count (S2). Ranking, lists and trending by impressions come later (S5);
this phase collects the signal and shows it.

## Decisions made with the owner

| # | Decision |
|---|---|
| M1 | **What counts: server-side first.** An impression is an image the server returned in a search result page. A later client "seen on screen" report (web, Android) may refine it; it is not built now. |
| M2 | **Which listings count: search only.** `GET /api/images` (search), `GET /api/images/{image_id}/similar` and `POST /api/meme-from-text/search`. Every page counts, "show more" pages included. Trending, browse feeds and the admin and curation lists (flagged, untagged, no-ocr, duplicates, recut, ingestion review) do not count. Trending can be added later with one call per endpoint. |
| M3 | **Detail kept: one row per shown image** in an append-only table: image, time, rank, surface, and the search it came from. Counts, windows and duplicate trees are computed when read, as for saves and popularity. |
| M4 | **Link to the search: a nullable `search_history_id` with no foreign key.** Rows survive "Forget text" and any history clean-up; searches that are not logged leave it null and still count. |
| M5 | **Written inline, best effort, switchable.** Same request, same session, inside a savepoint; a failure never fails the search. A setting `popularity.impressions.enabled` turns it off. |
| M6 | **S1 also shows the numbers**: the meme details page shows the count over all time and over a window. The window length is a setting. |
| M7 | **Window and all-time both returned** (`count` and `countWindow`). The window is the setting `popularity.impressions.window_days`, default 30. |
| M8 | **Keep every row.** No pruning or roll-up. The size estimate is in the Behaviour section; a roll-up can be a later follow-up because the rows stay the source of truth. |
| M9 | **No hook for the client report now.** No `seen_at` column and no batch id in responses. The later report adds them with its own migration and contract. |

## Planner's calls (not stated by the owner; open to veto)

| # | Call |
|---|---|
| N1 | **Separate number from popularity and saves.** Popularity is "duplicates folded into this meme", saves are "how often the owner needed it", impressions are "how often the app offered it". They are never added together and the popularity log is not touched. |
| N2 | **Storage: table `meme_impressions`** with `id` (bigserial primary key), `image_id` (uuid, not null), `shown_at` (timestamptz, not null, default `now()`), `rank` (smallint, not null, 1-based), `surface` (text, not null; `search`, `similar` or `reply`, checked by a CHECK constraint) and `search_history_id` (the type of `search_history.id`, nullable). `image_id` and `search_history_id` have **no foreign key**, like `popularity_absorptions` and `meme_saves`. Indexes: `(image_id, shown_at)` and `(search_history_id)`. One migration after head `d5b8e3a07c41`; the owner runs it on live, never an agent. Rows are never updated or deleted by the application. |
| N3 | **Rank** is the 1-based position in the full ordering when the paging cursor makes that known, otherwise the position within the page. The planner checks each of the three surfaces and records which one applies; the column means "position the owner saw", and the difference only matters to a later rank analysis. |
| N4 | **Recording point.** A small `repository/meme_impressions.py`, shaped like the saves repository: `record_many(rows)` is one bulk `INSERT` (never commits) and `summary(image_id, window_days)` returns `(count, count_window, last_shown_at)`. Each of the three services calls one helper after it has built the page. An empty page writes nothing. `similar` does not count its source image, only the images returned. |
| N5 | **Failure isolation.** The helper runs in `session.begin_nested()`; on any exception the savepoint rolls back, one log line is written with the surface and the exception type (never the query text, never the message of a meme-from-text search), and the search response is unchanged. The `search_history` write and the rest of the request still commit. |
| N6 | **`search_history_id` availability.** The planner checks, for each surface, whether the `search_history` row id exists when the impressions are written. Where it does not (no log row, logging disabled by `search.meme_from_text.query_log.enabled`, the best-effort log failed), the column is null. Impressions are never skipped because the log is. |
| N7 | **Read endpoint:** `GET /api/images/meme/{image_id}/impressions` returns `{ "imageId", "count", "countWindow", "windowDays", "lastShownAt" }`. `count` is the number of `meme_impressions` rows whose `image_id` is the requested image **or any image in its duplicate tree** (the same tree the saves count uses, walked with the open-edge predicate `OPEN_EDGE_SQL` from `repository/popularity.py`). `countWindow` is the same count restricted to `shown_at` within the last `windowDays` days. `lastShownAt` is null when `count` is 0. `404` for an unknown or malformed id, through the same existing-image check as saves. Read only, never commits, no-cache headers, declared with the other `/meme/{image_id}/...` routes before `/{image_id}`. |
| N8 | **No write endpoint.** Only the server records impressions, so a client cannot inflate them. |
| N9 | **Settings:** `popularity.impressions.enabled` (bool, default true) and `popularity.impressions.window_days` (positive integer, default 30), both required in `environments/settings.yaml`; a missing key or a non-positive window is a startup error, like the other required keys. |
| N10 | **Schema:** `shared/schemas/memeimpressions.schema.json` added to `all.schema.json`; regenerate the TypeScript, Python and Kotlin types and confirm each tree changed. |
| N11 | **Web display:** an `ImpressionBlock` next to the save count in `MemeDetails`: "Shown N times (M in the last D days)". Like `SaveCountBlock` it renders nothing while loading or on any read error. It is keyed by image id. The counts start at 0 for every meme at rollout. |
| N12 | **Android scope: none.** Android displays neither popularity nor the save count, so it does not display impressions. Search on Android is recorded by the server like any other search. The Android client and its DTO generation are touched only if the schema regeneration requires a generated-file update. |

## Behaviour

**Repository.** `repository/meme_impressions.py` with `record_many` and `summary` as in N4. The duplicate-tree statement
is the one the saves repository uses with `meme_impressions` in place of `meme_saves`; the builder chooses whether to
share it. The existing-image check lives in the service, not in SQL.

**Services and routers.** The three search services call the recording helper once per page. The read endpoint is in
`Backend/app/api/images.py` with a service function mapping "no such image" to 404.

**Size.** A page writes up to about 50 rows. At 1,000 pages a day that is about 18 million rows a year, roughly 1 to 2
GB with both indexes. The docs state the estimate. The statistics page does not read the table.

**Docs.** `backend_api.md` (the new endpoint, and a note on each of the three search endpoints that it records
impressions), `docs/image-derived-data.md` (a row for `meme_impressions`: no foreign key, not copied by a recut),
the epic README (S1 row designed, then done, and
a `Follow-ups:` link). `CLAUDE.md` gets one line only if a reader of the search section would otherwise miss that
search writes to the database.

## Rollout

One migration. Until it has run on an environment a search there still works (the savepoint swallows the failed
write and logs it) and records nothing; the impressions read endpoint returns an error until then. Migrate first, then
restart the backend. The always-on backends may already run merged code before migration (the open question from the
recut rollout), and the savepoint makes that harmless for search.

## Testing

Tests are written first and watched failing.

- Repository, on the real test database: bulk insert and counts; a row just inside and just outside the window; a copy
  folded into a winner counts on the winner and counts again for the copy once unfolded; a deleted image's rows survive;
  an empty list is a no-op.
- Services, for the three surfaces: rows written per page; a "show more" page adds rows; `similar` skips the source
  image; rank is recorded as decided in N3; a forced insert failure still returns the normal response and leaves the
  `search_history` row committed; the switch off writes nothing; the log disabled still records impressions with a null
  `search_history_id`; the failure log line contains no query text.
- Endpoint: response shape, 404 for an unknown and for a malformed id, no-cache headers.
- Settings: a missing key and a zero or negative window are startup errors.
- `tests/docs/`: the new table is classified in `docs/image-derived-data.md`.
- Frontend: the block renders the counts and the window text, and renders nothing on a failed read.
- Whole roots run: `Backend/tests`, all of `tests/integration`, `tests/docs`, `batch/tests`, `tests/rules`, the
  frontend `tsc -b`, `eslint src/` (0 warnings) and `vitest run`, and the Android unit tests if generated files change.

## Out of scope

- The client "seen on screen" report, a `seen_at` column and a batch id in responses (M9).
- Trending, browse and admin listings (M2).
- Retention, pruning and roll-up (M8).
- Any use of impressions in ranking or in lists, and the "shown but never saved" view: S5.
- Failed and abandoned searches: S4.
- An Android display (N12).
