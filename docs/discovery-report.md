# Discovery report

A read-only command that helps tell a meme that is **popular** from one that is **hard to find**. A meme that keeps
coming back through ingestion is either; the report puts each such meme next to how often search showed it and how
often it was saved, and lists the searches that found nothing or little.

Design: `docs/superpowers/specs/2026-10-26-meme-popularity/2026-11-01-s4-discovery-report-design.md`.

## Running it

```powershell
# the read-only role's URL for one environment; never the main DATABASE_URL
$env:DATABASE_URL_READONLY = "postgresql+asyncpg://ocr_readonly:...@localhost:5434/ocrdb"
python -m tools.discovery_report memes   --env general
python -m tools.discovery_report queries --env general
python -m tools.discovery_report misses  --env general
```

`--env` only names the environment in the header and warns when the URL's port belongs to another environment
(metal 5432, general 5434, it 5436). The command refuses a role that can write, opens a read-only connection, never
prints the URL and writes no file unless you pass `--csv PATH`.

## Memes view

One row per meme (the image that survives in a duplicate tree) with at least one counted re-drop.

| Column | Meaning |
|---|---|
| `redrops` | Copies that came back, all time: the sum of the open popularity edges in the meme's duplicate tree whose source is counted. By default every source counts except manual `adjustment`; `--sources a,b` narrows it. |
| `redrops_window` | The same, for edges created in the last `--window-days` (default `popularity.impressions.window_days`, 30). |
| `sources` | Where the re-drops came from, `source:n`. `hash_cross_corpus`, `ingest_tier_a` and `ingest_tier_b` mean you dropped it again; `cluster_review`, `agent_review`, `file_duplicates` and `recut` are library clean-up. |
| `popularity` | The cached `images.popularity`. |
| `saves` | Saves of the meme and of the copies folded into it (S2). |
| `impressions`, `impressions_window` | Times search returned it (S1), all time and in the window, over the same tree. |
| `last_back` | When the newest counted copy came back. |
| `per_100` | `redrops_window * 100 / impressions_window`. **High means it keeps coming back while search rarely shows it.** `inf` means it came back and search never showed it in the window; 0 means no re-drops in the window. |

Options: `--min-redrops N` (re-drops in the window, default 1), `--max-impressions N` (impressions in the window), `--sort per_100|redrops|redrops_window|popularity|saves|impressions`
(default `per_100`), `--limit N` (default 50), `--csv PATH`.

## Queries view

Searches that found nothing or little, from the search history, one row per distinct query (lower-cased, whitespace
collapsed) and facet filter. A search with only facets shows `(no text)` and its tags.

Columns: `times`, `first_at`, `last_at`, `min_results`, `avg_results`, the `web` and `android` counts, and `degraded`
with the `fallbacks` reasons.

Options: `--kind search|meme_text|all` (default `search`: typed searches; pasted meme-from-text messages are opt-in),
`--max-results N` (default 3; 0 means no results at all), `--include-degraded` (also counts meme-from-text searches whose
parser failed), `--since-days N`, `--min-times N`, `--sort times|last|min_results`, `--limit N`, `--csv PATH`.

`--include-degraded` only matters with `--kind meme_text` or `--kind all`: typed searches never record a parser failure.
Output is always UTF-8, so redirecting it to a file keeps non-Latin queries intact.

The query text is printed to your terminal and nowhere else. A `--csv` file contains it too: keep it out of the
repository.

## Misses view

Search sessions that ended without a save (epic meme-popularity, S6): the direct signal that a meme was hard to find.
`python -m tools.discovery_report misses --env general`.

How it decides. The search history is cut into sessions: rows of one client (and kind) belong to one session until a
pause longer than `--gap-minutes` (default 5). A session is *resolved* if any of its rows is the search a save came from
(Android sends the `searchId` of the search list with the save), otherwise it is a *miss*. A miss is *explicit* if the
web "I didn't find it" button was pressed for any of its rows; a save wins over a press. Rows with no query text and no
filters (the browse feed) are not searches and are skipped, and a session whose last row is newer than the gap is left
out because it may still be going. Misses are grouped by the session's last query and filters (lower-cased, whitespace
collapsed); a search with only filters shows `(no text)` and its tags.

Columns: `times` (sessions), `explicit` (of them pressed on the web), `searches` (average searches per session; a high
number means you kept refining), `first_at`, `last_at`, and the `web` and `android` counts.

Options: `--gap-minutes N`, `--kind search|meme_text|all` (default `search`), `--since-days N`, `--min-times N`,
`--explicit-only`, `--sort times|explicit|last`, `--limit N`, `--csv PATH`. The header states the date the outcome data
begins. Search text is printed to your terminal only; a `--csv` file contains it too.

## What the numbers cannot tell you

- **The misses view only sees sessions after the S6 migration (`b4c9e1f7a028`) has run and the first linked save or
  press was recorded**; earlier sessions would all look like misses, so they are ignored. Until then the view says so.
  Only the Search list of the Android app sends the link: a save from Explore, a deep link or a similar-meme hop counts
  as a save but resolves no session. Sessions are a time-gap approximation: two hunts within the gap merge, and one long
  hunt with a long pause splits.

- **Impressions start when the S1 migration (`a2f6d8c3b715`) has run** on that environment. Before that the impression
  columns and `per_100` show `n/a`, and the memes view sorts by `redrops_window`. The picture sharpens over the weeks
  after the migration.
- **Re-drops before the popularity log existed are invisible.** A copy that was dropped and discarded earlier left no
  edge.
- **The "low results" part of the queries view is approximate.** Every page of a search, "show more" pages included,
  writes its own history row and the row does not record whether it came from a cursor, so the last page of a search that
  worked (for example 3 of 23 results) would look like a failure. The report skips a low-result row when the same
  query and client logged a higher count in the previous 30 minutes. Zero-result searches are always reliable.
- A report is a hint, not a verdict: a meme can come back often because it is popular and still be hard to find.
  Look at `per_100` together with the source breakdown, and at the matching failed queries.
