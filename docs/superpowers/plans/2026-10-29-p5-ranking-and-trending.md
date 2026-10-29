# P5 Popularity ranking and Trending memes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a small capped popularity boost to text-search ranking and an all-time "Trending memes" page.

**Architecture:** `ImageRepository._ranked_page` adds `weight * min(log2(max(1, popularity)), max_log2)` to each row's score before the existing sort and keyset cursor; a second cached settings loader supplies the two numbers. A new read-only endpoint `GET /api/images/trending` pages active, unflagged memes by `(popularity desc, created_at desc, id desc)`; a new frontend page renders them with a popularity badge.

**Tech Stack:** FastAPI, SQLAlchemy async, Dynaconf settings, Pydantic v2 (generated), React + Vitest, `shared/schemas` generators.

**Spec:** `docs/superpowers/specs/2026-10-26-meme-popularity/2026-10-29-p5-ranking-and-trending-design.md` (read it first; K1-K4 and L1-L7 are binding). Search ranking background: `docs/adr/adr-2026-10-01-search-ranking-in-python.md`, `repository/search_ranking.py`.

## Global Constraints

- Boost formula, exactly: `round(base_score + weight * min(log2(max(1, popularity)), max_log2), 6)`; `RankedRow.score` holds the boosted, rounded value and the keyset cursor `(score, created_at, id)` encodes it unchanged. Defaults in `environments/settings.yaml`: `search.ranking.popularity.weight: 0.03`, `search.ranking.popularity.max_log2: 5`. Validation: `weight` finite and >= 0 (0 disables the boost), `max_log2` finite and > 0; a missing key is a startup error. The frozen `RankingWeights` and the tests that construct it stay untouched (a separate cached loader).
- Only `_ranked_page` (queries with text) changes. Queries without text, `/similar`, recommendations and the meme-from-text path are untouched. No extra database query is added to the ranked path.
- Trending: `status = active`, not flagged (a missing `image_extras` row counts as not flagged), `popularity >= search.trending.min_popularity` (required key, default 2 in `environments/settings.yaml`); order `(popularity desc, created_at desc, id desc)`; keyset cursor over that triple; an invalid or foreign cursor restarts at page 1; no facets; `limit` 1..100; read-only, never commits; the route is declared before `/{image_id}`. The shared `Meme` schema is not changed.
- No migration and no new index. `backend_api.md` stays in sync with the router. Generated files come from the generators only (`documents/generation.md`); revert timestamp-only churn in unrelated generated Python files.
- Test database only: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the command line for `tests/integration/`. Other Claude sessions may be running integration tests on `ocrdb_test` at the same moment and corrupt each other's runs: before each full integration run check no other `pytest tests/integration` process exists, and if a run shows unexplained failures that pass in isolation, rerun against a private scratch database (`CREATE DATABASE ocrdb_test_<name>` plus `CREATE EXTENSION vector` and `pg_trgm` on the same server with the `ocr` role, drop it afterwards; never touch `ocrdb`). Never the main `DATABASE_URL`; never bind ports 8081-8083 or 5173-5175.
- Run each pytest root separately (`cd Backend && pytest` in a subshell so the shell stays at the repo root; `tests/integration`; `tests/docs`) with `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe`. Known flake: `Backend/tests/test_batch_subprocess.py` utf-8 child-env test depends on the shell.
- Frontend gate from `Frontend/memes-frontend/`: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`; the app runs in `StrictMode` with React Compiler lint rules (effects survive a double mount, refs only in handlers/effects). `node_modules` may be absent in a new worktree: `pnpm install --frozen-lockfile` (offline first), never change `package.json` or the lockfile.
- Plain, separate git commands. Commits carry the trailers `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01Shd1bU52Pzc4DShVXTt3HC`.

## Review Focus

1. The boost never lets a popular weaker match beat a clearly better one: a note-exact hit of popularity 1 outranks an OCR-fuzzy hit at or above `2 ** max_log2` copies (Task 1).
2. Keyset paging over boosted scores with ties produces no repeats or gaps, and weight 0 reproduces today's order exactly (Task 1).
3. Trending excludes flagged, non-active, discarded (0) and below-minimum memes, pages without repeats, and an invalid cursor restarts at page 1 instead of erroring (Task 2).
4. The trending response carries the number beside the meme and `Meme` is unchanged (Task 2).
5. The page shows an empty state and an error state, loads more with the cursor, and opens the meme details on click (Task 3).

---

### Task 1: Popularity boost in search ranking

**Files:**
- Modify: `repository/search_ranking.py`, `Backend/app/repositories/image_repository.py` (`_ranked_page`), `environments/settings.yaml`, `docs/adr/adr-2026-10-01-search-ranking-in-python.md`, `CLAUDE.md` (the search-ranking sentence in Configuration), `backend_api.md` (the search endpoint's ordering text)
- Test: `tests/integration/test_search_scoring.py` (sibling settings tests), create `tests/integration/test_search_popularity_boost.py`

**Interfaces:**
- Produces: `PopularityBoost(weight: float, max_log2: float)` (frozen dataclass) with `bonus(popularity: int) -> float` returning `weight * min(log2(max(1, popularity)), max_log2)` unrounded; `get_popularity_boost() -> PopularityBoost` (`lru_cache(maxsize=1)`, raises `ValueError` naming the bad or missing keys, same style as `get_weights`).

- [ ] **Step 1: Failing tests.** Settings (next to the `get_weights` tests, with the same `cache_clear()` monkeypatch pattern): defaults load; missing `popularity` block, missing `weight`, missing `max_log2`, negative weight, non-finite, bool and string values all raise `ValueError` naming the key; `weight: 0` is valid; `max_log2: 0` is invalid. Pure: `bonus(1) == 0`, `bonus(0) == 0`, `bonus(8) == weight * 3`, `bonus(2 ** 20) == weight * max_log2`. Integration (`test_search_popularity_boost.py`, built like `tests/integration/test_search_ranking_repository.py`): two equally matching images of popularity 1 and 8 rank the popular first; with weight 0 (monkeypatch `get_popularity_boost`) the order equals the unboosted order; a note-exact hit of popularity 1 outranks an OCR-fuzzy hit of popularity `2 ** max_log2` (Review Focus 1); popularity beyond the cap changes nothing; a result set with equal boosted scores pages with a small `limit` without repeats or gaps (Review Focus 2); a query without text is unchanged; the cursor from page 1 reproduces the exact float on page 2.
- [ ] **Step 2: Run them; expect FAIL** (`DATABASE_URL=... pytest tests/integration/test_search_scoring.py tests/integration/test_search_popularity_boost.py -v`).
- [ ] **Step 3: Implement** `PopularityBoost` and `get_popularity_boost()` in `repository/search_ranking.py` (reuse `_SCORE_DECIMALS`); add the two keys under `search.ranking.popularity` in `environments/settings.yaml` with a comment pointing at the spec; in `_ranked_page` select `img.popularity`, compute `round(scores[r.id] + boost.bonus(r.popularity), _SCORE_DECIMALS)` per row, keep `RankedRow` as it is, and leave sorting, cursor filtering and the warn log unchanged.
- [ ] **Step 4: Docs.** ADR "Consequences": one line that scores now include a capped popularity term (link the P5 spec). `CLAUDE.md`: name the two new keys and the weight-0 switch. `backend_api.md`: the search ordering description mentions the boost and where it is configured.
- [ ] **Step 5: Run the full roots separately** (Backend, whole `tests/integration/`, `tests/docs/`); all pass.
- [ ] **Step 6: Commit** (`feat(popularity): capped popularity boost in text-search ranking`).

### Task 2: Trending endpoint

**Files:**
- Create: `shared/schemas/trendingmemeitem.schema.json`, `shared/schemas/trendingmemesresponse.schema.json`
- Modify: `shared/schemas/all.schema.json`, regenerated trees (`Backend/app/types/generated/`, `Frontend/memes-frontend/src/types/generated/all.d.ts`, the Kotlin DTOs), `environments/settings.yaml`, `Backend/app/repositories/image_repository.py`, `Backend/app/services/image_service.py`, `Backend/app/api/images.py`, `backend_api.md`
- Test: `Backend/tests/test_image_service.py`, `Backend/tests/test_images_endpoints.py`, create `tests/integration/test_trending.py`

**Interfaces:**
- Consumes: the `Meme` construction and `ImageService._fill_texts_and_tags(items)` used by `get_untagged`.
- Produces: generated `Backend.app.types.generated.trendingmemeitem.Schema` (`meme: Meme`, `popularity: int`) and `trendingmemesresponse.Schema` (`items`, optional `nextCursor`, `hasNext`) (record the real generated names if different); setting `search.trending.min_popularity` (int >= 1, required, default 2); `ImageRepository.get_trending(cursor_popularity: Optional[int], cursor_created_at: Optional[datetime], cursor_id: Optional[uuid.UUID], limit: int, min_popularity: int)` returning rows with `id, filename, created_at, flagged, popularity` (limit + 1 rows); `ImageService.get_trending(cursor: Optional[str], limit: int) -> TrendingMemesResponse`; route `GET /api/images/trending` (`limit` 1..100 default 36, `cursor` optional, no-cache headers).

- [ ] **Step 1: Schemas and setting.** Write the two schema files (item: `meme` as `$ref` to `meme.schema.json`, `popularity` integer minimum 0, both required; response: `items` array of the item, `nextCursor` string, `hasNext` boolean), register them in `all.schema.json`, add `search.trending.min_popularity: 2` to `environments/settings.yaml`, and run all three generators; verify the diffs show only the new types and no hand edits.
- [ ] **Step 2: Failing tests.** Service (mocked repo): cursor round trip `(popularity, created_at, id)`; an invalid, truncated or foreign (relevance-format) cursor decodes to "no cursor" instead of raising; `get_trending` returns items with the number beside the meme, `hasNext` and `nextCursor` pointing at the last returned row (not the extra one); the missing setting raises at startup-style validation. Endpoint: 200 shape and no-cache headers; `limit=0` and `limit=101` → 422. Integration (`test_trending.py`): ordering by popularity then `created_at` then id; a flagged image, a `pending` image, a discarded image (stored 0) and an image below `min_popularity` are absent; an image with no `image_extras` row is eligible; paging with `limit=2` over a set with equal popularity yields every image exactly once; a bogus cursor returns page 1; nothing is written (row counts of `images` and `popularity_absorptions` unchanged).
- [ ] **Step 3: Run them; expect FAIL.**
- [ ] **Step 4: Implement** the repository query (one select over `images` outer-joined to `image_extras`, filters per Global Constraints, order and keyset predicate on the triple), the service method (decode cursor with soft-fail, build `Meme` items, `_fill_texts_and_tags`, wrap each in the generated item, cursor from the last returned row) and the route placed with the other literal GETs before `/{image_id}`; read `min_popularity` through a small cached loader validated like the ranking keys.
- [ ] **Step 5: Docs.** `backend_api.md`: the endpoint (parameters, response, eligibility rules, cursor behaviour, setting name).
- [ ] **Step 6: Run the full roots separately**; all pass; frontend `tsc -b` still compiles against the regenerated types (install dependencies first if needed).
- [ ] **Step 7: Commit** (`feat(popularity): GET trending memes endpoint`).

### Task 3: Trending memes page

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/test/mockApi.ts`, `src/app/router.tsx`, `src/app/AppLayout.tsx`, the P5 spec status line, the epic `README.md`
- Create: `src/pages/TrendingMemesPage.tsx`, `src/pages/TrendingMemesPage.test.tsx`, `src/api/http/HttpMemesApi.trending.test.ts`

**Interfaces:**
- Consumes: Task 2's generated `TrendingMemesResponse` / `TrendingMemeItem` TypeScript types and the endpoint; `MemeCard`, `MemeDetailsModal` (as `MemesList` uses them), `useFetchById` is not needed.
- Produces: `MemesApi.getTrendingMemes(limit?: number, cursor?: string): Promise<TrendingMemesResponse>` (same shape as `iterateUntaggedMemes`; throws `Error` on a non-2xx); page component `TrendingMemesPage({ memesApi })` at route `/trending-memes`; nav entry "Trending memes" next to "Trends".

- [ ] **Step 1: Failing tests.** HTTP: requests `{baseUrl}/api/images/trending?limit=..&cursor=..` with `Accept: application/json`, returns the parsed body, throws on a 500. Page: shows a card per item with its popularity badge; "Show more" appears when `hasNext`, calls the API with the cursor and appends without duplicating; an empty first page shows the empty-state message; a rejected first call shows an error message and no cards; clicking a card opens the meme details (as the list pages do); effects survive a `StrictMode` double mount (one first request); the nav renders a "Trending memes" link distinct from "Trends".
- [ ] **Step 2: Run `vitest run` on those files; expect FAIL** (install dependencies first if `node_modules` is missing).
- [ ] **Step 3: Implement** the API method, mock default, page (simple responsive grid of `MemeCard` each wrapped with a small badge showing the number, "Show more" button, empty and error states, `MemeDetailsModal` on click), route and nav link, matching the surrounding class style; no new dependency.
- [ ] **Step 4: Docs/status.** P5 spec status `planned` → `done` (keep the Plan link); epic README marks P5 done like P4.
- [ ] **Step 5: Gate:** from `Frontend/memes-frontend/`: `tsc -b`, `eslint src/ --max-warnings 0`, whole `vitest run`; from the repo root `git diff Frontend/memes-frontend/src/types/generated/` shows nothing beyond Task 2's committed change.
- [ ] **Step 6: Commit** (`feat(popularity): Trending memes page`).
