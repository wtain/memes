# S4: Discovery report (why a meme comes back)

Status: done
Plan: `docs/superpowers/plans/2026-11-01-s4-discovery-report.md`
Originates from: `docs/superpowers/specs/2026-10-26-meme-popularity/README.md` (epic; signals tracker row S4)
Follow-ups: none yet

## Purpose

A meme that keeps coming back through ingestion is either popular, hard to find in this app, or both. The owner who
cannot find a meme here googles it and re-drops it, so today the two cases look the same. S4 separates them from data
the app already keeps: which memes came back, how often search showed them (S1), how often they were saved (S2), and
which searches found nothing. The output is a report the owner reads; nothing here changes search or ranking.

## Decisions made with the owner

| # | Decision |
|---|---|
| M1 | **Analyse existing data first; no new capture.** Capturing a miss from the clients (S6) is a later project. |
| M2 | **Form: a read-only command-line report first**, an admin page later (S7), once the owner has used the report on real data. |
| M3 | **Two views, memes first**: a memes view (what came back and how visible it is) and a queries view (searches that found nothing or little). |
| M4 | **"Came back" counts every popularity source except manual `adjustment`**, including library clean-up merges and recut transfers. A per-source breakdown is shown so the owner can tell ingestion from clean-up, and `--sources` narrows a run. |
| M5 | **Numbers plus one ratio column**, no verdict label and no thresholds: `per_100` = re-drops in the window per 100 impressions in the window. |
| M6 | **A failed search is zero or low results** (`result_count <= --max-results`, default 3). `--include-degraded` also counts meme-from-text searches whose parser failed (`degraded` or a `fallback` reason). |
| M7 | **Plain text searches by default**; meme-from-text messages are opt-in (`--kind meme_text|all`). |
| M8 | **Later, separate project (S6)**: failed-search capture in the clients. Android: a search that does not end with a save (the download action) counts as a miss, with no extra tap. Web: an explicit "didn't find it" button. It needs its own brainstorm (what is a search session on the phone). |

## Planner's calls (not stated by the owner; open to veto)

| # | Call |
|---|---|
| N1 | **Layout.** `repository/discovery_report.py` holds `memes_report(session, ...)` and `queries_report(session, ...)` and returns plain dataclass rows; they only read and never commit. `tools/discovery_report.py` is the command: `python -m tools.discovery_report {memes,queries} --env {metal,general,it} [options]`. The later page (S7) reuses the repository module. |
| N2 | **Read-only by construction.** The tool needs `DATABASE_URL_READONLY` in the shell (never the main `DATABASE_URL`), reuses `role_is_read_only`, `target_check` and `scrub` from `tools/rollout_preflight.py`, refuses a role that can write (exit 2), and opens its connection with `default_transaction_read_only=on`. It prints a warning when the URL points at another environment's port. |
| N3 | **A meme is a tree root**: an image that exists, has status `active`, is not flagged, and has at least one counted open edge. Roots whose image row is gone are not listed. |
| N4 | **Re-drops** are open edges (`kind = absorb`, `reversed_by_id IS NULL`, the shared `OPEN_EDGE_SQL` predicate) whose winner is anywhere in the root's duplicate tree, found with the same recursive walk popularity, saves and impressions use. A count is the sum of edge `weight`, so weight-0 recut transfers add nothing. Edges with no loser id (re-dropped files that never became a row) belong to their winner's tree like any other. |
| N5 | **Memes columns**: `id`, `filename`, `redrops` (all time), `redrops_window`, `sources` (breakdown as `source:n`), `popularity` (the cached `images.popularity`), `saves`, `impressions`, `impressions_window`, `last_back` (newest counted edge), `per_100`. `per_100` is `redrops_window * 100 / impressions_window`; with 0 impressions in the window it is `inf` (and sorts first), and with 0 re-drops in the window it is 0. |
| N6 | **Memes options**: `--window-days` (default `popularity.impressions.window_days`, so the report matches the meme page), `--sources a,b` (default all except `adjustment`), `--min-redrops N` (default 1, over the window), `--max-impressions N`, `--sort per_100\|redrops\|redrops_window\|popularity\|saves\|impressions` (default `per_100`, then `redrops_window`, then `redrops`, all descending), `--limit N` (default 50), `--csv PATH`. |
| N7 | **Before S1 is migrated** the report still runs: the existence of `meme_impressions` is checked with `to_regclass`, and the impression columns and `per_100` show `n/a`; the default sort then falls back to `redrops_window`, then `redrops`. |
| N8 | **Queries source and grouping.** `search_history` only. One row per distinct normalised query (lower-cased, whitespace collapsed) plus its facet pairs from `search_history_tags` (sorted `category:value`); a facet-only search shows `(no text)` and its tags. |
| N9 | **Queries columns**: `query`, `tags`, `kind`, `times`, `first_at`, `last_at`, `min_results`, `avg_results`, `web`, `android`, `degraded` (count and fallback reasons). |
| N10 | **Queries options**: `--kind search\|meme_text\|all` (default `search`), `--max-results N` (default 3; 0 gives zero-results only), `--include-degraded`, `--since-days N` (default all time), `--min-times N` (default 1), `--sort times\|last\|min_results` (default `times`, then `last_at`), `--limit N` (default 50), `--csv PATH`. |
| N11 | **Tail-page rule.** Every page of a search, "show more" pages included, writes its own `search_history` row, and the row does not record whether it came from a cursor, so the last page of a search that worked (for example 3 of 23 results) would look like a failure. A zero-result row is always reliable, because a cursor page is never empty. A row with `0 < result_count <= --max-results` is therefore ignored when the same normalised query and the same client logged a row with a higher `result_count` in the 30 minutes before it. The rule is approximate and the report says so. An exact fix would be a `has_cursor` column on `search_history`; it is out of scope and revisited only if the rule proves too noisy. |
| N12 | **Privacy.** Query text is printed to the owner's terminal only; nothing is logged; no file is written without `--csv`, and `--csv` prints one reminder line that the file contains search text. Meme-from-text rows are opt-in and already vanish when the owner uses "Forget text". |
| N13 | **Output.** An aligned plain-text table on stdout with a header line stating the environment, the view, the window or filters and the row count; `--csv PATH` also writes the same rows as CSV (UTF-8, header row). Exit 0 on success, 2 for a refused role or a usage error. |
| N14 | **No migration, no schema, no API, no client change.** The deliverable works after the merge, with the read-only URL. |

## Behaviour

**Memes view.** One recursive statement seeded with every image that is the winner of a counted open edge and is not
itself the loser of an open edge (a root), carrying the root id through the walk. Edge counts, saves and impressions
are joined per root over the whole tree. Filtering, sorting and limiting run in SQL, and the ratio is computed in the
statement so sorting by it is exact.

**Queries view.** One statement over `search_history` (with a window function for the tail-page rule and an aggregate
of `search_history_tags`) grouped by normalised query and tag pairs.

**Docs.** A short `docs/discovery-report.md` explains each column and states three caveats: impressions start when S1
is migrated, re-drops before the popularity log existed are invisible, and the tail-page rule is approximate. One line
in `CLAUDE.md` names the tool next to the preflight tool. The epic README gets the S4 row updated and two new tracker
rows: S6 (client failed-search capture, M8) and S7 (the admin page for this report).

## Rollout

Nothing to migrate or deploy. After the merge the owner runs the tool per environment with `DATABASE_URL_READONLY`.
Until the S1 migration (`a2f6d8c3b715`) has run on an environment its impression columns are `n/a`, and they fill in as
searches are made afterwards.

## Testing

Tests are written first and watched failing, on the real test database in rolled-back transactions.

- Memes: a copy folded into a winner counts for the winner and a chain for the final root; `adjustment` is excluded by
  default and `--sources` narrows; weight-0 recut edges add nothing; reversed edges do not count; an edge just inside
  and just outside the window; flagged, non-active and deleted roots are not listed; `per_100` is `inf` at 0
  impressions and 0 at 0 re-drops in the window; every sort option; `--min-redrops`, `--max-impressions`, `--limit`;
  the impressions table missing gives `n/a` and the fallback sort.
- Queries: zero versus low results; the tail-page rule at 29 and at 31 minutes, for a different client, for a higher
  earlier count and for an unrelated query; normalised grouping; facet pairs and `(no text)`; `--kind`;
  `--include-degraded`; `--since-days`; `--min-times`; the sorts.
- Command: options parse; `--env` is required; a role that can write is refused with exit 2; `--csv` writes the
  header and rows and prints the reminder; one run through a real read-only role against the test database.
- Whole roots run: `tests/integration`, `tests/docs`, `Backend/tests` (nothing under `Backend` changes) and
  `tests/rules`.

## Out of scope

- Any client or API change, and S6 (failed-search capture) and S7 (the admin page).
- Linking a failed query to a meme automatically.
- Using any of these numbers in ranking (S5).
- A `has_cursor` column on `search_history` (N11).
- Backfilling re-drops that happened before the popularity log.
