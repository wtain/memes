# S2 Save Count Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Android app reports each gallery save to the server, the server counts saves per meme (saves of discarded duplicates count for the survivor), and the web meme details page shows the count.

**Architecture:** An append-only table `meme_saves` (no foreign key to `images`). `POST /api/images/meme/{id}/saves` inserts a row; `GET` on the same path counts rows whose `image_id` is in the requested image's duplicate tree (the walk `repository/popularity.py` already does, sharing `OPEN_EDGE_SQL`). Android sends the POST best-effort after the file is written; the web adds a `SaveCountBlock` beside `PopularityBlock`.

**Tech Stack:** PostgreSQL + Alembic, SQLAlchemy async, FastAPI, JSON schemas with three generators, React + vitest, Kotlin + Retrofit + MockK.

**Spec:** `docs/superpowers/specs/2026-10-26-meme-popularity/2026-10-30-s2-save-count-design.md` (binding; read it first).

## Global Constraints

- Work only in this worktree. Never run a migration, restart a backend or touch a live database (`it`, `general`, `metal`); the owner does that. Subagents never receive the main `DATABASE_URL`.
- Never bind ports 8081-8083 or 5173-5175 or 5432/5434/5436 servers; the test database is `ocrdb_test` on `ocr-db`, port 5432. Other sessions share it: before an integration run check no other `pytest tests/integration` process is running, or use a private scratch database (`ocrdb_test_s2` with `vector` and `pg_trgm`, dropped afterwards, never `ocrdb`).
- Python: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe`. Run each pytest root separately (`Backend/tests`, `tests/integration`, `tests/docs`). Integration runs need `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the command line.
- Repositories never call `session.commit()` (`get_async_db` does). ORM models only in `Storage/models.py`.
- `backend_api.md` is updated in the same change as the routes. Generated files (`Backend/app/types/generated/`, `Frontend/memes-frontend/src/types/generated/all.d.ts`, `AndroidClient/.../Models.kt`) come from the generators only (`documents/generation.md`, `Frontend/generate-types.sh`, `AndroidClient/scripts/generate_dtos.py`); a new schema is also listed by hand in `shared/schemas/all.schema.json`.
- Frontend before commit: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`. Android before commit: `.\gradlew :app:testDebugUnitTest --no-daemon` with `JAVA_HOME` set to Android Studio's JBR.
- The migration's `down_revision` is `c8e2f5a1d093` (the current head). Use the Edit tool for repo text, not PowerShell Get/Set-Content round trips.
- Commits end with the trailers `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01Shd1bU52Pzc4DShVXTt3HC`.

## Review Focus

Inputs and conditions the spec implies and a person would hit; each has a test in the owning task.

1. A non-UUID or unknown id on either endpoint answers 404, never 500, and a POST for an unknown id inserts nothing (Task 2).
2. A save of a folded-in copy shows on the survivor, transitively; undoing the fold (reversed edge) hands it back; a deleted image's saves still count (Task 1).
3. A meme with no saves reads `count 0`, `lastSavedAt null` and the page still renders it (Tasks 1 and 3).
4. Navigating from meme A to meme B on the web never shows A's count under B, and a failed or slow read renders nothing (Task 3).
5. On Android a failing, slow or 404 report never turns a successful save into an error, and no report is sent when the gallery write itself failed (Task 4).

---

### Task 1: Table, migration and repository

**Files:**
- Modify: `Storage/models.py` (add `MemeSave` after `PopularityAbsorption`)
- Create: `Storage/alembic/versions/d5b8e3a07c41_add_meme_saves.py`
- Create: `repository/meme_saves.py`
- Modify: `repository/cut_copier.py:25-29` (add `"meme_saves"` to `NOT_COPIED_TABLES`)
- Modify: `docs/image-derived-data.md` (row for `meme_saves`, after the `popularity_absorptions` row)
- Test: `tests/integration/test_meme_saves_repository.py`, `tests/integration/test_meme_saves_migration.py`

**Interfaces:**
- Consumes: `OPEN_EDGE_SQL` from `repository/popularity.py`; test helpers `make_image` from `tests/integration/test_popularity_repository.py` and `PopularityRepository.absorb(loser_id, winner_id, source)`; the `db_session` fixture.
- Produces: `class MemeSaveRepository(session: AsyncSession)` with `async record(image_id: uuid.UUID) -> None` (inserts one row, flushes, never commits) and `async summary(image_id: uuid.UUID) -> tuple[int, datetime | None]` (saves of the image and of every image in its tree through open absorb edges; `(0, None)` when none). `Storage.models.MemeSave`.

- [ ] **Step 1: Write the failing repository tests** in `test_meme_saves_repository.py` (`@pytest.mark.asyncio(loop_scope="session")`, `db_session`, `make_image`):
  - `test_summary_of_unsaved_image_is_zero_none`: `summary(img.id) == (0, None)`.
  - `test_every_record_counts`: two `record(img.id)` calls give count 2 and a `last_saved_at` that is not None and is the maximum `created_at` of the rows.
  - `test_loser_saves_count_for_winner`: absorb `l` into `w` via `PopularityRepository.absorb(l.id, w.id, "cluster_review")`, `record(l.id)` once and `record(w.id)` once; `summary(w.id)[0] == 2` and `summary(l.id)[0] == 1` (a loser's own subtree).
  - `test_chain_credits_final_root`: absorb c into b, b into a, `record(c.id)`; `summary(a.id)[0] == 1`.
  - `test_reversed_edge_hands_saves_back`: after the absorb of the previous test shape, reverse the edge with `PopularityRepository(db_session).reverse_open_edge(l.id, "unflag")`, then `summary(w.id)[0]` excludes the loser's save and `summary(l.id)[0]` includes it.
  - `test_deleted_image_saves_still_count`: record for an image, delete the `Image` row, `summary(id)[0] == 1`.

- [ ] **Step 2: Run to verify they fail** (`ModuleNotFoundError: repository.meme_saves`).
  Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_meme_saves_repository.py -v`

- [ ] **Step 3: Add `MemeSave`** to `Storage/models.py`: `__tablename__ = "meme_saves"`; `id` UUID primary key default `uuid.uuid4`; `image_id` UUID not null with **no foreign key**; `created_at` timezone-aware `DateTime`, not null, `server_default=func.now()`; `Index("ix_meme_saves_image_id", "image_id")`. Docstring: append-only, why there is no foreign key (same reason as `PopularityAbsorption`).

- [ ] **Step 4: Write the migration** `d5b8e3a07c41_add_meme_saves.py` (revision `d5b8e3a07c41`, down_revision `c8e2f5a1d093`, header comment naming epic meme-popularity S2): `upgrade` creates the table and index exactly as the model declares them (`created_at` server default `sa.text("now()")`); `downgrade` drops the index and the table. Style: copy the typing and header of `c8e2f5a1d093_popularity_adjustment_source.py`.

- [ ] **Step 5: Implement `repository/meme_saves.py`.** `record` is one `insert(MemeSave)` executed and flushed. `summary` is one recursive statement: `tree(id)` seeded with the requested id and extended with `UNION` (not `UNION ALL`, so a cycle cannot loop) through `popularity_absorptions a JOIN tree t ON a.winner_image_id = t.id WHERE {OPEN_EDGE_SQL} AND a.loser_image_id IS NOT NULL`, then `SELECT count(*), max(s.created_at) FROM meme_saves s JOIN tree t ON s.image_id = t.id`. Return `(int(count), last)`. Same shape as `_TREE_WEIGHT_SQL` in `repository/popularity.py`; import `OPEN_EDGE_SQL` rather than copying it.

- [ ] **Step 6: Write the migration test** `test_meme_saves_migration.py` following `test_popularity_migration.py`: load the migration module by path, run `upgrade` and `downgrade` on a scratch connection inside a transaction, and assert the created table's columns (names, types, nullability), primary key, index `ix_meme_saves_image_id` and **absence of any foreign key** equal what `Base.metadata.tables["meme_saves"]` declares; downgrade removes the table.

- [ ] **Step 7: Add the cut-copier and docs entries.** `"meme_saves"` goes into `NOT_COPIED_TABLES`; the new `docs/image-derived-data.md` row says: none (no foreign key to `images`; plain ids, so history survives deletion); not copied; a cut starts with no saves; same wording style as the `popularity_absorptions` row.

- [ ] **Step 8: Run all three** `test_meme_saves_repository.py`, `test_meme_saves_migration.py` (with the DATABASE_URL form above) and `tests/docs/` (`H:\...\python.exe -m pytest tests/docs -v`). Expected: all pass.

- [ ] **Step 9: Commit** `feat(popularity): S2 meme_saves table, migration and repository` (add only the files listed above).

---

### Task 2: API, schema and generated types

**Files:**
- Create: `shared/schemas/memesaves.schema.json`
- Modify: `shared/schemas/all.schema.json` (add `"MemeSaves": { "$ref": "memesaves.schema.json" }` next to `MemePopularity`)
- Regenerate: `Backend/app/types/generated/`, `Frontend/memes-frontend/src/types/generated/all.d.ts`, `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt`
- Modify: `Backend/app/services/image_service.py` (after `set_popularity`)
- Modify: `Backend/app/api/images.py` (after the popularity routes, before `mark_flagged`; all `/meme/{image_id}/...`, so before `/{image_id}`)
- Modify: `backend_api.md`
- Test: `tests/integration/test_meme_saves_endpoint.py`, `Backend/tests/test_images_endpoints.py`, `Backend/tests/test_image_service.py`

**Interfaces:**
- Consumes: `MemeSaveRepository` (Task 1); `ImageRepository.get_status(image_id: str) -> str | None` (None = no such image); `self.repo.session`.
- Produces: schema `MemeSaves` = `{imageId: string, count: integer >= 0, lastSavedAt: string (date-time) or null}`, all three required; generated Python `Backend.app.types.generated.memesaves.Schema`. `ImageService.record_save(image_id: str) -> None` and `ImageService.get_saves(image_id: str) -> MemeSaves`; both raise `HTTPException(404, "Image not found")` for an unparsable or unknown id, and `record_save` inserts nothing in that case. Routes: `POST /api/images/meme/{image_id}/saves` -> 204, no body, `no_cache_headers`; `GET /api/images/meme/{image_id}/saves` -> `MemeSaves`, `no_cache_headers`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/integration/test_meme_saves_endpoint.py` (service level with the real repository, modelled on `test_popularity_endpoint.py`'s `make_service`): fresh image `get_saves` has `count == 0` and `lastSavedAt is None`; `record_save` twice then `get_saves` gives `count == 2`, `lastSavedAt` not None, `imageId` equal to the requested id; a save of an absorbed loser shows on the winner; `record_save("not-a-uuid")` and `get_saves("not-a-uuid")` raise `HTTPException` 404; `record_save(str(uuid.uuid4()))` raises 404 and a following count of `meme_saves` rows is unchanged; a flagged or non-active existing image can be recorded.
  - `Backend/tests/test_images_endpoints.py` (follow its existing mocked-service pattern): `POST .../meme/{id}/saves` answers 204 with an empty body and calls `record_save` once; `GET` answers 200 with the three camelCase keys; the routes resolve (not shadowed by `/{image_id}`).
  - `Backend/tests/test_image_service.py`: `record_save` with a mocked repository whose `get_status` returns None raises 404 before touching the saves repository.

- [ ] **Step 2: Run to verify they fail.** Run each root separately: `pytest tests/integration/test_meme_saves_endpoint.py` (with DATABASE_URL) and, from `Backend/`, `pytest tests/test_images_endpoints.py tests/test_image_service.py`.

- [ ] **Step 3: Add the schema and list it in `all.schema.json`, then run the three generators** exactly as `documents/generation.md`, `Frontend/generate-types.sh` and `AndroidClient/scripts/generate_dtos.py` say. Expected: new `memesaves.py`, `MemeSaves` in `all.d.ts` and a `MemeSaves` data class in `Models.kt`, and no unrelated diff (check `git diff --stat`). `lastSavedAt` uses the existing nullable form `{"type": ["string", "null"], "format": "date-time"}`.

- [ ] **Step 4: Implement `ImageService.record_save` and `get_saves`.** Parse the id with `uuid.UUID` (a `ValueError` is a 404, as in `get_popularity`), check `self.repo.get_status(canonical_id)` is not None, then call `MemeSaveRepository(self.repo.session)`. `get_saves` builds `MemeSaves(imageId=canonical_id, count=..., lastSavedAt=...)`.

- [ ] **Step 5: Add the two routes** in `images.py` with `response_model=MemeSaves` on the GET, `status_code=204` on the POST (return `Response(status_code=204)` with the no-cache headers set), importing `Backend.app.types.generated.memesaves.Schema as MemeSaves`.

- [ ] **Step 6: Document in `backend_api.md`:** both routes, the response, the counting rule (own and folded-in copies through open edges), that the POST has no idempotency (every call counts, so clients must not retry) and the 404 cases.

- [ ] **Step 7: Run** the three test files above plus the full `Backend/tests` root and the full `tests/integration` root (each separately); then `tsc -b` from `Frontend/memes-frontend/` to confirm the regenerated types compile. Expected: all green.

- [ ] **Step 8: Commit** `feat(popularity): S2 saves endpoints and schema` (include the regenerated files).

---

### Task 3: Web save count block

**Files:**
- Create: `Frontend/memes-frontend/src/components/SaveCountBlock.tsx`
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts` (after `setMemePopularity`), `src/api/http/HttpMemesApi.ts` (after `setMemePopularity`), `src/test/mockApi.ts`, `src/components/MemeDetails.tsx:303`
- Test: `src/components/SaveCountBlock.test.tsx`, `src/components/MemeDetails.test.tsx`, `src/api/http/HttpMemesApi.saves.test.ts`

**Interfaces:**
- Consumes: `MemeSaves` from `types/generated/all` (Task 2); `useFetchById(id, fetcher, onResult, onError)` from `src/utils/useFetchById` exactly as `PopularityBlock` uses it.
- Produces: `MemesApi.getMemeSaves(id: string): Promise<MemeSaves>`; `<SaveCountBlock imageId memesApi />`.

- [ ] **Step 1: Write the failing tests.**
  - `HttpMemesApi.saves.test.ts` (copy the shape of the existing trending API test): `getMemeSaves("x")` fetches `{baseUrl}/api/images/meme/x/saves` and returns the JSON; a non-ok response throws.
  - `SaveCountBlock.test.tsx`: renders `Saves` and the count in `data-testid="save-count-value"`; with `lastSavedAt` shows a `last saved` line, with `null` shows none; renders nothing while the promise is pending and nothing (no alert) when it rejects.
  - `MemeDetails.test.tsx` (new `describe('saves', ...)`): the block shows for the meme (`getMemeSaves` mocked to count 3 gives `save-count-value` "3"); after rerendering with meme B (second call never resolving) the old count is gone and `getMemeSaves` was called with `meme-B`.

- [ ] **Step 2: Run to verify they fail.** Run: `pnpm exec vitest run src/components/SaveCountBlock.test.tsx src/api/http/HttpMemesApi.saves.test.ts` from `Frontend/memes-frontend/`.

- [ ] **Step 3: Add `getMemeSaves`** to the `MemesApi` interface, `HttpMemesApi` (same fetch and headers as `getMemePopularity`) and the mock in `src/test/mockApi.ts` (default resolves `{ imageId: 'x', count: 0, lastSavedAt: null }`).

- [ ] **Step 4: Implement `SaveCountBlock`** modelled on `PopularityBlock`: holds the response in state, returns null until it arrives and on any read error, shows `Saves` + `save-count-value`, and a muted `last saved <locale date>` line only when `lastSavedAt` is set. Mount it in `MemeDetails.tsx` directly under the `PopularityBlock` with `key={meme.id}`.

- [ ] **Step 5: Run** `tsc -b`, `eslint src/ --max-warnings 0` and `vitest run` (whole suite). Expected: clean.

- [ ] **Step 6: Commit** `feat(popularity): S2 save count on the meme details page`.

---

### Task 4: Android capture, docs and wrap-up

**Files:**
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/data/api/MemeApiService.kt` (after `downloadImage`), `.../data/repository/MemeRepository.kt` (after `downloadImage`), `.../ui/detail/MemeDetailViewModel.kt:141`
- Test: `AndroidClient/app/src/test/java/com/memebrowser/app/ui/detail/MemeDetailViewModelTest.kt`
- Modify: `docs/superpowers/specs/2026-10-26-meme-popularity/README.md` (S2 row done), the S2 spec (status `done`, `Plan:` link), `CLAUDE.md` only if it needs a line naming `meme_saves`

**Interfaces:**
- Consumes: `POST api/images/meme/{id}/saves` (Task 2).
- Produces: `MemeApiService.recordSave(@Path("id") id: String): Response<Unit>` (`@POST("api/images/meme/{id}/saves")`); `MemeRepository.recordSave(id: String): Result<Unit>` (never throws, logs and returns a failure); in `MemeDetailViewModel.saveToGallery`, after `saveImageToGallery(...)` returns and `saveSuccess = true` is set, one `repo.recordSave(meme.id)` call. The constructor does not change.

- [ ] **Step 1: Write the failing tests** in `MemeDetailViewModelTest.kt`. `saveToGallery` needs a stubbed gallery write: `mockkStatic("com.memebrowser.app.util.MediaStoreHelperKt")` and `coEvery { saveImageToGallery(any(), any(), any(), any(), any()) } just Runs` (unmock in `@After`), a `mockk<Context>()`, `repo.downloadImage("meme-1")` returning `Result.success("x".toResponseBody("image/jpeg".toMediaType()))`; if `android.util.Log` is not stubbed in unit tests, `mockkStatic(Log::class)` with `every { Log.d(any(), any()) } returns 0` and the same for `e`. Tests:
  - `save reports the save once after the gallery write`: `coVerifyOrder { saveImageToGallery(...); repo.recordSave("meme-1") }` and `saveSuccess` true.
  - `a failing report does not fail the save`: `repo.recordSave` returns `Result.failure(...)`; state has `saveSuccess` true and `error` null.
  - `no report when the gallery write fails`: the static stub throws; `coVerify(exactly = 0) { repo.recordSave(any()) }`, `error` starts with `Save failed`.
  - `no report when the download fails`: `downloadImage` fails; no `recordSave` call.

- [ ] **Step 2: Run to verify they fail** (compile error: `recordSave`). Run: `.\gradlew :app:testDebugUnitTest --no-daemon --tests "*MemeDetailViewModelTest"` from `AndroidClient/` with `JAVA_HOME` set to `C:\Program Files\Android\Android Studio\jbr`.

- [ ] **Step 3: Add `recordSave`** to the API service and the repository (`runCatching { api.recordSave(id) }`, map a non-successful `Response` to a failure, log failures with `Log.e` like `downloadImage` does), then the single call in `saveToGallery` placed after the success state update and wrapped so that nothing it does can reach the `catch` that prints `Save failed` (the repository already returns a `Result`; ignore it beyond logging).

- [ ] **Step 4: Run the whole Android unit suite** (`.\gradlew :app:testDebugUnitTest --no-daemon`). Expected: all pass. Search `src/test` and `src/androidTest` for fakes of `MemeRepository` or `MemeApiService` that need the new method.

- [ ] **Step 5: Docs.** Epic README S2 row `done: 2026-10-30-s2-save-count-design.md`; spec status line `done` plus a `Plan:` link to this file; add the implementer's measured facts (none needed) only if a deviation occurred, under an "Addendum" in the spec.

- [ ] **Step 6: Final checks, once:** `tests/docs`, `Backend/tests`, `tests/integration` (separately, DATABASE_URL form), `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`, the Android unit suite.

- [ ] **Step 7: Commit** `feat(popularity): S2 Android save report and docs`.
