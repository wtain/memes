# S6: Failed-search capture in the clients

Status: planned
Plan: `docs/superpowers/plans/2026-11-01-s6-failed-search-capture.md`
Originates from: `docs/superpowers/specs/2026-10-26-meme-popularity/README.md` (epic; signals tracker row S6) and `2026-11-01-s4-discovery-report-design.md` (decision M8)
Follow-ups: none yet

## Purpose

A meme that keeps coming back through ingestion is either popular, hard to find in this app, or both. S4 infers "hard to
find" from existing data (zero or low results). S6 captures it directly: a search that ended without a save is a miss.
On Android this costs no extra tap; on the web an explicit "didn't find it" button records it. The signal is read
through a third view of the S4 discovery report. Nothing here changes search or ranking.

## Decisions made with the owner

| # | Decision |
|---|---|
| Q1 | **Outcome by absence.** The server puts a `searchId` in each search response. A save sends the `searchId` of the search that led to it. A miss is a search session with no linked save, decided by the server after the fact. The phone sends no "gave up" event. |
| Q2 | **Session rule: a time gap, computed at report time.** Rows of one client belong to one session until a pause longer than N minutes (default 5, a report option). No new column and no request parameter. |
| Q3 | **Storage: a link on the save row plus one small table.** `meme_saves` gets a nullable `search_history_id`; the web press is an append-only `search_not_found` table. |
| Q4 | **The phone sends the latest `searchId` its list holds** (not a per-item id). Any row of a session counts, so the exact page does not matter. |
| Q5 | **Web: one button under the result list on `/` and on `/reply`**, shown whenever a `searchId` exists, including when results exist but are the wrong ones. |
| Q6 | **Report: a third view, `misses`**, next to `memes` and `queries`. `queries` is unchanged. |

## Planner's calls (not stated by the owner; open to veto)

| # | Call |
|---|---|
| P1 | **Switch.** `popularity.search_outcomes.enabled` mirrors S1's impressions switch. When off, both writes are skipped and the endpoints still answer normally. |
| P2 | **A save wins over a press.** A session with a linked save is resolved even if it also has a not-found press. |
| P3 | **Cut-off.** Sessions before the first linked save or press (the migration date, in practice) have no outcome data and would all look like misses, so `misses` ignores rows older than that moment and prints the date in its header. |
| P4 | **Idempotency.** `search_not_found.search_history_id` is unique and the insert uses `ON CONFLICT DO NOTHING`. The endpoint answers 204 for an unknown or pruned id, because no foreign key exists and the client is best-effort. |
| P5 | **Android scope.** Only the Search list passes the id. Explore, deep links and the Reply tab pass nothing and keep working. |

## Data and API

- **Migration (the owner runs it).**
  - `meme_saves.search_history_id`: nullable, the same type as `search_history.id`, no foreign key.
  - `search_not_found`: `id`, `search_history_id` (unique), `created_at`; no foreign keys; classified in
    `docs/image-derived-data.md` as having no tie to images.
  - The ORM models live in `Storage/models.py`; the repository code is in `repository/`; repositories never commit.
- **`searchId` on search responses.** `GET /api/images` when `q` or facets are present, and
  `POST /api/meme-from-text/search`, return a nullable `searchId`: the id `record_history` already returns. It is null
  when query logging is off, when the history write failed, and on the unfiltered browse feed. Shared schema change
  with regenerated TypeScript, Python and Kotlin types.
- **`POST /api/images/meme/{id}/saves`** takes an optional `searchId` and stores it. A client that omits it behaves
  exactly as today (S2).
- **`POST /api/search-history/{searchId}/not-found`** writes the press (P4) and returns 204. Best-effort, same savepoint
  pattern as the S1 and S2 recorders.
- `backend_api.md` documents all three changes.

## Clients

**Android.**
- The search response model gets `searchId`; `SearchViewModel` keeps the latest one in `SearchUiState`. A new search or
  a facet toggle replaces it when its response arrives; "show more" replaces it with the new page's id; clearing the
  query or switching environment clears it.
- Opening a meme from the Search list passes it to the detail screen as an optional navigation argument.
  `MemeDetailViewModel.saveToGallery` then calls `repo.recordSave(meme.id, searchId)`, still best-effort and
  fire-and-forget. A null id sends no field.
- No new UI, no extra tap, no session events.

**Web.**
- The `search.ts` and meme-from-text client types gain `searchId`; `SearchPage` and `useMessageSearch` keep the latest
  one next to the list with the same reset rules.
- A new `NotFoundButton` component sits under the result list on `/` and `/reply`, shown only when a `searchId` exists.
  On press it calls the new endpoint and shows "Noted" for that search; a new search resets it. On failure it shows
  "Couldn't send, try again" and keeps the button. It never alters the list. The "noted" state lives in a handler, not
  an effect (StrictMode and the React Compiler lint rules).

## The `misses` view

`python -m tools.discovery_report misses --env {metal,general,it}`, same read-only role rules, UTF-8 output and
terminal-only query text as the other views.

- **Sessions.** `search_history` rows ordered per client; a new session starts after a gap longer than `--gap-minutes`.
  Plain text searches by default (`--kind search|meme_text|all`).
- **Outcome.** Resolved if any row of the session is the `search_history_id` of a `meme_saves` row; otherwise a miss.
  Explicit if any row has a `search_not_found` row (P2 applies).
- **Rows.** Misses grouped by the session's last query plus facet pairs, normalised as in the `queries` view.
- **Columns:** `query`, `tags`, `times`, `explicit`, `searches` (average rows per session), `first_at`, `last_at`,
  `web`, `android`.
- **Options:** `--gap-minutes` (default 5), `--since-days`, `--min-times` (default 1), `--explicit-only`, `--kind`,
  `--sort times|explicit|last` (default `times`, then `last_at`), `--limit` (default 50), `--csv`.
- **Missing schema.** If the new column or table does not exist yet, the view prints one line saying so and exits 0.
- `docs/discovery-report.md` documents the view and its caveats: sessions are a time-gap approximation, and misses
  before the cut-off are invisible.

## Testing

Tests are written first and watched failing, on the real test database in rolled-back transactions.

- **Server.** The save stores `searchId` and works without it; the press is idempotent and answers 204 for an unknown
  id; the switch off writes nothing; `searchId` is null on the unfiltered feed and when logging is off.
- **Report.** Gap boundaries at 4:59 and 5:01; per-client separation; a save in any row resolves the session; a press
  plus a save counts as resolved; the cut-off rule; missing schema; every sort and option; the CLI contract.
- **Web.** The button shows only with a `searchId`, resets on a new search, shows the failure state.
- **Android.** The view model keeps and clears the id; the argument reaches `recordSave`.
- **Whole roots.** `tests/integration`, `Backend/tests`, `tests/docs`, `tests/rules`, frontend `tsc`/`eslint`/`vitest`,
  Android `:app:testDebugUnitTest`.

## Docs

`backend_api.md`, `docs/image-derived-data.md`, `docs/discovery-report.md`, one line in `CLAUDE.md`, and the epic README
(S6 row, `Follow-ups:`).

## Rollout

The migration is run by the owner per environment. Until it has run, the new writes fail softly (best-effort) and old
apps and old rows are unaffected; `misses` reports that the schema is missing.

## Amendments found while planning (code facts the design had wrong or left open)

| # | Amendment |
|---|---|
| A1 | **Browse rows exist.** The unfiltered Explore/home feed does write a `search_history` row (empty query, no tags). `misses` therefore skips rows with no query text and no tags before it forms sessions (the earlier statement that such rows do not exist was wrong). |
| A2 | **Open sessions are not misses.** A session whose last row is newer than `--gap-minutes` may still be going, so `misses` leaves it out. |
| A3 | **`searchId` is also null when the switch is off**, so a client never shows the button or sends a link that the server will not store. |
| A4 | **Routes and shapes.** The save link is a query parameter, `POST /api/images/meme/{id}/saves?searchId=...` (the Android call has no body today); a malformed `searchId` is ignored, not a 422. The press lives on the existing history router: `POST /api/history/{searchId}/not-found`. If the link cannot be stored (for example the migration has not run) the save is still recorded without it. |
| A5 | **Reply pages.** Only a fresh message writes a `search_history` row, so a `/reply` response to a refine or "show more" has no `searchId`; the web keeps the id of the list's fresh search for those. |
| A6 | **Web sessions are misses only when pressed** (found in the final review). The web has no save action, so an unpressed web session can never be resolved and would drown the Android signal. `misses` therefore counts an unsaved session as a miss when its client is not `web`, and a web session only when the button was pressed. |

## Out of scope

- Any use in ranking (S5) and the admin page (S7).
- An Android "didn't find it" button, session events from the phone, and an exact session id.
- Linking a missed session to the meme the owner eventually found or re-dropped.
