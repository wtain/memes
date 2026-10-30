# S3 Trending memes, last 30 days Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `window=30d` view to the Trending memes endpoint and page: memes ranked by the duplicates they picked up in the last 30 days, with a switch between All time and Last 30 days.

**Architecture:** A new `ImageRepository.get_trending_window` runs one recursive-CTE statement: it seeds with the open, non-adjustment absorb edges created in the last 30 days, climbs each edge's winner to its tree root through the open edges, sums the weights per root, filters roots for eligibility and pages by `(gain desc, created_at desc, id desc)`. `ImageService.get_trending` and the route take an optional `window`; items of the month view carry `windowGain`. The page gets a switch kept in the URL.

**Tech Stack:** FastAPI, SQLAlchemy async (raw `text()` for the CTE), Pydantic v2 (generated), React + Vitest, `shared/schemas` generators.

**Spec:** `docs/superpowers/specs/2026-10-26-meme-popularity/2026-10-30-s3-trending-window-design.md` (read it first; M1-M2 and N1-N8 are binding). Background: the P5 spec and plan next to it, `repository/popularity.py` (the tree queries and the open-edge definition).

## Global Constraints

- Gain = sum of `weight` of `popularity_absorptions` rows with `kind = 'absorb'`, `reversed_by_id IS NULL`, `source <> 'adjustment'`, `weight > 0` and `created_at >= now() - interval '30 days'` (database clock), credited to the root reached by following open absorb edges from the edge's winner (`loser_image_id = node` gives parent `winner_image_id`). Use `UNION ALL` in the climb, never `UNION`: two edges with the same weight and winner would collapse and lose a copy. Edges of deleted losers still count (no foreign key by design).
- Eligible roots: `images.status = 'active'`, not flagged (a missing `image_extras` row counts as not flagged), gain >= 1. No `min_popularity` rule in the month view. Order `(gain desc, created_at desc, id desc)`, keyset cursor over that triple, `limit + 1` rows, read-only, never commits.
- API: `GET /api/images/trending?window=all|30d` (default `all` = today's behaviour, byte-for-byte unchanged); any other value is a 422 (use a `Literal`). Month items carry `windowGain` (integer, minimum 1, optional in the schema); all-time items omit it. The shared `Meme` schema is not changed. The month cursor uses its own payload key (`gain`, with `created_at` and `id`), so a cursor from the other window fails to decode and restarts at page 1, like any unusable cursor.
- The window length is the constant `TRENDING_WINDOW_DAYS = 30` in `Backend/app/repositories/image_repository.py`, named after the API value `30d`. The open-edge predicate (`a.kind = 'absorb' AND a.reversed_by_id IS NULL`) lives once, as a constant in `repository/popularity.py`, and the new statement uses it; replace the identical clauses in the two existing tree statements there too, with their tests staying green.
- No migration, no new index, unless Task 1 Step 7 measures a 95th percentile over 300 ms (then stop and report; the index is a migration the owner runs). `backend_api.md` stays in sync with the router. Generated files come from the generators only (`documents/generation.md`); revert timestamp-only churn in unrelated generated Python files.
- Test database only: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the command line for `tests/integration/`. Other Claude sessions may be running integration tests on `ocrdb_test` at the same moment and corrupt each other's runs: before each full integration run check no other `pytest tests/integration` process exists, and if a run shows unexplained failures that pass in isolation, rerun against a private scratch database (`CREATE DATABASE ocrdb_test_<name>` plus `CREATE EXTENSION vector` and `pg_trgm` on the same server with the `ocr` role, drop it afterwards; never touch `ocrdb`). Never the main `DATABASE_URL`; never bind ports 8081-8083 or 5173-5175; never touch the live databases.
- Run each pytest root separately (a subshell `( cd Backend && pytest )` so the shell stays at the repo root; `tests/integration`; `tests/docs`) with `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe`. Known flake: `Backend/tests/test_batch_subprocess.py` utf-8 child-env test depends on the shell.
- Frontend gate from `Frontend/memes-frontend/`: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`; the app runs in `StrictMode` with React Compiler lint rules (effects survive a double mount, refs only in handlers/effects). `node_modules` may be absent in a new worktree: `pnpm install --frozen-lockfile`, never change `package.json` or the lockfile.
- Plain, separate git commands. Commits carry the trailers `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01Shd1bU52Pzc4DShVXTt3HC`.

## Review Focus

1. An edge older than 30 days, a reversed edge and an `adjustment` edge never add to the gain (Task 1).
2. A chain A absorbed B, A absorbed into C credits C only, with the edges of both levels, and two equal-weight edges into the same winner both count (Task 1).
3. Flagged, non-active and absorbed (discarded) roots are never listed, and a root whose gain fell to 0 by reversals is not listed (Task 1).
4. Keyset paging over equal gains has no repeats or gaps, and a cursor from the other window restarts at page 1 instead of erroring (Tasks 1 and 2).
5. `window` omitted is exactly today's response (no `windowGain`), `window=7d` is a 422, and switching the page's view resets the list and shows no stale cards from the previous view (Tasks 2 and 3).

---

### Task 1: Windowed trending query

**Files:**
- Modify: `repository/popularity.py` (shared open-edge constant), `Backend/app/repositories/image_repository.py`
- Test: create `tests/integration/test_trending_window.py` (build it like `tests/integration/test_trending.py`; create log rows directly with explicit `created_at` values)

**Interfaces:**
- Produces: `OPEN_EDGE_SQL: str` in `repository/popularity.py`; `TRENDING_WINDOW_DAYS = 30` and `ImageRepository.get_trending_window(cursor_gain: Optional[int], cursor_created_at: Optional[datetime], cursor_id: Optional[uuid.UUID], limit: int)` returning up to `limit + 1` rows with attributes `id, filename, created_at, flagged, popularity, gain` (`flagged` may be None, as in `get_trending`).

- [ ] **Step 1: Failing tests** (names are the checks): `test_edge_inside_window_counts_and_31_days_old_does_not`; `test_reversed_edge_does_not_count`; `test_adjustment_edge_does_not_count`; `test_zero_weight_edge_adds_nothing`; `test_chain_credits_final_root_with_both_levels` (A absorbed B 10 days ago, A absorbed into C 2 days ago; C is listed with the sum, A and B are not); `test_two_equal_edges_into_one_winner_both_count`; `test_edge_of_deleted_loser_still_counts`; `test_flagged_nonactive_and_absorbed_roots_are_not_listed`; `test_root_with_gain_reduced_to_zero_by_reversal_is_not_listed`; `test_missing_extras_row_counts_as_not_flagged`; `test_order_is_gain_then_created_at_then_id`; `test_keyset_pages_equal_gains_without_repeats_or_gaps` (small `limit`, at least 7 memes sharing a gain); `test_small_total_with_gain_is_listed` (popularity below `search.trending.min_popularity` still appears); `test_popularity_column_is_returned` (the stored popularity, not the gain).
- [ ] **Step 2: Run them; expect FAIL** (`DATABASE_URL=... pytest tests/integration/test_trending_window.py -v`).
- [ ] **Step 3: Implement** `OPEN_EDGE_SQL` and swap it into the two existing statements in `repository/popularity.py`; implement `get_trending_window` as one `text()` statement: `WITH RECURSIVE climb(node, weight)` seeded from the recent edges, recursive step joining `popularity_absorptions p ON p.loser_image_id = climb.node AND <open edge on p>` with `UNION ALL`; then the roots (nodes with no open edge where they are the loser) summed per node; join `images` and `image_extras` for the eligibility filter; optional keyset predicate `(gain, created_at, id) < (:g, :c, :i)` added only when all three cursor values are given; order and `LIMIT :limit + 1`. Window from the constant via `make_interval(days => :days)`.
- [ ] **Step 4: Run the new file and `tests/integration/test_popularity_repository*.py` (the existing tree statements); expect PASS.**
- [ ] **Step 5: Mutation check.** Temporarily change `UNION ALL` to `UNION`, run `test_two_equal_edges_into_one_winner_both_count`, see it fail, restore. Temporarily drop the `source <> 'adjustment'` predicate and the `reversed_by_id` clause one at a time; each must fail a named test; restore.
- [ ] **Step 6: Commit** (`feat(popularity): windowed trending query`).
- [ ] **Step 7: Measure (S3 N7).** On a private scratch database (see Global Constraints) with the schema migrated, insert 200,000 synthetic absorb edges (random winners among 30,000 images, `created_at` spread over 400 days, a few chains) and time `get_trending_window` for the first page, 20 runs after a warm-up; report p50 and p95 in the review notes (`docs/superpowers/reviews/2026-10-30-s3-trending-window-review.md`, a short measurements section). If p95 is over 300 ms stop and report; otherwise commit the note. Drop the scratch database.

### Task 2: Schema, service, route, docs

**Files:**
- Modify: `shared/schemas/trendingmemeitem.schema.json`, regenerated trees (`Backend/app/types/generated/`, `Frontend/memes-frontend/src/types/generated/all.d.ts`, the Kotlin DTOs), `Backend/app/services/image_service.py`, `Backend/app/api/images.py`, `backend_api.md`, `docs/superpowers/specs/2026-10-26-meme-popularity/README.md`, the S3 spec status line
- Test: `Backend/tests/test_image_service.py`, `Backend/tests/test_images_endpoints.py`, `tests/integration/test_trending.py` (extend with the endpoint-level window cases)

**Interfaces:**
- Consumes: `ImageRepository.get_trending_window` from Task 1.
- Produces: schema field `windowGain` (optional integer, minimum 1) on `TrendingMemeItem` (generated name `windowGain`; record the real generated spelling if different); `ImageService.get_trending(cursor: Optional[str], limit: int, window: Literal["all", "30d"] = "all") -> TrendingMemesResponse`; `ImageService._encode_trending_window_cursor(gain: int, created_at: datetime, id) -> str` and `_decode_trending_window_cursor(cursor) -> (gain, created_at, id)` or `(None, None, None)`; the route's `window: Literal["all", "30d"] = Query("all")`.

- [ ] **Step 1: Failing tests.** Service (mocked repository): `window="30d"` calls `get_trending_window` with the decoded cursor and builds items with `windowGain` equal to each row's gain; `window="all"` still calls `get_trending` and items have `windowGain` None; a month cursor passed with `window="all"`, and an all-time cursor passed with `window="30d"`, both decode to no cursor (page 1); the next cursor of a month page encodes the last returned row's gain, never the extra row's; `hasNext` follows `limit + 1`. Endpoint (`TestClient`, mocked service): default is `all`; `window=30d` is passed through; `window=7d` is 422; `GET /api/images/trending?window=30d` is not captured by `/{image_id}`. Integration: with real rows, `window=30d` returns the expected order and `windowGain` values, the second page via `nextCursor` continues without repeats, `window=all` response has no `windowGain` key.
- [ ] **Step 2: Run them; expect FAIL.**
- [ ] **Step 3: Implement.** Add `windowGain` to the schema and regenerate all three trees with the generators; implement the service branch (the item construction reuses the all-time `Meme` building and `_fill_texts_and_tags`; only the row source, cursor helpers and `windowGain` differ), the route parameter and the headers as today.
- [ ] **Step 4: Docs.** `backend_api.md`: the `window` parameter, the month item's `windowGain`, the rule (30 days, adjustments and reversed edges excluded, credit to the tree root, listed gain >= 1, no `min_popularity`), the cursor note. Epic README: S3 row becomes "designed" with a link, and the epic's phase list mentions the window; the S3 spec status becomes `planned` and gets the `Plan:` link (it becomes `done` at the end).
- [ ] **Step 5: Run the roots separately** (Backend, whole `tests/integration/`, `tests/docs/`); `git diff Frontend/memes-frontend/src/types/generated/` shows only the intended field.
- [ ] **Step 6: Commit** (`feat(popularity): window parameter on the trending endpoint`).

### Task 3: Page switch

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`, `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`, `Frontend/memes-frontend/src/pages/TrendingMemesPage.tsx`, `Frontend/memes-frontend/src/test/mockApi.ts` (only if the mock needs the new argument)
- Test: `Frontend/memes-frontend/src/pages/TrendingMemesPage.test.tsx`, `Frontend/memes-frontend/src/api/http/HttpMemesApi.trending.test.ts`

**Interfaces:**
- Consumes: the regenerated `TrendingMemeItem` type with optional `windowGain`.
- Produces: `getTrendingMemes(limit?: number, cursor?: string, window?: "all" | "30d"): Promise<TrendingMemesResponse>` (the `window` query parameter is sent only for `"30d"`, so all-time requests stay byte-identical); the page reads and writes the `window` search parameter (`?window=30d`; absent or any other value means all time).

- [ ] **Step 1: Failing tests.** API: `getTrendingMemes(24, undefined, "30d")` requests `...?limit=24&window=30d` and `getTrendingMemes(24)` requests no `window`. Page: default is All time with the first request using window `all`; choosing Last 30 days requests window `30d` once, clears the previous cards and cursor before the new list loads, and updates the URL to `?window=30d`; opening `?window=30d` directly loads the month view; choosing All time removes the parameter; month cards show "+N in 30 days" and "Popularity T"; all-time cards show no gain badge; the month empty state reads "No memes picked up duplicates in the last 30 days."; a response for a superseded window is ignored (switch while the first request is pending; only the second result is shown); "Show more" in the month view passes the month cursor and window; Retry works in both views; one first request under `StrictMode` for each starting window; the switch exposes the selected option (for example `aria-pressed` or `aria-selected`) so the tests and keyboard users can tell.
- [ ] **Step 2: Run them; expect FAIL** (`vitest run src/pages/TrendingMemesPage.test.tsx src/api/http/HttpMemesApi.trending.test.ts`).
- [ ] **Step 3: Implement.** Page state follows the URL window; reload the list when the window changes using the existing request-id guard, and keep the StrictMode single-request guard working per window (a guard keyed on the window value, not a single boolean). Two-option switch above the grid, styled like the page's existing controls.
- [ ] **Step 4: Gate** from `Frontend/memes-frontend/`: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run` (whole suite).
- [ ] **Step 5: Commit** (`feat(popularity): All time / Last 30 days switch on the Trending page`).
