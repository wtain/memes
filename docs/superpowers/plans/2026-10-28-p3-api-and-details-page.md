# P3 API and details page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a meme's popularity number, status and breakdown by source on its details page, through one new read-only endpoint.

**Architecture:** `GET /api/images/meme/{id}/popularity` (router → `ImageService.get_popularity` → `ImageRepository` for the cached number and `PopularityRepository.breakdown` for the rows). A new shared schema feeds the three generators. `MemeDetails` renders a self-contained `PopularityBlock` that fetches by image id and renders nothing while loading or on any error.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic v2 (generated), React + Vitest, `shared/schemas` JSON schemas with json2ts, datamodel-codegen and `AndroidClient/scripts/generate_dtos.py`.

**Spec:** `docs/superpowers/specs/2026-10-26-meme-popularity/2026-10-28-p3-api-and-details-page-design.md` (read it first; its B1–B6 are binding). Epic: `.../README.md`. The repository read here is `repository/popularity.py` (P1).

## Global Constraints

- Read-only: the endpoint writes nothing and commits nothing. Repositories never call `session.commit()`.
- Response is exactly `{ "popularity": int, "status": "normal" | "flagged" | "discarded", "breakdown": [{ "source": str, "count": int }] }`.
- No change to the `Meme` schema, search/list responses or any Android screen. Only the Kotlin DTO is regenerated.
- Test database only: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the command line for `tests/integration/`. Never bind the metal/general/IT ports; never use a live database or the main `DATABASE_URL`.
- Run each pytest root separately: `Backend/tests` (`cd Backend && pytest`), `tests/integration`, `tests/docs`. Python is `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe`.
- Frontend gate before each frontend commit (from `Frontend/memes-frontend/`): `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`. The app runs in `StrictMode` with React Compiler lint rules: effects must survive a double mount, refs are touched only in handlers/effects.
- `backend_api.md` must stay in sync with the router.
- Use plain, separate git commands (no compound constructs). Commits carry the trailers `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01Shd1bU52Pzc4DShVXTt3HC`.

## Review Focus

Failure modes the spec implies but the happy path does not exercise; each has its test in the owning task.

1. Cache and log disagree (drift): the response returns the number from the cache and the rows from the open edges, as stored, with no recomputation (Task 2).
2. An image that is both flagged and absorbed (flag with `duplicateOf`) reads `discarded`, not `flagged` (Task 2).
3. An id that is not a UUID, and an unknown UUID, both answer 404 and never 500 (Task 2).
4. Navigating from one meme to another never shows the previous meme's number (Task 3, block keyed by image id).
5. A 404/500 from an older backend, and a source key the labels do not know, never break the page (Task 3).

---

### Task 1: Shared schema and generated types

**Files:**
- Create: `shared/schemas/popularitybreakdownrow.schema.json`, `shared/schemas/memepopularity.schema.json`
- Modify: `shared/schemas/all.schema.json`
- Regenerated: `Frontend/memes-frontend/src/types/generated/all.d.ts`, `Backend/app/types/generated/` (two new modules), the Kotlin DTOs under `AndroidClient/`

**Interfaces:**
- Produces: Python `Backend.app.types.generated.memepopularity.Schema` (fields `popularity: int`, `status` enum of `normal|flagged|discarded`, `breakdown: list[<row model>]`) and `Backend.app.types.generated.popularitybreakdownrow.Schema` (`source: str`, `count: int`); TypeScript `MemePopularity` and `PopularityBreakdownRow` in `src/types/generated/all`. Task 2 and Task 3 consume these names; if the generator names a type differently, record the real names at the top of the Task 2 and Task 3 reports.

- [ ] **Step 1: Write the two schema files** in the style of `shared/schemas/descriptionfeedbackresponse.schema.json` and `ingestionclusterpage.schema.json` (separate row file referenced with `$ref`, as that page does for its members). `status` is a string `enum` of the three values; every property is `required`; `popularity` and `count` are `integer` with `minimum: 0`.
- [ ] **Step 2: Register both in `shared/schemas/all.schema.json`** next to the existing definitions (`"MemePopularity"`, `"PopularityBreakdownRow"`).
- [ ] **Step 3: Regenerate all three trees** with the commands in `documents/generation.md` (Python: run from `Backend/`; TypeScript: `Frontend/generate-types.sh`; Kotlin: `AndroidClient/scripts/generate_dtos.py`). If a generator cannot run here (network for `npx`), say so in the report and hand-check that the file would match; do not hand-write generated files.
- [ ] **Step 4: Verify** `git status` shows new modules under `Backend/app/types/generated/`, a diff in `all.d.ts`, and a Kotlin diff; no other generated file changed beyond ordering noise. Run `cd Backend && pytest` (root) and `tsc -b` from `Frontend/memes-frontend/`: both pass.
- [ ] **Step 5: Commit** (`feat(popularity): P3 schema and generated types`).

### Task 2: Endpoint, service and docs

**Files:**
- Modify: `Backend/app/repositories/image_repository.py`, `Backend/app/services/image_service.py`, `Backend/app/api/images.py`, `backend_api.md`
- Test: `Backend/tests/test_image_service.py`, `Backend/tests/test_images_endpoints.py`, create `tests/integration/test_popularity_endpoint.py`

**Interfaces:**
- Consumes: Task 1's `memepopularity.Schema` and `popularitybreakdownrow.Schema`; `PopularityRepository(session).breakdown(uuid) -> dict[str, int]` (keys `own` plus source names, roots only); `ImageRepository.get_is_flagged(image_id) -> bool`.
- Produces:
  - `ImageRepository.get_popularity(image_id: str) -> Optional[int]`: the cached `images.popularity`, `None` when there is no such row.
  - `popularity_status(popularity: int, flagged: bool) -> str` and `order_breakdown(parts: dict[str, int]) -> list[tuple[str, int]]` (module-level pure functions in `image_service.py`).
  - `ImageService.get_popularity(image_id: str) -> MemePopularity`; raises `HTTPException(404)` for a non-UUID or unknown id.
  - Route `GET /api/images/meme/{image_id}/popularity` with `response_model=MemePopularity`, same no-cache headers as `get_meme`.

- [ ] **Step 1: Failing unit tests** (`Backend/tests/test_image_service.py`, mocked repo; patch `Backend.app.services.image_service.PopularityRepository`): `popularity_status` truth table (0 → `discarded` whatever the flag; non-zero and flagged → `flagged`; else `normal`); `order_breakdown({"own": 1, "cluster_review": 2, "agent_review": 2, "hash_in_batch": 5})` returns `own` first, then `hash_in_batch`, `agent_review`, `cluster_review` (count descending, ties by source name); `get_popularity` for a root returns the cached number with rows from the breakdown; for `popularity == 0` returns `discarded`, `[]` and never calls `breakdown`; non-UUID id raises 404 before touching the repo; repo returning `None` raises 404; a cached number that differs from the breakdown sum is returned as stored (Review Focus 1).
- [ ] **Step 2: Failing endpoint tests** (`Backend/tests/test_images_endpoints.py`, mock service as the file does): 200 with the exact JSON shape and the no-cache headers; a service-raised 404 maps to 404.
- [ ] **Step 3: Run both; expect FAIL** (`cd Backend && pytest tests/test_image_service.py tests/test_images_endpoints.py -q`).
- [ ] **Step 4: Implement** `get_popularity` in `ImageRepository` (select `Image.popularity` where `Image.id == image_id`, `scalar_one_or_none`, same shape as `get_filename`); the two pure functions and `ImageService.get_popularity` in `image_service.py` (build `PopularityRepository(self.repo.session)` as `mark_flagged` already does; parse the id with `uuid.UUID`, `ValueError` → 404; read the number, then `get_is_flagged`, then breakdown only when the number is not 0); the route in `images.py` placed with the other `/meme/{image_id}/…` routes.
- [ ] **Step 5: Run the unit and endpoint tests; expect PASS.**
- [ ] **Step 6: Integration test** `tests/integration/test_popularity_endpoint.py` on `ocrdb_test`, built like `tests/integration/test_popularity_flagging.py` (`ImageService(ImageRepository(session), MagicMock(), MagicMock())`): a fresh image → `{1, normal, [own 1]}`; a root that absorbed two losers from different sources → number 3 and rows in the ordered form summing to 3; a loser → `{0, discarded, []}`; a loser that is also flagged → `discarded` (Review Focus 2); a flagged root with no edge → `flagged` with its number; unknown UUID and `"not-a-uuid"` → 404 (Review Focus 3); a drifted cache (set `images.popularity` directly to a value that differs from the log) → number from the cache, rows from the log (Review Focus 1); row counts of `popularity_absorptions` and every `images.popularity` are unchanged after each call.
- [ ] **Step 7: Document in `backend_api.md`** the endpoint: path, response fields and the three statuses, the ordering rule, the 404 cases (unknown or non-UUID id; stated as a deliberate difference from `GET /meme/{id}`), that it writes nothing, and that it needs the P1 migration.
- [ ] **Step 8: Run the full roots, separately:** `cd Backend && pytest`; `DATABASE_URL=... pytest tests/integration/` (whole root); `pytest tests/docs/`. All pass.
- [ ] **Step 9: Commit** (`feat(popularity): GET meme popularity endpoint`).

### Task 3: Details page block, frontend API and spec status

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/test/mockApi.ts`, `src/components/MemeDetails.tsx`, any other class that implements `MemesApi` (grep `implements MemesApi`), `docs/superpowers/specs/2026-10-26-meme-popularity/2026-10-28-p3-api-and-details-page-design.md`, `docs/superpowers/specs/2026-10-26-meme-popularity/README.md`
- Create: `src/components/PopularityBlock.tsx`, `src/components/PopularityBlock.test.tsx`, `src/api/http/HttpMemesApi.popularity.test.ts`

**Interfaces:**
- Consumes: Task 1's `MemePopularity` and `PopularityBreakdownRow` types from `src/types/generated/all`; the endpoint from Task 2; `useFetchById(id, fetcher, onResult, onError?)` from `src/utils/useFetchById.ts`.
- Produces: `MemesApi.getMemePopularity(id: string): Promise<MemePopularity>` (HTTP implementation throws `Error` on a non-2xx); `PopularityBlock({ imageId, memesApi })`; exported `sourceLabel(source: string): string` in `PopularityBlock.tsx`.

- [ ] **Step 1: Failing tests.** `HttpMemesApi.popularity.test.ts` (mirror `HttpMemesApi.flag.test.ts`): requests `{baseUrl}/api/images/meme/{id}/popularity` with `Accept: application/json`, returns the parsed body, throws on 404. `PopularityBlock.test.tsx`: for a root shows the number and one line per row with the labels of spec B4 ("Identical files dropped together: 3"); shows `0` and "discarded" with no rows for a discarded image; shows the status for `flagged` and none for `normal`; renders nothing while the promise is pending and after a rejection (Review Focus 5); an unknown source key shows its raw key (Review Focus 5); re-rendered with a new `imageId` it never shows the old number (Review Focus 4; the parent keys the block, so test the keyed remount through `MemeDetails` in `MemeDetails.test.tsx` as well, and add `getMemePopularity` to the mocks there).
- [ ] **Step 2: Run `vitest run` on those files; expect FAIL.**
- [ ] **Step 3: Implement** the API method in `MemesApi.ts`, `HttpMemesApi.ts` (same shape as `getDescriptions`) and the mock in `src/test/mockApi.ts` (default resolves `{ popularity: 1, status: "normal", breakdown: [{ source: "own", count: 1 }] }`); `PopularityBlock` using `useFetchById(imageId, …)` with state `null` until loaded and `null` again on error; the label map of spec B4 with the raw key as fallback; render the block in `MemeDetails.tsx` as `<PopularityBlock key={meme.id} imageId={meme.id} memesApi={memesApi} />` near the existing Flagged checkbox. Match the surrounding markup and class style; no new dependency.
- [ ] **Step 4: Gate:** from `Frontend/memes-frontend/` run `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`: all clean. From the repo root, `git diff Frontend/memes-frontend/src/types/generated/` shows nothing beyond Task 1's committed change.
- [ ] **Step 5: Docs.** Spec status line `draft` → `done` with the `Plan:` link; add this spec to the epic README `Follow-ups` and mark P3 done in its designs list if that list tracks status. Leave `CLAUDE.md` unless a popularity entry mentions where the number is shown.
- [ ] **Step 6: Commit** (`feat(popularity): popularity block on the meme details page`).
