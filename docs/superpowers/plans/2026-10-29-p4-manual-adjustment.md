# P4 Manual adjustment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner set a meme's popularity to a chosen number from the details page, recorded as a signed `adjustment` entry in the absorption log.

**Architecture:** A ninth source `adjustment` with signed weight (migration for the owner to run); the repository stops moving the cache with increments and recomputes it from the tree, floored at 1 for roots, and gains `adjust(image_id, target)`; a `PUT /api/images/meme/{id}/popularity` endpoint returns the P3 response; the P3 `PopularityBlock` gains a "Set popularity" control.

**Tech Stack:** SQLAlchemy async + Alembic, FastAPI, Pydantic v2 (generated), React + Vitest, `shared/schemas` generators.

**Spec:** `docs/superpowers/specs/2026-10-26-meme-popularity/2026-10-29-p4-manual-adjustment-design.md` (read it first; G1-G2 and H1-H8 are binding). Background: P1 `...-p1-data-model-and-absorption-rules-design.md`, P3 `...-p3-api-and-details-page-design.md`. Code: `repository/popularity.py`, `Storage/models.py` (`PopularityAbsorption`).

## Global Constraints

- Repositories never call `session.commit()`; `adjust` is not wrapped in `guarded` (errors reach the caller, spec H6).
- Weight CHECK, exactly: `(source <> 'adjustment' AND weight >= 0) OR (source = 'adjustment' AND weight <> 0)`, for every row kind. Source list gains `adjustment`. `images.popularity >= 0` stays.
- Roots are floored: `popularity = max(1, 1 + sum of open edge weights in the tree)`; a node with an open loser edge is exactly 0. Sums stay raw (spec H3). `adjust` diffs against the floored derived value, not the cached column.
- Target range: integer 1 to 1,000,000 (422 otherwise). Only `active` images are adjustable (409 otherwise); a discarded image (stored 0) is 409; unknown or non-UUID id or a vanished image row is 404.
- The response is the P3 `MemePopularity` (`popularity`, `status`, `breakdown`); the `adjustment` row is listed last and carries a signed count.
- Migration is for the owner to run: never run `alembic upgrade` against any live database; tests use the test database only.
- Test database only: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the command line for `tests/integration/`; never two integration sessions at once; never bind ports 8081-8083 or 5173-5175; never the main `DATABASE_URL`.
- Run each pytest root separately (`cd Backend && pytest`; `tests/integration`; `tests/docs`) with `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe`. Known unrelated flake: `Backend/tests/test_batch_subprocess.py` utf-8 child-env test (passes standalone).
- Frontend gate from `Frontend/memes-frontend/`: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`; the app runs in `StrictMode` with React Compiler lint rules. `node_modules` may be absent in a new worktree: `pnpm install --frozen-lockfile` (offline first), never change `package.json` or the lockfile.
- `backend_api.md` stays in sync with the router. Generated files come from the generators only (`documents/generation.md`).
- Plain, separate git commands. Commits carry the trailers `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01Shd1bU52Pzc4DShVXTt3HC`.

## Review Focus

1. A negative adjustment followed by reversing an absorbed copy never takes a root below 1, and `verify` reports no drift (Task 2).
2. An adjusted meme that is later absorbed carries its adjustment with its tree; a reversal that releases an adjusted subtree leaves the released loser at `max(1, ...)` and the winner correct (Task 2).
3. Setting the same value twice writes no second row; two different values stack and the breakdown shows one summed line (Tasks 2, 3).
4. A failed save keeps the old number and shows an inline error; a discarded or pending image answers 409 and the control is hidden for `discarded` (Tasks 3, 4).
5. The downgrade refuses while an `adjustment` row exists; `recut` weight 0 is still accepted and weight 0 is rejected for `adjustment` (Task 1).

---

### Task 1: Model and migration

**Files:**
- Modify: `Storage/models.py` (`PopularitySource`, the weight CHECK in `PopularityAbsorption`)
- Create: `Storage/alembic/versions/<new id>_popularity_adjustment_source.py` with `down_revision = 'a4f7c1e92b68'` (the current single head; confirm with the head-finding script in `tests/integration/test_popularity_migration.py` style or by grepping `down_revision`)
- Test: `tests/integration/test_popularity_migration.py`, `tests/integration/test_popularity_model.py`

**Interfaces:**
- Produces: `PopularitySource.ADJUSTMENT == "adjustment"` and `PopularitySource.ALL` containing it (ninth entry, appended last); the table accepts a signed non-zero `weight` for `adjustment` rows of both kinds.

- [ ] **Step 1: Failing tests.** In `test_popularity_model.py`: an `adjustment` absorb row with weight `-4` and one with `+28` insert; weight `0` for `adjustment` is rejected; weight `0` for `recut` is accepted; weight `-1` for `hash_in_batch` is rejected; a reverse row copying a negative adjustment weight inserts. In `test_popularity_migration.py`: the new revision's upgrade produces the same CHECK definitions as the ORM declares; downgrade restores the old source list and `weight >= 0`; downgrade raises a clear error while an `adjustment` row exists and succeeds when none does.
- [ ] **Step 2: Run them; expect FAIL** (`DATABASE_URL=... pytest tests/integration/test_popularity_model.py tests/integration/test_popularity_migration.py -v`).
- [ ] **Step 3: Implement** `PopularitySource.ADJUSTMENT`, extend `ALL`, replace the weight CHECK in the model with the Global Constraints predicate (keep the constraint name `ck_popularity_absorptions_weight`); write the migration (drop and recreate `ck_popularity_absorptions_source` and `ck_popularity_absorptions_weight`; downgrade first `SELECT count(*)` of `adjustment` rows and raise `RuntimeError` with an explanatory message if any, then restore the originals).
- [ ] **Step 4: Run the two files; expect PASS.** Then run the whole `tests/integration/` root once (the shared schema comes from the models): all pass.
- [ ] **Step 5: Commit** (`feat(popularity): adjustment source and signed weight`).

### Task 2: Repository floor, recompute and `adjust`

**Files:**
- Modify: `repository/popularity.py`, `batch/check_image_popularity.py` only if its output wording needs the floor (the logic lives in `verify`)
- Test: `tests/integration/test_popularity_repository.py`, `tests/integration/test_popularity_properties.py`, `tests/integration/test_check_image_popularity.py`, create `tests/integration/test_popularity_adjust.py`

**Interfaces:**
- Consumes: Task 1's `PopularitySource.ADJUSTMENT`.
- Produces: `PopularityRepository.adjust(image_id: uuid.UUID, target: int) -> PopularityAbsorption | None` (raises `ValueError` for `target < 1`, `AlreadyDiscardedError` when the image has an open loser edge, `WinnerGoneError` when the image row is missing; returns `None` when the difference is 0); `popularity_of` and `verify` floor roots at 1; every write operation recomputes the cache from the tree instead of moving it by increments.

- [ ] **Step 1: Failing tests** in `test_popularity_adjust.py` (real DB): set 1 → 40 writes one `+39` `adjustment` edge, `images.popularity == 40`, `popularity_of == 40`; the same call again returns `None` and writes nothing; set a root with 11 absorbed copies to 1 writes `-11` and reads 1; after set-to-40, three more absorbed losers read 43 (G2); a loser raises `AlreadyDiscardedError`; a missing image raises `WinnerGoneError`; `target=0` raises `ValueError`; `breakdown` shows one `adjustment` entry equal to the sum of two stacked adjustments (Review Focus 3). Floor cases (Review Focus 1, 2): a root with two absorbed copies and a `-4` adjustment reads 1 (raw -1), reversing one copy keeps it at 1 and `verify()` returns no drift; absorbing that root into another image moves exactly `weight + tree_weight` (raw) into the winner and sets the loser to 0; reversing that edge releases the loser at `max(1, 1 + its subtree)` and the winner returns to its previous value. Add to `test_popularity_properties.py` a randomized operation that adjusts a random root, keeping its cache-equals-`verify` invariant; existing P1/P2 tests must pass unchanged.
- [ ] **Step 2: Run the new and existing popularity tests; expect the new ones to FAIL, existing to PASS.**
- [ ] **Step 3: Implement.** A private `_refresh_cache(root)` that sets `images.popularity` to `GREATEST(1, 1 + tree_weight(root))` for an image with no open loser edge (use the existing `_TREE_WEIGHT_SQL`); call it from `absorb` (for the root; the loser is still written exactly 0 with `_zero_popularity`), `record_dropped_copies`, `reverse` (for the root, and for a released loser set `GREATEST(1, 1 + its tree_weight)`) and `adjust`, replacing the `_add_popularity` increments (delete the method if unused). `popularity_of` returns `0` for a loser else `max(1, 1 + tree_weight)`; `verify` floors a non-loser's expected value at 1. `adjust` uses the same lock-and-retry loop as `absorb`: resolve the root, raise `AlreadyDiscardedError(open edge)` if the image is not its own root, lock the image row with `_lock_images`, re-check, raise `WinnerGoneError` when the row is missing, compute `delta = target - floored current`, return `None` for 0, else insert the `adjustment` row (`kind=ABSORB`, `loser_image_id=None`, `winner_image_id=image_id`, `weight=delta`, `winner_content_hash` from the image, no `dedupe_key`, `reason` or `batch_run_id`), flush, refresh the cache, flush.
- [ ] **Step 4: Run the whole `tests/integration/` root; expect PASS** (P1, P2 and P3 tests included).
- [ ] **Step 5: Commit** (`feat(popularity): floor at 1, recompute the cache, adjust to a target`).

### Task 3: Endpoint, service, schemas and API docs

**Files:**
- Create: `shared/schemas/memepopularityupdate.schema.json`
- Modify: `shared/schemas/popularitybreakdownrow.schema.json` (drop the `minimum: 0` on `count`), `shared/schemas/all.schema.json`, regenerated trees (`Backend/app/types/generated/`, `Frontend/memes-frontend/src/types/generated/all.d.ts`, the Kotlin DTOs), `Backend/app/api/images.py`, `Backend/app/services/image_service.py`, `Backend/app/repositories/image_repository.py` (an accessor returning an image's `status`, if none exists), `backend_api.md`
- Test: `Backend/tests/test_image_service.py`, `Backend/tests/test_images_endpoints.py`, create `tests/integration/test_popularity_set_endpoint.py`

**Interfaces:**
- Consumes: Task 2's `PopularityRepository.adjust`; the P3 `ImageService.get_popularity`, `popularity_status`, `order_breakdown`.
- Produces: generated request model `Backend.app.types.generated.memepopularityupdate.Schema` with `popularity: int` constrained 1..1000000 (record the real generated name if it differs); `ImageService.set_popularity(image_id: str, value: int) -> MemePopularity`; `order_breakdown` puts `own` first and `adjustment` last; route `PUT /api/images/meme/{image_id}/popularity` with the request model as body and `response_model=MemePopularity`, same no-cache headers as the P3 read.

- [ ] **Step 1: Schemas.** Write `memepopularityupdate.schema.json` (object, `popularity` integer, `minimum` 1, `maximum` 1000000, required), relax `count`, register in `all.schema.json`, run all three generators (`documents/generation.md`); verify the diffs show only the new request model, the relaxed `count` and nothing hand-written.
- [ ] **Step 2: Failing tests.** Service (mocked repos, patch `PopularityRepository` as the P3 tests do): `set_popularity` returns the response after `adjust`; non-UUID and unknown id → 404; a non-`active` image status → 409; a stored popularity of 0 → 409; `AlreadyDiscardedError` → 409; `WinnerGoneError` → 404; `order_breakdown({"own": 1, "adjustment": -4, "cluster_review": 2})` is `own, cluster_review, adjustment`. Endpoint: 200 shape; 422 for `0`, `-3`, `1000001`, `40.5` and a string; 409/404 map through. Integration (`test_popularity_set_endpoint.py`, built like `test_popularity_endpoint.py`): set on an active root returns the new number and `+delta` in the breakdown, a second identical call changes nothing; a pending image → 409 and no row; a loser → 409; a flagged active image can be set and `verify()` stays clean; nothing commits inside the repository (the test session decides).
- [ ] **Step 3: Run them; expect FAIL.**
- [ ] **Step 4: Implement** `ImageService.set_popularity` (parse the id once as in P3 → 404; status accessor → 404 if the row is missing, 409 unless `active`; read the cached popularity → 409 when 0; call `adjust`; map the exceptions; return `await self.get_popularity(image_id)`), the `order_breakdown` change, the route in `images.py` next to the P3 read, and the status accessor. Update the P3 unit tests that assert the old ordering rule only if they now fail.
- [ ] **Step 5: Docs.** `backend_api.md`: the PUT (body, 200/404/409/422, bounds, `active` only, discarded → 409, idempotent by value) and the signed `adjustment` row in the P3 read description; remove or adjust any text saying counts are never negative.
- [ ] **Step 6: Run the full roots, separately:** `cd Backend && pytest`; `DATABASE_URL=... pytest tests/integration/`; `pytest tests/docs/`. All pass. Frontend `tsc -b` still compiles against the regenerated types.
- [ ] **Step 7: Commit** (`feat(popularity): PUT meme popularity endpoint`).

### Task 4: Details-page control and docs

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/test/mockApi.ts`, `src/components/PopularityBlock.tsx`, `src/utils/popularitySources.ts`, `CLAUDE.md`, `docs/schema.md`, the P4 spec status line, the epic `README.md`
- Test: `src/components/PopularityBlock.test.tsx`, `src/utils/popularitySources.test.ts`, create `src/api/http/HttpMemesApi.popularitySet.test.ts`

**Interfaces:**
- Consumes: Task 3's `MemePopularityUpdate` TypeScript type and the PUT endpoint; the P3 `PopularityBlock({ imageId, memesApi })`, `sourceLabel`.
- Produces: `MemesApi.setMemePopularity(id: string, popularity: number): Promise<MemePopularity>` (HTTP PUT with a JSON body, throws `Error` on a non-2xx); the `adjustment` label "Manual adjustment"; a signed rendering (`+28`, `-4`) for the `adjustment` row only.

- [ ] **Step 1: Failing tests.** HTTP: PUT to `{baseUrl}/api/images/meme/{id}/popularity` with `Content-Type: application/json` and body `{"popularity": N}`, returns the parsed body, throws on 409. Block: the control shows a number input with the current value and a Save button for `normal` and `flagged`, and is absent for `discarded`; Save is disabled while the value is unchanged, outside 1..1000000, or a save is in flight; saving calls `setMemePopularity(imageId, N)` once and the block shows the response (number, new adjustment line `Manual adjustment: +28`); a rejected save keeps the old number and shows an inline `role="alert"` message (Review Focus 4); a negative adjustment renders `Manual adjustment: -4`; no `alert()` or `confirm()` anywhere. Labels: `sourceLabel("adjustment") === "Manual adjustment"`.
- [ ] **Step 2: Run `vitest run` on those files; expect FAIL** (install dependencies first if `node_modules` is missing).
- [ ] **Step 3: Implement** the API method (same shape as the P3 read, plus method, headers and body), the mock default, the label, the signed rendering and the control inside `PopularityBlock` (state for the input and the pending flag; on success `setData(response)`; keep the P3 behavior of rendering nothing while loading or on a read error; effects must survive a `StrictMode` double mount). Match the surrounding markup and class style.
- [ ] **Step 4: Docs.** `CLAUDE.md`: in the `check_image_popularity` entry note the floor at 1 and the `adjustment` source, and mention the details-page control where the popularity entries describe the page. `docs/schema.md`: the ninth source and the signed weight. P4 spec status `draft` → `done` with the `Plan:` link; epic README: mark P4 done.
- [ ] **Step 5: Gate:** from `Frontend/memes-frontend/`: `tsc -b`, `eslint src/ --max-warnings 0`, whole `vitest run`; from the repo root `git diff Frontend/memes-frontend/src/types/generated/` shows nothing beyond Task 3's committed change.
- [ ] **Step 6: Commit** (`feat(popularity): set-popularity control on the meme details page`).
