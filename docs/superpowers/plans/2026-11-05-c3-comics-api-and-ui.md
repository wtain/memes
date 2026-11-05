# C3 Comics Management API and UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin can create, name, search, fill, order and preview a draft comics from a new admin page and from the meme page; Build stays disabled (C4).

**Architecture:** Two additions to `repository/comics.py` (batched `addability`, `find_by_image`); a service and router over the C1 repository and the C2 preview; shared JSON schemas with generated types; a `record=false` switch on the existing image search; three frontend surfaces (list page, editor page, meme-page block) over new `MemesApi` methods. No migration, no new table, no file moves.

**Tech Stack:** FastAPI, async SQLAlchemy, Pillow (via `batch/utils/comics_compose.py`), React 19 + TypeScript + vitest, JSON-schema type generation.

**Spec:** `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-04-c3-comics-api-and-ui-design.md` (read it first; it holds the endpoint table, error mapping and UI behavior)

## Global Constraints

- Python 3.11 (`.venv311`). ORM models only in `Storage/models.py`; this plan adds none. Repositories never `commit()`.
- Router, then service, then repository. `backend_api.md` is updated in the same task that adds or changes an endpoint.
- Error body: `HTTPException(status, detail={"code": <ComicsError.code>, **detail})`. Status map: `not_found` 404; `name_taken`, `built`, `image_in_comics`, `image_not_addable` (any reason except `not_found`) 409; `image_not_addable` with reason `not_found` 404; `name_invalid`, `reorder_mismatch` 422; `not_buildable` 409 (unused until C4).
- Panel position and compose positions shown to people are 1-based; stored and C2 positions are 0-based.
- Preview: `maxWidth` 200 to 2400, default 1200; `Cache-Control: no-store`; semaphore of 2; composer runs in `asyncio.to_thread`.
- Frontend: generated types only (never hand-written API types); effects survive StrictMode double mount; `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run` must pass before every frontend commit.
- Test commands: backend `cd Backend && pytest tests/<file>`; repository `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/<file> -v` (never combine test roots in one `pytest` call); docs guard `pytest tests/docs/`.
- Commit messages end with the two attribution lines given in the session's system reminder.

## Review Focus

- Reorder with panel ids that no longer match the comics (added or removed meanwhile): clean 422 `reorder_mismatch`, nothing applied (Task 3).
- Corrupt, huge (near 16,000 px) or missing panel file in the preview: 422 `compose_failed` with the 1-based position, never a 500, semaphore released (Task 4).
- Names with Cyrillic, emoji, trailing dot, different case: the C1 reasons reach the user in words (Tasks 3 and 7).
- A search result flagged or put into a comics between the search and the click: the 409 refreshes the "+" state instead of failing silently (Task 8).
- `by-image` for an image that was a panel of a built comics or a released kept panel: `{comics: null}`, not an error (Tasks 1 and 3).

## File Structure

- Modify `repository/comics.py`: `Addability`, `addability`, `find_by_image`, shared addability checks.
- Create `shared/schemas/comics*.schema.json` (list in Task 2), register in `shared/schemas/all.schema.json`; regenerate the three type trees.
- Create `Backend/app/services/comics_service.py` (shaping, error mapping, preview), `Backend/app/api/comics.py`; modify `Backend/app/main.py`, `Backend/app/api/images.py`, `Backend/app/services/image_service.py`, `Dockerfile.backend`, `.dockerignore`, `backend_api.md`.
- Frontend: `src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/api/errors.ts`; create `src/pages/ComicsListPage.tsx`, `src/pages/ComicsEditorPage.tsx`, `src/comics/` (pure helpers, reducer), `src/components/comics/` (panel strip, preview, search column, picker modal, meme-page block); modify `src/app/router.tsx`, `src/app/AppLayout.tsx` (admin nav), `src/components/MemeDetails.tsx` or the curator panel (the block).

---

### Task 1: Repository additions

**Files:**
- Modify: `repository/comics.py`
- Test: `tests/integration/test_comics_addability.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Addability: image_id: uuid.UUID; addable: bool; reason: str | None; comics_id: uuid.UUID | None; comics_name: str | None`.
- Produces: `ComicsRepository.addability(self, image_ids: list[uuid.UUID]) -> dict[uuid.UUID, Addability]` (every requested id is a key; at most 5 queries whatever the count).
- Produces: `ComicsRepository.find_by_image(self, image_id: uuid.UUID) -> tuple[Comics, ComicsPanel] | None`.
- Consumes: C1 `_check_addable` rules; `ComicsPanel.image_id` unique index.

- [ ] **Step 1: Write failing tests** (integration, fixtures as in `tests/integration/test_comics_panels.py`): `test_addability_reasons_and_precedence` (one image per reason: missing id `not_found`; pending image `not_active`; flagged `flagged`; live recut cut as source and as cut `live_cut`; panel of a comics `in_comics` with that comics' id and name; a clean image addable with `reason None`; an image both flagged and in a comics reports `flagged`); `test_addability_runs_a_fixed_number_of_queries` (60 ids, count statements through an `before_cursor_execute` listener, assert <= 5); `test_find_by_image_returns_comics_and_panel_or_none` (panel image gives comics and its panel; a plain image, a released panel and a built comics' retired panel give None).
- [ ] **Step 2: Run to verify they fail** (`pytest tests/integration/test_comics_addability.py -v`): FAIL, no `addability`.
- [ ] **Step 3: Implement** both methods. Extract the status, flagged and live-cut checks of `_check_addable` into module-level helpers that take a list of ids (one `IN` query each) and have `_check_addable` call them with one id, so the single and batched paths cannot drift; precedence is not_found, not_active, flagged, live_cut, in_comics.
- [ ] **Step 4: Run** the new file, then `pytest tests/integration/test_comics_*.py -v` (C1 suites must stay green). Expected: PASS.
- [ ] **Step 5: Commit** `feat(comics): batched addability and find_by_image`.

### Task 2: Shared schemas and generated types

**Files:**
- Create: `shared/schemas/comicssummary.schema.json`, `comicspage`, `comicspanel`, `comicsdetail`, `comicscreaterequest`, `comicsaddpanelrequest`, `comicsreorderrequest`, `comicsaddability` (items array of `{imageId, addable, reason, comicsId, comicsName}`), `comicsbyimage` (all `*.schema.json`)
- Modify: `shared/schemas/all.schema.json`, `shared/schemas/memesearchrequest.schema.json` (add optional boolean `record`)
- Generated: `Frontend/memes-frontend/src/types/generated/all.d.ts`, `Backend/app/types/generated/`, AndroidClient DTOs

**Interfaces:**
- Produces (camelCase wire names): `ComicsSummary {id, name, state, panelCount, coverImageId|null, createdAt, builtAt|null}`; `ComicsPage {items: ComicsSummary[], hasMore}`; `ComicsPanel {id, position, imageId|null, sourceFilename}`; `ComicsDetail {id, name, state, builtImageId|null, builtAt|null, panels: ComicsPanel[]}`; `ComicsCreateRequest {name}`; `ComicsAddPanelRequest {imageId, move?: boolean}`; `ComicsReorderRequest {panelIds: string[]}`; `ComicsAddability {items: {imageId, addable, reason|null, comicsId|null, comicsName|null}[]}`; `ComicsByImage {comics: {id, name, state, position}|null}`. Follow the nullability convention of `recutproposaldetail.schema.json`.

- [ ] **Step 1: Write the schemas**, register each in `all.schema.json` like `RecutDecision`.
- [ ] **Step 2: Regenerate** with the three commands from `documents/generation.md` (frontend `npx json2ts`, backend `datamodel-codegen` from `Backend/`) and `python AndroidClient/scripts/generate_dtos.py`.
- [ ] **Step 3: Verify**: `git diff --stat` shows changes in all three trees; `tsc -b` passes; `cd Backend && pytest tests/test_main.py`.
- [ ] **Step 4: Commit** `feat(comics): shared schemas and generated types`.

### Task 3: Service, router and CRUD endpoints

**Files:**
- Create: `Backend/app/services/comics_service.py`, `Backend/app/api/comics.py`
- Modify: `Backend/app/main.py` (`include_router(..., prefix="/api")`), `backend_api.md`
- Test: `Backend/tests/test_comics_endpoints.py`

**Interfaces:**
- Consumes: Task 1 methods; Task 2 generated `Schema` classes; `ComicsRepository` (C1).
- Produces: `class ComicsService: def __init__(self, session: AsyncSession)`, with async `list(q, limit, offset) -> dict` (`{items, hasMore}`, fetches `limit+1`; cover = built image, else first panel's image, else None), `create(name)`, `detail(id)`, `rename(id, name)`, `delete(id)`, `add_panel(id, image_id, move)`, `remove_panel(id, panel_id)`, `reorder(id, panel_ids)`, `addability(image_ids)` (1 to 100 ids), `by_image(image_id)`; `def http_error(error: ComicsError) -> HTTPException` (the Global Constraints map).
- Produces endpoints (spec table): `GET /api/comics`, `POST /api/comics` (201), `GET|PATCH|DELETE /api/comics/{id}` (DELETE 204), `POST /api/comics/{id}/panels` (201, returns the detail), `DELETE /api/comics/{id}/panels/{panelId}` (204), `PUT /api/comics/{id}/panels/order` (returns the detail), `GET /api/comics/addability?imageIds=`, `GET /api/comics/by-image/{imageId}`. Static paths are registered before `/{id}`. `remove_panel` requires the panel to belong to `{id}` (else 404).

- [ ] **Step 1: Write failing tests** with a mocked service (pattern of `test_recut_endpoints.py`, dependency override of a `get_comics_service`): per endpoint the success status and body; one test per mapped error code asserting status and `detail.code` (incl. `image_in_comics` carrying `comicsId`/`comicsName`, `name_invalid` carrying `reason`); `limit` bounds 1..100 and `imageIds` over 100 or empty are 422; `test_reorder_with_stale_ids_is_422_and_applies_nothing` (service raises `reorder_mismatch`; assert the body); `test_by_image_for_retired_panel_is_null_comics`. Plus service tests (`Backend/tests/test_comics_service.py`) for the cover rule, `hasMore`, and `http_error` for every code.
- [ ] **Step 2: Run** (`cd Backend && pytest tests/test_comics_endpoints.py tests/test_comics_service.py`): FAIL.
- [ ] **Step 3: Implement** service and router; the service translates `ComicsError` through `http_error` only at the router boundary (`try/except ComicsError` helper like `_decide` in `recut.py`). `reorder_mismatch` is raised by the C1 repository; do not re-check ids in the service.
- [ ] **Step 4: Document** every endpoint in `backend_api.md` (request, response, error codes).
- [ ] **Step 5: Run** `cd Backend && pytest` (whole root) and `pytest tests/docs/`; start the app import check `python -c "import Backend.app.main"` with the env of `.env.it` loaded read-only (do not bind a port).
- [ ] **Step 6: Commit** `feat(comics): comics API`.

### Task 4: Preview endpoint and backend image

**Files:**
- Modify: `Backend/app/services/comics_service.py`, `Backend/app/api/comics.py`, `Dockerfile.backend`, `.dockerignore`, `tests/docker/test_backend_image_boots.py`, `backend_api.md`
- Test: `Backend/tests/test_comics_preview.py`

**Interfaces:**
- Consumes: `batch.utils.comics_compose.compose_preview(paths, max_width) -> bytes`, `ComposeError(reason, position, detail)`; `settings.get("BASE_PATH")`; `Image.filename`.
- Produces: `ComicsService.preview(comics_id, max_width) -> bytes` (draft only: built is 409 `built`; reads the panel images' filenames in order under the comics row without a write lock; paths are `BASE_PATH/filename`; compose in `asyncio.to_thread` under a module-level `asyncio.Semaphore(2)`); `GET /api/comics/{id}/preview?maxWidth=1200` returning `image/jpeg` with `Cache-Control: no-store`; `ComposeError` becomes 422 `{code: "compose_failed", reason, position}` with `position` 1-based or null.

- [ ] **Step 1: Write failing tests** with real small PNG/JPEG files in `tmp_path` and a fake base path: `test_preview_returns_jpeg_bytes_of_the_tiled_panels` (3 panels, decoded size equals the C2 layout at the requested width); `test_preview_of_an_empty_draft_is_422_no_panels`; `test_preview_with_a_corrupt_panel_names_its_one_based_position`; `test_preview_with_a_missing_file_is_unreadable_not_500`; `test_preview_of_a_built_comics_is_409`; `test_max_width_bounds_are_422` (199, 2401); `test_semaphore_is_released_after_a_failure` (two failing previews then a good one succeeds); `test_cache_control_is_no_store`.
- [ ] **Step 2: Run to verify FAIL.**
- [ ] **Step 3: Implement.** Add to `Dockerfile.backend` `COPY batch/utils/comics_compose.py ./batch/utils/comics_compose.py` (its imports `file_hash.py` and `__init__.py` are already copied) with a comment naming this service, and the matching `!batch/utils/comics_compose.py` line in `.dockerignore`. In `tests/docker/test_backend_image_boots.py` add a test that imports `batch.utils.comics_compose` inside the built image (pattern of the retrieval import test).
- [ ] **Step 4: Document** the endpoint in `backend_api.md`; run `cd Backend && pytest` and `pytest tests/docs/`; run `pytest tests/docker/` if a Docker daemon is available (it skips otherwise; say so in the report).
- [ ] **Step 5: Commit** `feat(comics): preview endpoint and backend image support`.

### Task 5: `record=false` on the image search

**Files:**
- Modify: `Backend/app/api/images.py` (`get_images`), `Backend/app/services/image_service.py` (`search`), `backend_api.md`
- Test: `Backend/tests/test_images_endpoints.py`, `Backend/tests/test_image_service.py`

**Interfaces:**
- Produces: `GET /api/images?...&record=false` (query param `record: bool = True`); `ImageService.search(..., record: bool = True)`. With `record=False` the service calls neither `record_impressions` nor the search-history write, and the response has no `searchId`.

- [ ] **Step 1: Write failing tests**: `test_search_with_record_false_records_nothing_and_has_no_search_id`; `test_search_default_still_records` (existing behavior unchanged); `test_endpoint_passes_record_through` (`record=false` reaches the service as `False`, absent as `True`).
- [ ] **Step 2: Run to verify FAIL**, **Step 3: Implement**, **Step 4: Run** `cd Backend && pytest` (whole root), **Step 5: Commit** `feat(search): optional record=false for admin tools`.

### Task 6: Frontend API layer

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/api/errors.ts`; any fake `MemesApi` used by existing tests (grep for `implements MemesApi` and test doubles)
- Test: `src/api/http/HttpMemesApi.comics.test.ts`

**Interfaces:**
- Produces on `MemesApi` (names fixed): `listComics(q?: string, offset?: number, limit?: number): Promise<ComicsPage>`, `createComics(name: string): Promise<ComicsDetail>`, `getComics(id): Promise<ComicsDetail>`, `renameComics(id, name): Promise<ComicsDetail>`, `deleteComics(id): Promise<void>`, `addComicsPanel(id, imageId, move?): Promise<ComicsDetail>`, `removeComicsPanel(id, panelId): Promise<void>`, `reorderComicsPanels(id, panelIds): Promise<ComicsDetail>`, `comicsAddability(imageIds: string[]): Promise<ComicsAddability>`, `comicsByImage(imageId): Promise<ComicsByImage>`, `comicsPreviewUrl(id, maxWidth, version): string`; `searchMemes` request gains `record?: boolean` sent as `record=false` when false.
- Produces: `class ComicsRequestError extends Error { status: number; code?: string; detail: Record<string, unknown> }` (parses the `detail` object of the error body; tolerates a string or missing detail).

- [ ] **Step 1: Write failing tests** with a mocked `fetch`: each method's URL, verb and body; `ComicsRequestError` for a 409 `name_taken`, a 409 `image_in_comics` (keeps `comicsId`), a 422 `name_invalid` (keeps `reason`), a non-JSON 500; `searchMemes({record:false})` sends `record=false`, omitted sends nothing.
- [ ] **Step 2: Run to verify FAIL**, **Step 3: Implement**, **Step 4:** `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`. **Step 5: Commit** `feat(comics): frontend API client`.

### Task 7: Comics list page

**Files:**
- Create: `src/pages/ComicsListPage.tsx`, `src/comics/comicsErrors.ts` (pure: `describeComicsError(error): string` mapping codes and `name_invalid` reasons to words: empty, too_long, forbidden_character, trailing_dot_or_space, reserved_name, name_taken), test files beside them
- Modify: `src/app/router.tsx` (`/admin/comics` and `/admin/comics/:id`), `src/app/AppLayout.tsx` (a "Comics" link next to the other admin links)

**Interfaces:**
- Consumes: Task 6 `MemesApi` methods and `ComicsRequestError`.
- Produces: `ComicsListPage({memesApi})`: debounced name search (300 ms), "New comics" inline form, rows (cover via `imageUrl` of `coverImageId`, name, state badge, panel count) linking to `/admin/comics/:id`, delete on drafts after a confirmation, "Show more" driven by `hasMore`; error text from `describeComicsError`.

- [ ] **Step 1: Write failing tests** (vitest + testing library, pattern of `RecutReviewPage.test.tsx`): renders rows from the API; typing in search calls `listComics` with `q` after the debounce; "Show more" requests the next offset and appends; creating shows the C1 reason in words for `name_invalid` of each reason and for `name_taken` (parametrized, including a Cyrillic name that reaches the API unchanged); delete asks for confirmation and is absent for built rows.
- [ ] **Step 2: Run to verify FAIL**, **Step 3: Implement**, **Step 4:** the three frontend checks. **Step 5: Commit** `feat(comics): comics list page`.

### Task 8: Comics editor page

**Files:**
- Create: `src/pages/ComicsEditorPage.tsx`, `src/comics/editorReducer.ts` (pure state: comics detail, preview version, search results, addability by image id), `src/components/comics/PanelStrip.tsx`, `ComicsPreview.tsx`, `ComicsSearchColumn.tsx`, tests beside each

**Interfaces:**
- Consumes: Task 6 methods; `describeComicsError`.
- Produces: `ComicsEditorPage({memesApi})` at `/admin/comics/:id`. Draft: left column (inline rename, panel strip with 1-based number, up, down, remove; preview; Build button disabled with the title "Build is not available yet") and right column (search sent with `record: false`, results with "+"). A built comics shows the result image (`builtImageId`), a provenance list (position, `sourceFilename`) and a link to `/memes/:builtImageId`, with no controls. Reorder sends the full `panelIds` order after the move. After add, remove or reorder the detail is replaced by the response, the preview version bumps after a 400 ms debounce, and `comicsAddability` is re-fetched for the visible results. A result is shown disabled with its reason (`in_comics` links to that comics via `comicsId`), "added" when `comicsId` equals the open comics, and a 409 from add (`image_in_comics`, `image_not_addable`) refreshes addability instead of showing an error. A preview 422 shows the reason in words and, when present, "panel N". A draft with no panels shows an empty state and does not request the preview.

- [ ] **Step 1: Write failing tests**: reducer unit tests; page tests for add (+ then strip grows, preview url version changes), up/down send the exact new id order, remove, disabled "+" per reason with the link, "added" state, `test_add_conflict_refreshes_addability_not_error` (mock add rejects with `image_in_comics`; the result becomes disabled and no error banner appears), preview 422 message with panel number, empty draft makes no preview request, built comics view has no controls and shows the provenance list, search requests carry `record: false`.
- [ ] **Step 2: Run to verify FAIL**, **Step 3: Implement** (React Compiler rules: refs only in handlers and effects; effects idempotent under StrictMode), **Step 4:** the three frontend checks. **Step 5: Commit** `feat(comics): comics editor page`.

### Task 9: Meme-page block and picker

**Files:**
- Create: `src/components/comics/MemeComicsBlock.tsx`, `ComicsPickerModal.tsx`, tests beside them
- Modify: the owner-only area of the meme page (`src/components/MemeDetails.tsx` or the curator panel, whichever already hosts owner-only actions; state the choice in the report) and its test

**Interfaces:**
- Consumes: `comicsByImage`, `listComics`, `createComics`, `addComicsPanel` (Task 6).
- Produces: `MemeComicsBlock({memesApi, imageId})` with three states: in a comics ("In comics X (panel n)" linking to `/admin/comics/:id`), not in one ("Add to comics…" opening `ComicsPickerModal`), and conflict ("Move from X" after an `image_in_comics` error calls `addComicsPanel(id, imageId, true)`). The picker lists draft comics with a name search and a "New comics" option (name field, errors via `describeComicsError`), then shows a link to the editor.

- [ ] **Step 1: Write failing tests** for the three states, `by-image` returning `{comics: null}` after a retired panel (shows "Add to comics…", no error), picking an existing draft, creating a new one with a bad name (reason in words), and the move offer.
- [ ] **Step 2: Run to verify FAIL**, **Step 3: Implement**, **Step 4:** the three frontend checks plus the existing `MemeDetails` tests. **Step 5: Commit** `feat(comics): add-to-comics block on the meme page`.

### Task 10: Docs, tracker and status

**Files:**
- Modify: `docs/superpowers/specs/2026-11-02-multi-image-comics/tracker.md` (C3 `done`, review link), the C3 spec status line (`done`) and `Plan:` line, `README.md` of the epic if it lists status, the memory pointer is updated by the controller

- [ ] **Step 1:** Run all suites once: `cd Backend && pytest`; `pytest tests/docs/`; `DATABASE_URL=... pytest tests/integration/ -v` (whole root: the repository file changed); frontend `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`; `git diff Frontend/memes-frontend/src/types/generated/` shows nothing after `bash generate-types.sh`.
- [ ] **Step 2:** Update the tracker and spec status lines, commit `docs(comics): C3 done`.
