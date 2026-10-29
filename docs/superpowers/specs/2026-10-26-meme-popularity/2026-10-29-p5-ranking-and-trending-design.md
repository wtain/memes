# P5: Popularity in search ranking, and a Trending memes page

Status: planned
Plan: `docs/superpowers/plans/2026-10-29-p5-ranking-and-trending.md`
Originates from: `docs/superpowers/specs/2026-10-26-meme-popularity/README.md` (epic; read it first), `2026-10-28-p3-api-and-details-page-design.md` (why the shared `Meme` schema stays untouched) and `docs/adr/adr-2026-10-01-search-ranking-in-python.md` (where search scores are computed)
Follow-ups: the signals tracker in the epic README (future projects, not written yet)

## Purpose

Popularity has been recorded (P1, P2), shown (P3) and corrected (P4); nothing uses it yet. The owner searches for
memes in this app and, when a meme cannot be found, googles it and drops it into the ingestion inbox again, which
raises its popularity. P5 puts the number to work in two ways: as a small boost in search ranking, and as a
**Trending memes** page listing the most popular memes of all time. It also writes the neighbouring signals down as
future projects, so the idea of "what the owner reaches for" is tracked in one place.

## Decisions made with the owner

| # | Decision |
|---|---|
| K1 | **Popularity is a ranking signal in search output.** |
| K2 | **A separate "Trending memes" page**, distinct from the existing Trends page. **All time, no time window for now**; a month window is deferred to a follow-up (see L6). |
| K3 | **Other signals go in a tracker in the epic README** as future projects: how often a meme appears in search results, how often it is saved from the app, a trending time window, and the discovery question below. |
| K4 | **"Why did I google it instead of finding it here" is a separate discovery brainstorm.** Re-ingestion may mean "popular", "hard to find", or both; P5 does not try to tell them apart. |

## Planner's calls (not stated by the owner; open to veto)

| # | Call |
|---|---|
| L1 | **Boost, not a re-sort.** In `ImageRepository._ranked_page`, after the relevance score is known, add `weight * min(log2(max(1, popularity)), max_log2)` to it, then sort and page exactly as today (`(score, created_at, id)` descending, same keyset cursor). Log-scaled and capped, so a meme with thousands of copies cannot bury a clearly better textual match. Settings `search.ranking.popularity.weight` (default 0.03) and `search.ranking.popularity.max_log2` (default 5, so the largest boost is 0.15). Weight 0 turns it off. Validation: `weight` finite and >= 0, `max_log2` finite and > 0. They live in `environments/settings.yaml` only (the environment overlays inherit them), are loaded by a second cached loader next to `get_weights()` (for example `get_popularity_boost()`), so the frozen `RankingWeights` and the tests that construct it stay untouched, and a missing key is a startup error like the other ranking keys. **The invariant the defaults keep:** the largest boost (0.15) is smaller than the gap between a note-exact hit (1.0) and an OCR-fuzzy hit (0.45), so a very popular weaker match never beats a clearly better one; it can still reorder near-equal hits such as an OCR-exact (0.9) against a tag-exact (0.8). The boost is additive, so it matters relatively less for long multi-token queries; that is intended. The final score (base plus boost) is rounded to 6 decimals, the same rounding `score_images` uses, so the keyset cursor round-trips exactly; `RankedRow.score` holds this boosted, rounded value and the cursor encodes it unchanged. |
| L2 | **Only text queries are affected.** Queries without text keep the recency order and cursor. The popularity value comes from the same page-row query that already fetches `(id, filename, created_at, flagged)`, so it costs no extra query. Discarded images (popularity 0) are not in results, and the `max(1, ...)` makes a stored 0 harmless anyway. |
| L3 | **Trending endpoint** `GET /api/images/trending?limit=&cursor=`. Eligible images: `status = active`, not flagged (a missing `image_extras` row counts as not flagged, as in the other list queries), and stored popularity at least `search.trending.min_popularity` (a required key in `environments/settings.yaml`, default 2; a meme nobody re-dropped is not trending). An invalid or foreign cursor restarts at page 1, the same soft behaviour a stale relevance cursor has. The response carries no facets. Order `(popularity desc, created_at desc, id desc)`, keyset cursor over the same triple, `limit` bounded like the other list endpoints. Response is a new shared schema: items of `{ meme: Meme, popularity: int }` plus `nextCursor` and `hasNext` shaped like `MemeSearchResponse`. The route is declared before the `/{image_id}` catch-all. |
| L4 | **`Meme` is not changed.** The trending items carry the number beside the meme (P3 B1: a field on `Meme` would put it on every list and client). Search results keep not showing it. |
| L5 | **No migration, no new index.** One sort over the active images (tens of thousands of rows) is cheap; revisit with a partial index if the page is ever slow. |
| L6 | **The time window is later, and needs no remodelling.** Every absorption row has `created_at`, so "copies that arrived in the last 30 days" is a later query over open `absorb` edges grouped by their winner's root. Not built now. |
| L7 | **Page.** Route `/trending-memes`, nav label "Trending memes", a grid of the existing meme cards each with a small popularity badge, "Show more" using the cursor, an empty state when nothing reaches `min_popularity`. Reading failures show the usual error state used by the other list pages. |

## Behaviour

**Search.** `_ranked_page` selects `Image.popularity` with its other columns, computes
`score = base_score + boost(popularity)` per row (the cursor already carries the score, so paging stays consistent for
unchanged data; popularity changing mid-pagination has the same caveat the ADR notes for any data change), then sorts,
applies the cursor and returns `limit + 1` rows as before. `get_weights()` (or its neighbour in
`repository/search_ranking.py`) loads and validates the two new keys; `warn_match_count` logic is untouched.

**Trending.** A new service method builds the page from one query over `images` (and the flagged state), builds each
item with the same `Meme` construction the list endpoints use, and encodes/decodes the cursor with the helpers the
other cursor endpoints use. Read-only; never commits.

**Docs.** `backend_api.md` documents the endpoint and the search-ranking change (including the settings keys and the
weight-0 switch). The ADR gets a short "Consequences" line: scores now include a popularity term. The epic README gets
the signals tracker (below) and marks P5 designed. `CLAUDE.md`'s search-ranking sentence names the new keys.

## Signals tracker (goes into the epic README)

| # | Signal / project | Why it matters | State |
|---|---|---|---|
| S1 | **Search-result appearance frequency**: how often a meme is shown in results | A meme that appears often but is never saved is a ranking problem; one never appearing is invisible | idea; needs impression capture |
| S2 | **Save frequency**: how often a meme is saved with "save" in the app | The strongest "this was useful" signal the app can see | idea; needs an event on save |
| S3 | **Trending time window** (e.g. last 30 days) | Separates "was popular once" from "is being re-dropped now" | deferred from P5 (L6) |
| S4 | **Discovery: why the owner googles instead of finding the meme here**, including capturing failed searches | Re-ingestion mixes "popular" and "hard to find"; failed searches would separate them | needs its own brainstorm |
| S5 | **Evaluating the ranking boost**: does it help the owner find memes | P5 ships a conservative default without measurement | follow-up once S1/S2 exist |

## Tests

- Ranking (integration, `ocrdb_test`): with weight 0 the order is exactly today's; with the default weight, two equally
  matching memes of popularity 1 and 8 rank the popular one first; a note-exact hit of popularity 1 still outranks an
  OCR-fuzzy hit whose popularity is at or above `2 ** max_log2` (the pair the invariant in L1 protects); popularity above
  `2 ** max_log2` adds nothing more; the keyset cursor pages through a result set containing equal base scores without
  repeats or gaps; queries without text are unchanged.
- Settings: both keys present in the shipped settings for every environment, a missing key is a startup error, the
  existing ranking weight tests still pass.
- Trending (integration + endpoint): items are ordered by popularity then `created_at` then id; a flagged image, a
  non-active image, a discarded image (0) and a meme below `min_popularity` are absent; the cursor pages without
  repeats; `limit` bounds; the response carries the number; nothing is written.
- Frontend: the page lists cards with badges, loads more with the cursor, shows the empty state and the error state;
  `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`.
- Full roots, separately: `Backend/tests`, `tests/integration`, `tests/docs`.

## Goals

- Memes the owner keeps re-dropping surface a little higher in search without ever outranking a clearly better match.
- A page that answers "what are the most re-dropped memes?" in one glance.
- The other signals are written down once, where the epic lives.

## Non-goals

- No time window, no per-period counts (L6, S3).
- No popularity shown on search or other list cards; no sort-by-popularity control on existing lists.
- No impression or save capture (S1, S2) and no failed-search capture (S4).
- No change to what is matched, to facets, or to the recency order of queries without text.

## Anti-goals

- Popularity overriding relevance: the boost is capped well below one exact hit.
- A slower search for every query: no extra query is added to the ranked path.
- A shared-schema change that spreads the number to every client (`Meme` stays as it is).

## Constraints

- Needs P1 to P4 migrations applied (the endpoint reads the stored number); P5 adds none.
- Shared schema change (`TrendingMemesResponse`): all three generators; no hand edits. `backend_api.md` stays in sync.
- Frontend gate per `CLAUDE.md`. Test database only (`ocrdb_test`); the three environments' ports are occupied, and
  integration runs must not overlap with another session's.
- Repositories do not commit.

## Left to the builder

- Names of the schema, service method, settings class and React components; page layout and badge style.
- Exactly how the cursor for trending is encoded, within L3.
- Whether trending items reuse the existing `Meme` construction (`_fill_texts_and_tags`) with a parallel response
  builder; the boost applies only in `_ranked_page` (not to `/similar`, recommendations or the meme-from-text path).
- The default of `search.trending.min_popularity` may be reconsidered if 2 leaves the page nearly empty on the real library
  (the builder cannot query it; the owner decides after seeing the page).
