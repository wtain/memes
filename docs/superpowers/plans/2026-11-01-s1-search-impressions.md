# S1 Search Impressions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The server records every image it returns from the three search surfaces (best effort, switchable), counts them per meme across its duplicate tree, and the web meme details page shows "Shown N times (M in the last D days)".

**Architecture:** An append-only table `meme_impressions` (no foreign keys). A small recorder (`Backend/app/services/impression_recorder.py`) writes one bulk insert inside a savepoint and swallows any failure; `ImageService.search`, `ImageService.get_similar` and `MemeFromTextService.search` call it after building a page. `GET /api/images/meme/{id}/impressions` counts rows over the image's duplicate tree (the walk the saves repository already does). The web adds an `ImpressionBlock` beside `SaveCountBlock`.

**Tech Stack:** PostgreSQL + Alembic, SQLAlchemy async, FastAPI, JSON schemas with three generators, React + vitest.

**Spec:** `docs/superpowers/specs/2026-10-26-meme-popularity/2026-11-01-s1-search-impressions-design.md` (binding; read it first). The S2 plan `docs/superpowers/plans/2026-10-30-s2-save-count.md` is the template for tasks 1, 3 and 4.

## Global Constraints

- Work only in the worktree. Never run a migration, restart a backend or touch a live database (`it`, `general`, `metal`); the owner does that. Subagents never receive the main `DATABASE_URL`.
- Never bind ports 8081-8083, 5173-5175 or the 5432/5434/5436 servers; the test database is `ocrdb_test` on `ocr-db`, port 5432. Other sessions share it: before an integration run check no other `pytest tests/integration` process is running, or use a private scratch database (`ocrdb_test_s1` with `vector` and `pg_trgm`, dropped afterwards, never `ocrdb`).
- Python: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe`. Run each pytest root separately (`Backend/tests`, `tests/integration`, `tests/docs`, `batch/tests`, `tests/rules`). Integration runs need `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the command line. Async integration tests carry `@pytest.mark.asyncio(loop_scope="session")` and use `db_session`.
- Repositories never call `session.commit()` (`get_async_db` does). ORM models only in `Storage/models.py`.
- `backend_api.md` is updated in the same change as the routes. Generated files (`Backend/app/types/generated/`, `Frontend/memes-frontend/src/types/generated/all.d.ts`, `AndroidClient/.../Models.kt`) come from the generators only (`documents/generation.md`, `Frontend/generate-types.sh`, `AndroidClient/scripts/generate_dtos.py`); a new schema is also listed by hand in `shared/schemas/all.schema.json`.
- Frontend before commit: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`. Android before commit (only if `Models.kt` changes): `.\gradlew :app:testDebugUnitTest --no-daemon` with `JAVA_HOME` set to `C:\Program Files\Android\Android Studio\jbr`.
- The migration's `down_revision` is `d5b8e3a07c41` (the current head). Use the Edit tool for repo text, never PowerShell Get/Set-Content round trips (BOM, double-encoding).
- The user's message text never reaches a log line or an error: the recorder logs only the surface and the exception type.
- Commits end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

Inputs and conditions the spec implies and a person would hit; each has a test in the owning task.

1. A failing impression write (missing table before the migration, any database error) never fails or alters a search response and never poisons the request's transaction: the history row and later queries in the same session still work (Task 2).
2. `similar` never counts its source image, an empty page writes nothing, and a "show more" page adds its own rows (Task 2).
3. With `query_log.enabled` off, or on a "show more" page that has no history row, impressions are still recorded with a null `search_history_id` (Task 2).
4. A malformed or unknown id on the read endpoint is a 404, never a 500; a meme with no impressions reads `count 0`, `countWindow 0`, `lastShownAt null` and the page still renders (Tasks 3 and 4).
5. A folded-in copy's impressions show on the survivor and return to the copy when the edge is reversed; a deleted image's rows still count; a row just outside the window is not in `countWindow` but is in `count` (Task 1).

---

### Task 1: Table, migration, settings and repository

**Files:**
- Modify: `Storage/models.py` (add `MemeImpression` after `MemeSave`)
- Create: `Storage/alembic/versions/a2f6d8c3b715_add_meme_impressions.py`
- Create: `repository/meme_impressions.py`
- Modify: `environments/settings.yaml` (new top-level `popularity` group)
- Modify: `repository/cut_copier.py:28` (add `"meme_impressions"` to `NOT_COPIED_TABLES`)
- Modify: `docs/image-derived-data.md` (row after the `meme_saves` row)
- Test: `tests/integration/test_meme_impressions_repository.py`, `tests/integration/test_meme_impressions_migration.py`, `tests/integration/test_meme_impressions_settings.py`

**Interfaces:**
- Consumes: `OPEN_EDGE_SQL` from `repository/popularity.py`; test helper `make_image` from `tests/integration/test_popularity_repository.py`; `PopularityRepository.absorb(loser_id, winner_id, source)` and `reverse_open_edge(loser_id, reason)`; the `db_session` fixture; `config.settings.settings`.
- Produces:
  - `Storage.models.MemeImpression` (columns in the spec's N2; `surface` has `CheckConstraint("surface IN ('search','similar','reply')", name="ck_meme_impressions_surface")`).
  - `repository.meme_impressions`: `SURFACES = ("search", "similar", "reply")`; `impressions_enabled() -> bool`; `window_days() -> int` (raises `ValueError` for a missing key, a non-bool `enabled`, or a non-int / bool / `< 1` window); `validate_impression_settings() -> None` (calls both); `class MemeImpressionRepository(session)` with `async record_many(surface: str, image_ids: list[uuid.UUID], search_history_id: uuid.UUID | None = None) -> None` (rank is the 1-based list position; one bulk insert and flush; empty list is a no-op; never commits) and `async summary(image_id: uuid.UUID, window_days: int) -> tuple[int, int, datetime | None]` (all-time count, count with `shown_at >= now() - window_days days`, newest `shown_at`; `(0, 0, None)` when none).

- [ ] **Step 1: Write the failing repository tests** in `test_meme_impressions_repository.py` (`@pytest.mark.asyncio(loop_scope="session")`, `db_session`, `make_image`):
  - `test_summary_of_unshown_image_is_zero`: `summary(img.id, 30) == (0, 0, None)`.
  - `test_record_many_assigns_ranks_in_order`: `record_many("search", [a.id, b.id, c.id])`; select `image_id, rank, surface, search_history_id` ordered by rank gives ranks 1, 2, 3 in that image order, surface `search`, null search id.
  - `test_search_history_id_is_stored`: pass a `uuid.uuid4()` that exists in no table; it is stored as given.
  - `test_empty_list_is_a_noop`: `record_many("search", [])` leaves the table row count unchanged.
  - `test_unknown_surface_is_rejected`: `record_many("trending", [a.id])` raises `ValueError` before any SQL (check `SURFACES`); a raw insert with surface `trending` violates the CHECK constraint (`IntegrityError`, wrap in `db_session.begin_nested()` so the test session survives).
  - `test_window_boundary`: insert two rows for one image with `shown_at` set explicitly, one 29 days 23 hours old and one 31 days old (raw `insert(MemeImpression).values(..., shown_at=...)`); `summary(id, 30)` gives `(2, 1, <the newer shown_at>)`.
  - `test_loser_impressions_count_for_winner`: absorb `l` into `w` via `PopularityRepository(db_session).absorb(l.id, w.id, "cluster_review")`; one row each; `summary(w.id, 30)[0] == 2`, `summary(l.id, 30)[0] == 1`.
  - `test_chain_credits_final_root`: absorb c into b, b into a, one row for c; `summary(a.id, 30)[0] == 1`.
  - `test_reversed_edge_hands_impressions_back`: after an absorb, `reverse_open_edge(l.id, "unflag")`; `summary(w.id, 30)[0]` excludes the loser's row and `summary(l.id, 30)[0]` includes it.
  - `test_deleted_image_rows_still_count`: record for an image, delete the `Image` row, `summary(id, 30)[0] == 1`.

- [ ] **Step 2: Run to verify they fail** (`ModuleNotFoundError: repository.meme_impressions`).
  Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_meme_impressions_repository.py -v`

- [ ] **Step 3: Add `MemeImpression`** to `Storage/models.py` after `MemeSave`: `__tablename__ = "meme_impressions"`; `id = Column(BigInteger, primary_key=True, autoincrement=True)`; `image_id` UUID not null, **no foreign key**; `shown_at` timezone-aware `DateTime`, not null, `server_default=func.now()`; `rank = Column(SmallInteger, nullable=False)`; `surface = Column(String(16), nullable=False)`; `search_history_id = Column(UUID(as_uuid=True), nullable=True)`, no foreign key. `__table_args__`: `Index("ix_meme_impressions_image_id_shown_at", "image_id", "shown_at")`, `Index("ix_meme_impressions_search_history_id", "search_history_id")` and the CHECK constraint named above. Docstring: append-only, why no foreign keys (same reason as `MemeSave` and `PopularityAbsorption`, and a history clean-up must not erase counts). Import `BigInteger`, `SmallInteger`, `CheckConstraint` if the file lacks them.

- [ ] **Step 4: Write the migration** `a2f6d8c3b715_add_meme_impressions.py` (revision `a2f6d8c3b715`, down_revision `d5b8e3a07c41`; header comment naming epic meme-popularity S1): `upgrade` creates the table, the two indexes and the CHECK exactly as the model declares them (`shown_at` server default `sa.text("now()")`); `downgrade` drops the indexes then the table. Copy the typing and header style of `d5b8e3a07c41_add_meme_saves.py`.

- [ ] **Step 5: Add the settings** to `environments/settings.yaml` as a new top-level group, with a comment pointing at the spec:
  ```yaml
  # Search impressions (epic meme-popularity, S1; docs/superpowers/specs/2026-10-26-meme-popularity/2026-11-01-s1-search-impressions-design.md).
  # A missing key is a startup error. enabled false stops recording; window_days is the span of countWindow.
  popularity:
    impressions: {enabled: true, window_days: 30}
  ```
  Write `test_meme_impressions_settings.py` (plain sync tests, `monkeypatch` on the `settings` object's `get`, or on `settings.set` — follow how `Backend/tests/test_search_settings_startup.py` fakes settings): the shipped values load (`impressions_enabled() is True`, `window_days() == 30`); a missing `window_days`, `window_days` of 0, -1, `True`, `"30"` and `1.5` each raise `ValueError`; `enabled` of `"yes"` raises; `validate_impression_settings()` raises for any of those. Run it first and watch it fail on the import.

- [ ] **Step 6: Implement `repository/meme_impressions.py`.** Settings readers use `settings.get("popularity.impressions.enabled")` and `...window_days` and the validation above; no caching, so tests can monkeypatch. `record_many` builds `[{"image_id": i, "surface": surface, "rank": n, "search_history_id": search_history_id} for n, i in enumerate(image_ids, 1)]` and runs one `insert(MemeImpression)` with that list, then `flush()`. `summary` is one recursive statement shaped like `_SUMMARY_SQL` in `repository/meme_saves.py` (`tree(id)` seeded with the root, `UNION` through `popularity_absorptions` with `{OPEN_EDGE_SQL} AND a.loser_image_id IS NOT NULL`), selecting `count(*), count(*) FILTER (WHERE s.shown_at >= now() - make_interval(days => :days)), max(s.shown_at)` from `meme_impressions s JOIN tree t ON s.image_id = t.id`. Import `OPEN_EDGE_SQL` rather than copying it.

- [ ] **Step 7: Write the migration test** `test_meme_impressions_migration.py` following `test_meme_saves_migration.py`: run `upgrade` and `downgrade` on a scratch connection inside a transaction; assert the created table's columns (names, types, nullability), primary key, both indexes, the CHECK constraint, and the **absence of any foreign key** equal what `Base.metadata.tables["meme_impressions"]` declares; downgrade removes the table.

- [ ] **Step 8: Cut-copier and docs.** Add `"meme_impressions"` to `NOT_COPIED_TABLES`. New `docs/image-derived-data.md` row, same wording style as `meme_saves`: none (no foreign keys; plain ids, so history survives deletion); not copied; a cut starts with no impressions.

- [ ] **Step 9: Run** the three new test files (DATABASE_URL form above) and `tests/docs/`. Expected: all pass.

- [ ] **Step 10: Commit** `feat(popularity): S1 meme_impressions table, migration, settings and repository` (only the files listed above).

---

### Task 2: Recorder and wiring into the three searches

**Files:**
- Create: `Backend/app/services/impression_recorder.py`
- Modify: `Backend/app/repositories/history_repository.py` (`add` returns the new row's id)
- Modify: `Backend/app/services/history_recorder.py` (`record_history` returns that id or `None` on failure)
- Modify: `Backend/app/services/image_service.py` (`search`, `get_similar`, `_record_history`)
- Modify: `Backend/app/services/meme_from_text_service.py` and `Backend/app/api/meme_from_text.py` (`get_meme_from_text_service`)
- Modify: `Backend/app/main.py:90` (call `validate_impression_settings()` next to `validate_search_settings()`)
- Test: `tests/integration/test_impression_recorder.py`, `tests/integration/test_impressions_wiring.py`, `Backend/tests/test_search_settings_startup.py` (extend), `Backend/tests/test_image_service.py` and the meme-from-text service tests (extend only if existing tests break)

**Interfaces:**
- Consumes: `MemeImpressionRepository`, `impressions_enabled`, `validate_impression_settings` (Task 1).
- Produces:
  - `async def record_impressions(session: AsyncSession, surface: str, image_ids: list[uuid.UUID], search_history_id: uuid.UUID | None = None) -> None` in `impression_recorder.py`: returns at once when `image_ids` is empty or `impressions_enabled()` is false; otherwise `async with session.begin_nested()` around `MemeImpressionRepository(session).record_many(...)`; any exception is caught and logged as `logger.warning("impression write failed: surface=%s error=%s", surface, type(error).__name__)`; never raises.
  - `HistoryRepository.add(...) -> uuid.UUID` (builds `SearchHistory(id=uuid.uuid4(), ...)` and returns that id); `record_history(**fields) -> uuid.UUID | None`.
  - `ImageService._record_history(...) -> uuid.UUID | None`.
  - `MemeFromTextService.__init__(..., history_recorder=None, max_chars=None, impression_recorder=None)`; `impression_recorder` is an async callable `(surface, image_ids, search_history_id)`; `get_meme_from_text_service` passes `functools.partial(record_impressions, db)`.
  - Rank is the 1-based position within the returned page on all three surfaces: neither the search cursor nor the retrieval cursor carries an offset (ruling for spec N3; record it in the spec addendum in Task 4).

- [ ] **Step 1: Write the failing recorder tests** in `test_impression_recorder.py` (`db_session`, `make_image`):
  - `test_records_rows_with_surface_and_search_id`: `record_impressions(db_session, "similar", [a.id, b.id], sid)` gives two rows with ranks 1, 2, surface `similar`, search id `sid`.
  - `test_empty_list_writes_nothing`.
  - `test_switch_off_writes_nothing`: `monkeypatch.setattr("Backend.app.services.impression_recorder.impressions_enabled", lambda: False)`.
  - `test_failure_is_swallowed_and_session_stays_usable`: monkeypatch `MemeImpressionRepository.record_many` to first execute a bad statement on the session (`await session.execute(text("SELECT * FROM table_that_does_not_exist"))`) and raise; `record_impressions` returns normally, `caplog` has the warning with the surface and `ProgrammingError`/`DBAPIError`-style type name and **no** other text from the exception, and a following `await db_session.execute(select(1))` and a following successful `record_impressions` both work (the savepoint rolled back).
  - `test_log_line_has_no_exception_message`: raise `RuntimeError("secret message text")`; the caplog text does not contain `secret message text`.

- [ ] **Step 2: Run to verify they fail** (`ModuleNotFoundError`). Same command form as Task 1, file `tests/integration/test_impression_recorder.py`.

- [ ] **Step 3: Implement `impression_recorder.py`** as specified in Produces (module logger `logging.getLogger(__name__)`).

- [ ] **Step 4: Write the failing wiring tests** in `test_impressions_wiring.py` (service level with real repositories on `db_session`, modelled on `test_meme_saves_endpoint.py`'s `make_service`; patch the history recorder where a test needs the id):
  - Search (`ImageService.search`): with three active images matching a text query, a call writes three `search` rows with ranks 1, 2, 3 in result order; the `search_history_id` of the rows equals the id the patched `record_history` returned; a second call with the returned cursor (limit 2 so a second page exists) adds rows for that page only, ranks restarting at 1.
  - `test_empty_search_writes_nothing`: a query with no match.
  - `test_similar_skips_the_source_image`: `get_similar` for image A with neighbours B and C writes `similar` rows for B and C only, null search id.
  - `test_failed_write_does_not_change_the_response`: monkeypatch `MemeImpressionRepository.record_many` to raise; the search result equals the result of a run with the switch off, and the history recorder was still called.
  - `test_history_recorder_failure_still_records_impressions`: patched `record_history` returns `None`; impressions are written with a null search id.
  - Meme-from-text (`MemeFromTextService` with the stub pipeline used by the existing `tests/integration` meme-from-text service tests): a fresh message writes `reply` rows for the returned images with the id the history recorder returned; a call with a cursor (no history row) writes `reply` rows with a null search id; `history_recorder=None` (query log off) still writes rows with a null search id; the failure log line (caplog) contains no part of the message text.

- [ ] **Step 5: Run to verify they fail.**

- [ ] **Step 6: Implement.**
  - `HistoryRepository.add`: create the record with an explicit `id=uuid.uuid4()` and `return record.id` after `session.add(record)`.
  - `record_history`: `return` the id from inside the `async with session.begin()` block (assign to a local, return after the block), `return None` in the `except`.
  - `ImageService._record_history`: `return await record_history(...)`. In `search`: `search_history_id = await self._record_history(...)`, then `await record_impressions(self.repo.session, "search", [uuid.UUID(m.id) for m in result.items or []], search_history_id)` before `return result`. In `get_similar`: after `items` is built, `await record_impressions(self.repo.session, "similar", [uuid.UUID(m.id) for m in items])`. (The repository query already excludes the source image; the test pins it.)
  - `MemeFromTextService.search`: capture `search_history_id = await self.history_recorder(...)` (initialised to `None`; the existing `try/except` stays), take `image_ids = [uuid.UUID(item["imageId"]) for item in items]` **before** `_hydrate` pops `imageId`, and after the history step call `await self.impression_recorder("reply", image_ids, search_history_id)` when `impression_recorder` is not None. A failure of the history call still leaves `search_history_id = None`.
  - `get_meme_from_text_service`: pass `impression_recorder=functools.partial(record_impressions, db)`.
  - `main.py`: call `validate_impression_settings()` right after `validate_search_settings()`, also uncaught, with the same style of comment.

- [ ] **Step 7: Extend `Backend/tests/test_search_settings_startup.py`** with two cases mirroring its existing ones: the lifespan calls `validate_impression_settings`, and a `ValueError` from it stops startup.

- [ ] **Step 8: Run** the two new integration files, then the full `tests/integration` root and the full `Backend/tests` root (each separately). Existing `Backend/tests` that mock `ImageService`'s repository get a `Mock` session: `record_impressions` must swallow that (the `begin_nested` call fails and is caught). If an existing test asserts on `_record_history`'s return or the number of log records, adjust it minimally and ledger the change. Expected: all green.

- [ ] **Step 9: Commit** `feat(popularity): S1 record search impressions` (include `history_repository.py`, `history_recorder.py`, `main.py`).

---

### Task 3: Read endpoint, schema and generated types

**Files:**
- Create: `shared/schemas/memeimpressions.schema.json`
- Modify: `shared/schemas/all.schema.json` (add `"MemeImpressions": { "$ref": "memeimpressions.schema.json" }` after `MemeSaves`)
- Regenerate: `Backend/app/types/generated/`, `Frontend/memes-frontend/src/types/generated/all.d.ts`, `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt`
- Modify: `Backend/app/services/image_service.py` (after `get_saves`), `Backend/app/api/images.py` (after the saves routes, before `/{image_id}`), `backend_api.md`
- Test: `tests/integration/test_meme_impressions_endpoint.py`, `Backend/tests/test_images_endpoints.py`, `Backend/tests/test_image_service.py`

**Interfaces:**
- Consumes: `MemeImpressionRepository.summary`, `window_days()` (Task 1); `ImageService._existing_image_uuid`.
- Produces: schema `MemeImpressions` = `{imageId: string, count: integer >= 0, countWindow: integer >= 0, windowDays: integer >= 1, lastShownAt: string (date-time) or null}`, all five required (nullable form as in `memesaves.schema.json`); generated Python `Backend.app.types.generated.memeimpressions.Schema`. `ImageService.get_impressions(image_id: str) -> MemeImpressions`, raising `HTTPException(404, "Image not found")` for an unparsable or unknown id. Route `GET /api/images/meme/{image_id}/impressions` -> `MemeImpressions`, `no_cache_headers`. There is no write route.

- [ ] **Step 1: Write the failing tests.**
  - `test_meme_impressions_endpoint.py` (service level with the real repository, like the saves endpoint test): a fresh image gives `count 0`, `countWindow 0`, `lastShownAt None`, `windowDays == window_days()`, `imageId` equal to the request; two `record_many` calls then `get_impressions` give `count 2`; a folded-in copy's row shows on the winner; `get_impressions("not-a-uuid")` and `get_impressions(str(uuid.uuid4()))` raise `HTTPException` 404; a non-active existing image can be read.
  - `Backend/tests/test_images_endpoints.py` (mocked-service pattern): `GET .../meme/{id}/impressions` answers 200 with the five camelCase keys and `Cache-Control: no-store`-style no-cache headers as the saves test asserts; the route is not shadowed by `/{image_id}`; `POST` to it answers 405.
  - `Backend/tests/test_image_service.py`: `get_impressions` with a repository whose `get_status` returns None raises 404 before touching the impressions repository.

- [ ] **Step 2: Run to verify they fail** (integration file with DATABASE_URL; from `Backend/`, `pytest tests/test_images_endpoints.py tests/test_image_service.py`).

- [ ] **Step 3: Add the schema, list it in `all.schema.json`, run the three generators** exactly as `documents/generation.md`, `Frontend/generate-types.sh` and `AndroidClient/scripts/generate_dtos.py` say. Expected: new `memeimpressions.py`, `MemeImpressions` in `all.d.ts` and a `MemeImpressions` data class in `Models.kt`, and no unrelated diff (`git diff --stat`).

- [ ] **Step 4: Implement `ImageService.get_impressions`**: `image_uuid = await self._existing_image_uuid(image_id)`, `days = window_days()`, `count, in_window, last = await MemeImpressionRepository(self.repo.session).summary(image_uuid, days)`, return `MemeImpressions(imageId=str(image_uuid), count=count, countWindow=in_window, windowDays=days, lastShownAt=last)`.

- [ ] **Step 5: Add the route** in `images.py` next to the saves routes (so before `/{image_id}`), GET only, `response_model=MemeImpressions`, headers via `response.headers.update(no_cache_headers())`.

- [ ] **Step 6: Document in `backend_api.md`:** the new route and response; the counting rule (own and folded-in copies through open edges; the window); that only the server records impressions (no client write route); a note under each of `GET /api/images`, `GET /api/images/{image_id}/similar` and `POST /api/meme-from-text/search` that returned images are recorded as impressions (best effort, `popularity.impressions.enabled`, rank is the position within the page, `similar` does not count the source image); and that the table must be migrated before the read route works (a search still works without it).

- [ ] **Step 7: Run** the three test files, then the full `Backend/tests` and `tests/integration` roots (each separately), then `tsc -b` from `Frontend/memes-frontend/` to confirm the regenerated types compile. Expected: green.

- [ ] **Step 8: Commit** `feat(popularity): S1 impressions endpoint and schema` (include the regenerated files).

---

### Task 4: Web impression block, docs and wrap-up

**Files:**
- Create: `Frontend/memes-frontend/src/components/ImpressionBlock.tsx`
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts` (after `getMemeSaves`), `src/test/mockApi.ts`, `src/components/MemeDetails.tsx:303`
- Test: `src/components/ImpressionBlock.test.tsx`, `src/components/MemeDetails.test.tsx`, `src/api/http/HttpMemesApi.impressions.test.ts`
- Modify: `docs/superpowers/specs/2026-10-26-meme-popularity/README.md` (S1 row), the S1 spec (status, `Plan:` link, addendum), `CLAUDE.md` (one sentence)

**Interfaces:**
- Consumes: `MemeImpressions` from `types/generated/all` (Task 3); `useFetchById` exactly as `SaveCountBlock` uses it.
- Produces: `MemesApi.getMemeImpressions(id: string): Promise<MemeImpressions>`; `<ImpressionBlock imageId memesApi />`.

- [ ] **Step 1: Write the failing tests.**
  - `HttpMemesApi.impressions.test.ts` (copy `HttpMemesApi.saves.test.ts`): `getMemeImpressions("x")` fetches `{baseUrl}/api/images/meme/x/impressions` and returns the JSON; a non-ok response throws.
  - `ImpressionBlock.test.tsx`: with `{count: 12, countWindow: 5, windowDays: 30}` renders `Shown` and `data-testid="impression-count"` "12" and `data-testid="impression-window"` containing "5 in the last 30 days"; with `lastShownAt` shows a `last shown` line, with `null` none; `count: 0` still renders "0"; renders nothing while pending and nothing (no alert) when the promise rejects.
  - `MemeDetails.test.tsx` (new `describe('impressions', ...)`): the block shows for the meme; after rerendering with meme B (second call never resolving) the old count is gone and `getMemeImpressions` was called with `meme-B`.

- [ ] **Step 2: Run to verify they fail.** `pnpm exec vitest run src/components/ImpressionBlock.test.tsx src/api/http/HttpMemesApi.impressions.test.ts` from `Frontend/memes-frontend/`.

- [ ] **Step 3: Add `getMemeImpressions`** to the `MemesApi` interface, `HttpMemesApi` (same fetch and headers as `getMemeSaves`) and `mockApi.ts` (default resolves `{ imageId: 'x', count: 0, countWindow: 0, windowDays: 30, lastShownAt: null }`).

- [ ] **Step 4: Implement `ImpressionBlock`** modelled on `SaveCountBlock`: state, `useFetchById`, null until loaded and on error; `Shown` + `impression-count`, a muted `(<countWindow> in the last <windowDays> days)` in `impression-window`, and `last shown <locale date>` in `impression-last-shown` only when set. Mount it in `MemeDetails.tsx` directly under `SaveCountBlock` with `key={meme.id}`.

- [ ] **Step 5: Run** `tsc -b`, `eslint src/ --max-warnings 0` and `vitest run` (whole suite). Expected: clean. If Task 3 changed `Models.kt`, run the Android unit suite once (`.\gradlew :app:testDebugUnitTest --no-daemon`, JAVA_HOME as above); Android gets no feature code (spec N12).

- [ ] **Step 6: Docs.** Epic README: S1 row becomes `done: 2026-11-01-s1-search-impressions-design.md`, and the epic's `Follow-ups:` entry for S1 says `(done)`. S1 spec: status `done`, `Plan:` link to this file, and an `## Addendum` with the rulings (rank is page-local on all three surfaces because neither cursor carries an offset; `search_history_id` is null for "show more" pages of meme-from-text and for `similar`, and whenever logging is off or failed) and any deviation. `CLAUDE.md`: one sentence after the `search.ranking.*` sentence in Configuration: "Search also records the images it returns in `meme_impressions` (`popularity.impressions.enabled` / `window_days`; best effort, see `docs/superpowers/specs/2026-10-26-meme-popularity/2026-11-01-s1-search-impressions-design.md`)."

- [ ] **Step 7: Final checks, once:** `tests/docs`, `Backend/tests`, `tests/integration`, `batch/tests`, `tests/rules` (each separately, DATABASE_URL form for integration), `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`, and the Android suite if `Models.kt` changed.

- [ ] **Step 8: Commit** `feat(popularity): S1 impressions on the meme details page and docs`.
