# S3: Trending memes, last 30 days

Status: planned
Plan: `docs/superpowers/plans/2026-10-30-s3-trending-window.md`
Originates from: `docs/superpowers/specs/2026-10-26-meme-popularity/README.md` (epic; signals tracker row S3) and `2026-10-29-p5-ranking-and-trending-design.md` (the all-time Trending page this extends; its L6 predicted this query)
Follow-ups: none yet

## Purpose

The Trending memes page (P5) ranks by total popularity, so it shows what was popular at some point. The owner wants
the other view: memes that **picked up the most duplicates in the last month**, i.e. what is being re-dropped now.
S3 adds a switch on the page between **All time** and **Last 30 days**.

## Decisions made with the owner

| # | Decision |
|---|---|
| M1 | **"Trending in the window" means: the memes that picked up the most duplicates in the last 30 days.** Not "popular overall and also active recently". |
| M2 | **The page gets a switch between All time and Last 30 days**; the all-time list stays exactly as it is. The month view shows, per meme, the gain in the window (for example "+4 in 30 days") next to its total popularity. |

## Planner's calls (not stated by the owner; open to veto)

| # | Call |
|---|---|
| N1 | **The gain of a meme is the sum of the weights of the `absorb` edges that are open (not reversed), were created in the last 30 days, and sit in the meme's duplicate tree.** `source = 'adjustment'` edges do not count: a manual "set popularity to N" is the owner's correction, not a duplicate arriving. Reversed edges (an unflag, an undone reject, an aborted ingestion) drop out, exactly as they drop out of popularity itself. Weight-0 edges add nothing. "Created" is the edge's `created_at` against the database clock (`now() - interval '30 days'`). Edges whose loser image row has since been deleted still count, as they do in popularity (the log has no foreign key to images on purpose). |
| N2 | **The credit goes to the root of the tree today.** If A absorbed B last week and A was absorbed into C yesterday, C's gain includes both edges. Each node has at most one open outgoing edge (the `uq_popularity_open_loser` index), so the walk from an edge's winner up to its root is a single chain. This is the same roll-up popularity uses. A manual adjustment can push a meme's total below its gain (set to 1 after four new copies); the page shows both numbers as they are. |
| N3 | **Eligibility.** The root must be `status = active` and not flagged (a missing `image_extras` row counts as not flagged), like the all-time list. The month view lists only memes with a gain of at least 1; the all-time `min_popularity` rule is not applied to it (a meme with 2 new copies this month is trending even if its total is small). |
| N4 | **Order and paging.** Gain descending, then `created_at` descending, then `id` descending; keyset cursor over that triple. The gain is computed by the query, so it can move between two page requests; that is the same caveat the all-time list has for popularity and the ranking has for any data change. |
| N5 | **API.** `GET /api/images/trending` gets an optional `window` query parameter: `all` (default, today's behaviour) or `30d`. Any other value is a 422. Items of the month view carry a new optional integer `windowGain` (minimum 1); all-time items omit it. The shared `Meme` schema stays untouched (P5 L4). The cursor encodes the window as well; a cursor from the other window restarts at page 1, the soft behaviour a bad cursor already has. The window length is a constant of the repository module (30 days) named after the API value; a different length would be a new `window` value, which keeps the contract explicit. |
| N6 | **Query shape.** One statement: a recursive CTE seeded with the recent open non-adjustment edges (winner, weight) and walking upward through open absorb edges (`loser_image_id = current` gives `winner_image_id` as the parent) until a node has no open parent; group the weights by that root, join `images` and `image_extras` for the eligibility filter, then order, apply the cursor and limit. Read-only; never commits. It reuses the open-edge predicate the tree queries in `repository/popularity.py` already use, so the definition of "open" lives in one place. |
| N7 | **No migration; index only if measured.** `popularity_absorptions` has no index on `created_at`, so the seed scans the log. The log is small (it is empty on all three live databases today and grows by one row per absorbed duplicate). The plan must measure the query on a scratch database with a synthetic log of 200,000 rows; if the 95th percentile is over 300 ms, add a partial index on `created_at` for open absorb rows in the same change (a migration the owner runs), otherwise none. |
| N8 | **Page.** The existing `/trending-memes` page gets a two-option switch (All time, Last 30 days), default All time; the choice is kept in the URL query (`?window=30d`) so the view can be linked and survives a reload. Switching resets the list and the cursor. Month-view cards show a badge "+N in 30 days" plus the total ("Popularity T"). Empty state of the month view: "No memes picked up duplicates in the last 30 days." The existing error, Retry, "Show more" and StrictMode single-request behaviour apply to both views. |

## Behaviour

**Repository.** A new method on `ImageRepository` next to `get_trending`, for example `get_trending_window(cursor_gain,
cursor_created_at, cursor_id, limit)`, returns up to `limit + 1` rows of `(id, filename, created_at, flagged,
popularity, gain)`. The window constant and the recursive statement live with the other trending code; the open-edge
predicate is shared with `repository/popularity.py` rather than copied (a small SQL fragment constant is enough).

**Service and router.** `ImageService.get_trending` takes the window and picks the repository method; the cursor helpers
carry a window tag; the router validates the enum (FastAPI `Literal` or an enum query parameter, so the 422 comes for
free) and declares nothing new, the route already sits before `/{image_id}`.

**Schema and types.** `trendingmemeitem.schema.json` gains `windowGain`, optional, integer, minimum 1. Regenerate the
TypeScript, Python and Kotlin types with the generators. `backend_api.md` documents the parameter, the new field and
the window rule (including that adjustments do not count).

**Frontend.** `HttpMemesApi.getTrendingMemes` and the `MemesApi` interface take the window (default `all`);
`TrendingMemesPage` reads and writes `?window=`, reloads on a change and ignores a response from a superseded request
(the page already guards this with a request id).

**Docs.** The epic README marks S3 designed and then done, and links this spec. `CLAUDE.md` is only touched if the
trending description there names the page's scope.

## Out of scope

- Other window lengths or a free date range (a new `window` value later).
- A trending signal that mixes the window with total popularity.
- Using the windowed gain in search ranking.
- A per-day history or chart of a meme's popularity.

## Tests

- Repository (integration, `ocrdb_test` or a private scratch database): an edge inside the window counts and an edge
  31 days old does not; a reversed edge does not count; an `adjustment` edge does not count; a chain A-B-C credits the
  final root only, counting edges of both levels; the loser of a deleted image still counts; flagged and non-active
  roots are left out; a root with gain 0 is not listed; ordering by gain with `created_at`/`id` tiebreaks; the keyset
  cursor pages a result with equal gains without repeats or gaps; a bad or foreign-window cursor restarts at page 1.
- API: `window` omitted equals `all` and responses carry no `windowGain`; `window=30d` items carry it; `window=7d` is a 422;
  the route still wins over `/{image_id}`.
- Frontend: the switch changes the request window and resets the list; the URL query round-trips; the month badge and
  empty state render; a failed request keeps Retry working in both views; one first request under StrictMode.
- Performance: the 200,000-row scratch-database measurement of N7, with its result written into the plan's review notes.
- Docs guard (`tests/docs/`) and the generated-types diff stay clean.

## Left to the builder

The exact SQL, the cursor encoding, the switch's markup and the badge styling, within the calls above.
