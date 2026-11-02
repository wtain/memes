# S7: Admin page for the discovery report

Status: done
Plan: `docs/superpowers/plans/2026-11-02-s7-discovery-admin-page.md`
Originates from: `docs/superpowers/specs/2026-10-26-meme-popularity/README.md` (epic; signals tracker row S7)
Follow-ups: none yet

## Purpose

The discovery report (S4 memes and queries views, S6 misses view) is a command-line tool that prints text tables. S7 puts
the same three views on an admin page, with thumbnails and links, so the owner can browse what keeps coming back and
what could not be found, and jump from a missed query to the search page. It reads through the report functions that
already exist in `repository/discovery_report.py`, so the page and the CLI always agree. Nothing here changes search or
ranking.

## Decisions made with the owner

| # | Decision |
|---|---|
| Q1 | **All three views as tabs, plus a "Search this" link.** Memes (thumbnails linking to the meme page), Queries and Misses (tables). Every query and miss row of kind `search` links to the Search page with that query and its facets filled in, so the owner sees what the app returns now. The CLI options become filters. Read-only, no actions. |
| Q2 | **Top N with a size choice (50, 100 or 200).** No paging. To go deeper the owner changes the sort or tightens a filter. The report functions stay exactly as they are. |
| Q3 | **No extra gate for query text, and the routes are recorded.** The queries and misses endpoints are open like the rest of the admin API (`GET /api/history` already serves the same text the same way). The three routes are added to `docs/security/admin-permissions-todo.md`. Nothing logs the text server-side. |

## Planner's calls (not stated by the owner; open to veto)

| # | Call |
|---|---|
| N1 | **Layers.** A new router `Backend/app/api/admin_discovery.py` (prefix `/admin/discovery`), a thin service `Backend/app/services/discovery_report_service.py` that maps the report dataclasses to response models, and the unchanged `repository/discovery_report.py`. |
| N2 | **Endpoints.** `GET /api/admin/discovery/memes`, `/queries` and `/misses`. Query parameters mirror the CLI options, with the same names (camelCase on the wire) and defaults: memes `windowDays` (default `popularity.impressions.window_days`), `sources` (comma list, default all except `adjustment`), `minRedrops`, `maxImpressions`, `sort`; queries `kind`, `maxResults`, `includeDegraded`, `sinceDays`, `minTimes`, `sort`; misses `gapMinutes`, `kind`, `sinceDays`, `minTimes`, `explicitOnly`, `sort`; all three `limit` (1 to 200, default 50). |
| N3 | **Validation.** A bad value gets a 422 with our own message (the `ValueError` text of the report functions or a parameter message). No message contains search text. |
| N4 | **Responses are shared JSON schemas** (`shared/schemas/discoverymemes.schema.json`, `discoveryqueries.schema.json`, `discoverymisses.schema.json`, registered in `all.schema.json`) with regenerated TypeScript, Python and Kotlin types. |
| N5 | **Memes response.** `windowDays`, `sources`, `impressionsAvailable`, `items`. Each item: `id`, `imageUrl` (`/api/images/{id}`), `filename`, `redrops`, `redropsWindow`, `sources` (a list of `{source, count}`), `popularity`, `saves`, `impressions` and `impressionsWindow` (null when the impressions table is missing), `lastBack`, `per100`. JSON cannot carry `inf`, so `per100` is null with `per100Infinite: true` for "no impressions in the window"; with no impression data both are null/false and `impressionsAvailable` is false. |
| N6 | **Queries and misses responses.** Items carry the report columns; `tags` is a list of `category:value` strings. The misses response also carries `status` (`ok`, `no_schema`, `no_outcomes`) and `cutoff`, so the page can say "migration not run" or "no outcome data yet". |
| N7 | **Read-only twice over.** The report functions only `SELECT`. The service also runs `SET TRANSACTION READ ONLY` as the first statement of the request's transaction, so the page cannot write even by mistake. |
| N8 | **Page.** Route `/admin/discovery` with a "Discovery" link after "Recut" in the header. The tab is in the URL (`?view=memes|queries|misses`). Filter state is local to the page and resets when the tab changes. A filter change refetches and cancels the previous request; loading, error (with retry and the server's 422 message) and empty states. |
| N9 | **"Search this" link** goes to `/search?q=<query>&<category>=<value>...` using the page's existing URL format (`utils/searchParams.ts`). Rows of kind `meme_text` get no link, because the Reply page cannot be prefilled from a URL. |
| N10 | **Notes the page shows.** An "impressions are n/a" banner when that table is missing; for misses, the `status` messages, the cut-off date, and the time-gap caveat. |
| N11 | **No caching.** Each tab load runs the report query once. The memes query walks the whole duplicate tree, so the cost is timed on the test database and revisited if it is slow on live data. |
| N12 | **No migration, no setting.** The page works against whatever the environment has. |

## Behaviour

- **Memes tab.** Thumbnail (links to `/memes/{id}`), filename, re-drops (all time and in the window), sources as small
  `source: count` text, popularity, saves, impressions (all time and in the window), last re-drop time, and `per_100`
  shown as `∞`, a number, or `n/a`.
- **Queries tab.** Query (or `(no text)` for a facet-only search), tag chips, kind, times, first and last time, min and
  average results, web and android counts, degraded count and reasons, and the "Search this" link.
- **Misses tab.** Query, tag chips, kind, sessions (`times`), `explicit` presses, average searches per session, first
  and last time, web and android counts, and the "Search this" link. The header shows the outcome-data date.
- **Layout.** Tables scroll sideways inside their container on a narrow screen; controls are at least 44 px tall.
- The page follows the existing frontend rules: effects survive a double mount (`StrictMode`) and refs are touched only
  in handlers and effects.

## Testing

Tests are written first and watched failing. Backend tests run on the real test database in rolled-back transactions.

- **Endpoints.** Rows map to the response, including `per100Infinite`, null impression fields without the impressions
  table, `tags` as `category:value` strings, and the misses `status` and `cutoff`; every parameter reaches the report
  function; defaults match the CLI; limits outside 1 to 200 and unknown sort, kind or source values give a 422 with no
  search text in the body.
- **Read-only.** A write attempted in the request's transaction is rejected by the database.
- **No text in logs.** Requests for queries and misses log nothing containing search text.
- **Router layer** with a mocked service for status codes and parameter wiring.
- **Frontend (vitest).** The filter-to-request mapping, `∞`/`n/a` formatting, the "Search this" URL (spaces, non-Latin
  text, several facets, no link for `meme_text`), the missing-schema notes, and the error state with retry.
- **Whole roots:** `tests/integration`, `Backend/tests`, `tests/docs`, `batch/tests`, `tests/rules`, and frontend `tsc`,
  `eslint --max-warnings 0` and `vitest run`. The Android suite is unaffected.

## Docs

`backend_api.md` (three endpoints), `docs/discovery-report.md` (an "Admin page" section, CLI sections point to it),
`docs/security/admin-permissions-todo.md` (three routes), one line in `CLAUDE.md`, and the epic README (S7 row,
`Follow-ups:`).

## Rollout

No migration. After the merge, restart a backend to serve the new routes. The memes tab fills in its impression columns
after the S1 migration (`a2f6d8c3b715`); the misses tab fills in after the S6 migration (`b4c9e1f7a028`) and some use of
the apps.

## Out of scope

- Paging, caching, CSV export (the CLI has it), charts, any write or action, authentication.
- A Reply-page prefill from a URL.
- Changes to the report modules or the CLI.
- Using any of these numbers in ranking (S5).
