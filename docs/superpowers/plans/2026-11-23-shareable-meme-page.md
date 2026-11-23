# Shareable meme page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pasted meme link shows a preview card (picture, title, text) in chat apps and still opens the real meme page.

**Architecture:** A new read-only backend route `GET /api/share/{id}` returns an HTML page with Open Graph/Twitter tags plus a redirect to `/memes/{id}`; the frontend's "Copy link" puts that share URL (built from the configured backend base) on the clipboard.

**Tech Stack:** FastAPI + SQLAlchemy async (Backend), pytest with mocks; React 19 + TypeScript + Vitest (Frontend).

**Spec:** `docs/superpowers/specs/2026-11-23-shareable-meme-page-design.md`

## Global Constraints

- New route: router made with `make_router(prefix="/share", tags=["share"])`, included in `Backend/app/main.py` with `prefix="/api"`, one line `("GET", "/api/share/{image_id}"): READER` in `ROUTE_POLICY` (`Backend/app/stage_policy.py`); `backend_api.md` documents it. Never leave a route without a verdict.
- Layers: Router -> Service/Repository; repositories never call `session.commit()`; no raw SQL in the router; no database write anywhere in this feature.
- Every dynamic value written into HTML goes through `html.escape(value, quote=True)`; the path parameter is validated as a UUID and never echoed; the file name never appears in the response.
- Eligible = valid UUID, image exists, `status == 'active'`, not flagged, file exists on disk. Anything else: `404 {"detail": "Not Found"}`.
- Exact tag values: `og:type` `website`; `og:site_name` `Memes`; `og:title` `A meme`; `og:description` = OCR text (lines joined with a space) cut to 200 characters with `…` at the end when cut, else `Open this meme`; `og:image` = `{request.base_url}api/images/{id}` absolute; `og:image:width` and `og:image:height` only when both known; `og:url` = the share URL; `twitter:card` `summary_large_image`.
- Headers: `Cache-Control: public, max-age=300`, `Content-Type: text/html; charset=utf-8`.
- Redirect target: relative `/memes/{id}` on prod (`config.stage.is_prod()`); on dev `{settings.get("GENERAL.FRONTEND_ORIGIN")}/memes/{id}` when that setting is set, else relative.
- Backend tests: `cd Backend && pytest` (its own root, never combined with other test roots). Frontend gate before every frontend commit, from `Frontend/memes-frontend/`: `pnpm exec tsc -b`, `pnpm exec eslint src/ --max-warnings 0`, `pnpm exec vitest run`.
- Commit messages end with the two attribution lines from the session reminder (Co-Authored-By and Claude-Session). Subagents work only in this worktree, never bind ports 8081-8083 / 5173-5175, never receive `DATABASE_URL`.

## Review Focus

- A flagged image, a pending/rejected/`cut_proposed` image and an image whose file is gone each answer 404 (not a preview of a hidden meme).
- OCR text containing `<script>`, quotes, `&`, `>` and non-ASCII (Cyrillic, emoji) is escaped in every attribute and in the body and never breaks out of an attribute; the description cut never splits an emoji.
- A path parameter that is not a UUID (`../`, `<img>`, an empty-looking id) answers 404 without being echoed.
- Behind the prod proxy the `og:image` and `og:url` use the public host and scheme (`https`), not an internal host (use `request.base_url`, covered by a test with forwarded-style base URL).
- The dev redirect goes to the frontend origin, never to the backend origin; the prod redirect is relative.
- "Copy link" on a dev frontend (backend on another origin) and on a prod build (same-origin, empty or relative backend base) both produce an absolute `.../api/share/<id>` URL.

---

### Task 1: Backend share route

**Files:**
- Create: `Backend/app/api/share.py`
- Modify: `Backend/app/main.py` (include the router), `Backend/app/stage_policy.py` (one `ROUTE_POLICY` line), `Backend/app/repositories/image_repository.py` (one read method), `backend_api.md`
- Test: `Backend/tests/test_share_endpoint.py`

**Interfaces:**
- Produces: `GET /api/share/{image_id}` -> `HTMLResponse`; `ImageRepository.get_share_info(image_id: str) -> Optional[ShareInfo]` where `ShareInfo` is a small frozen dataclass or NamedTuple in the repository module with fields `filename: str`, `status: str`, `flagged: bool`, `width: Optional[int]`, `height: Optional[int]`, `text: list[str]` (OCR text lines, empty when none); `None` when the id is unknown. Read how `get_filename`, `get_is_flagged` and the OCR text read of `get_meme` are done in `ImageRepository`/`ImageService` and reuse those queries and models (`Storage/models.py`; never redefine tables).

- [ ] **Step 1: Write failing tests** in `Backend/tests/test_share_endpoint.py`, following `Backend/tests/test_images_endpoints.py` (a bare `FastAPI()` with the share router under `/api`, the repository dependency overridden with an `AsyncMock`, `image_exists` patched):
  - `test_share_page_has_preview_tags`: eligible meme (text `["hello", "world"]`, width 800, height 600) -> 200, `text/html`, the body contains `og:title` with `A meme`, `og:description` with `hello world`, an absolute `og:image` equal to `http://testserver/api/images/<id>`, `og:image:width` `800`, `og:image:height` `600`, `twitter:card` `summary_large_image`, `og:url` `http://testserver/api/share/<id>`, and header `cache-control` `public, max-age=300`.
  - `test_description_falls_back_when_no_text`: empty text -> `Open this meme`.
  - `test_description_is_cut_at_200_chars_with_ellipsis`: 500 characters -> the description attribute is exactly 200 characters ending with `…`; a text of emoji only is cut without splitting a code point (decode the body, assert no lone surrogate).
  - `test_html_is_escaped`: text `<script>alert(1)</script> "q" & 'x'` -> the body contains no literal `<script>alert(1)</script>`, contains the escaped form, and the attribute value stays inside its quotes.
  - `test_file_name_never_in_body`: filename `secret-client-name.jpg` -> not in the body.
  - `test_size_omitted_when_unknown`: width/height `None` -> no `og:image:width`/`og:image:height` tags.
  - `test_404_cases` (parametrized): invalid UUID (`not-a-uuid`, `..%2F..`), unknown id (repo returns `None`), status `pending`, status `rejected`, status `cut_proposed`, `flagged=True`, file missing (`image_exists` False) -> `404` with `{"detail": "Not Found"}` and the input not echoed in the body.
  - `test_prod_redirect_is_relative` (patch `is_prod` to True): the body's refresh/`location.replace`/fallback link target is `/memes/<id>`.
  - `test_dev_redirect_uses_frontend_origin`: setting `GENERAL.FRONTEND_ORIGIN` = `http://localhost:5174` -> target `http://localhost:5174/memes/<id>`; with the setting unset -> `/memes/<id>`.
  - `test_public_host_used_behind_proxy`: request with base URL `https://memes.example/` (use `TestClient(app, base_url="https://memes.example")`) -> `og:image` and `og:url` start with `https://memes.example/`.
  - Also extend/verify the existing route-policy guard test in `Backend/tests/test_stage_policy.py` passes with the new route (run it; add a case only if that file enumerates routes by hand).

- [ ] **Step 2: Run to see them fail:** `cd Backend && pytest tests/test_share_endpoint.py -q` -> collection or assertion failures (module missing).

- [ ] **Step 3: Implement.** `ImageRepository.get_share_info` as above (one query joining image, extras for flagged and the OCR text; flagged None counts as False). In `Backend/app/api/share.py`: validate `image_id` with `uuid.UUID(...)` (404 on `ValueError`), call the repository via `get_async_db`/`ImageRepository` like `get_image` does, apply the eligibility order, `image_exists(filename)`, then build the HTML with `html.escape(..., quote=True)` for every dynamic value, a helper `_description(text: list[str]) -> str` that cuts by code point (`list(joined)[:199] + "…"` when longer than 200) and a helper for the redirect target (`config.stage.is_prod()`, `settings.get("GENERAL.FRONTEND_ORIGIN")`). The HTML carries the tags from Global Constraints, `<meta http-equiv="refresh" content="0; url=TARGET">`, a script `location.replace(TARGET_JSON)` where the target is passed through `json.dumps` and `</` is neutralised (`<\/`), and a visible link "Open this meme". Register: `app.include_router(share_router, prefix="/api")` in `main.py` (not behind `is_prod()`), and `("GET", "/api/share/{image_id}"): READER` in `ROUTE_POLICY` next to the other reader routes.

- [ ] **Step 4: Run** `cd Backend && pytest -q` (the whole Backend root) -> all pass, including `test_stage_policy.py`; run `python -c "import Backend.app.main"` from the repo root with `APP_STAGE=prod` set to prove the prod import does not raise (default deny) and once without it.

- [ ] **Step 5: Docs.** In `backend_api.md` add a `GET /api/share/{id}` section: purpose, eligibility, the tags table, redirect rule, caching, 404.

- [ ] **Step 6: Commit** `feat(share): GET /api/share/{id} preview page for pasted meme links`.

---

### Task 2: Frontend share URL

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`, `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`, `Frontend/memes-frontend/src/test/mockApi.ts`, `Frontend/memes-frontend/src/memeActions/useMemeActions.ts`
- Delete: `Frontend/memes-frontend/src/memeActions/links.ts` (and its test if it has one)
- Test: the existing tests of `HttpMemesApi` and of `useMemeActions` (find them with `Glob` for `HttpMemesApi*.test.*` and `useMemeActions*.test.*`; add cases there)

**Interfaces:**
- Consumes: `getImageUrlById(imageId: string): string` in `MemesApi`/`HttpMemesApi` (the pattern to follow for the base URL).
- Produces: `getShareUrlById(imageId: string): string` on `MemesApi` (always an absolute URL ending in `/api/share/<id>`).

- [ ] **Step 1: Write failing tests**: `HttpMemesApi.getShareUrlById` for (a) an absolute base `http://localhost:8082` -> `http://localhost:8082/api/share/abc`, (b) a base with a trailing slash -> no double slash, (c) an empty base -> `<window.location.origin>/api/share/abc`, (d) a relative base `/backend` -> `<origin>/backend/api/share/abc`. `useMemeActions`: "Copy link" copies `api.getShareUrlById(id)` (assert the clipboard text equals the mock's value), and the copy-image fallback that copies a link copies the same value; the success toast text is unchanged.

- [ ] **Step 2: Run to see them fail:** `pnpm exec vitest run src/api src/memeActions`.

- [ ] **Step 3: Implement** `getShareUrlById` on the interface, in `HttpMemesApi` (follow how `getImageUrlById` reads the base; resolve against `window.location.origin` with `new URL(...)` so the result is always absolute) and in `makeMockApi` (returns a deterministic `http://localhost/api/share/<id>`-style value, overridable like the other mock methods). In `useMemeActions.ts` replace both `memePageUrl(meme.id)` calls (the copy-link case and the copy-image fallback) with `memesApi.getShareUrlById(meme.id)` using the API object the hook already has access to (read how the hook gets it; if it does not receive the API today, pass it through its existing options/props rather than importing a singleton); delete `links.ts` and its import.

- [ ] **Step 4: Run** the full frontend gate from the Global Constraints -> clean and green.

- [ ] **Step 5: Commit** `feat(share): copy the share URL for Copy link`.

---

### Task 3: Verification and docs

**Files:**
- Modify: `docs/superpowers/specs/2026-11-18-ui-ux-audit.md` (Progress), `docs/superpowers/specs/2026-11-23-shareable-meme-page-design.md` (status line -> `done`)

- [ ] **Step 1 (controller, not a subagent):** run the full gates once more (`cd Backend && pytest -q`; frontend `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`).
- [ ] **Step 2 (controller):** start a throwaway backend from this worktree on a free port (8095, never 8081-8083) with `--env-file environments/.env.general` and `curl` an eligible id (read-only `GET`s; the controller picks an active unflagged id from `GET /api/images?limit=1` of the live general backend), a flagged id and a fake id: expect tags in the first, 404 in the others; send the request with a `TelegramBot` and a `facebookexternalhit/1.1` user agent; stop the server by its listening port afterwards.
- [ ] **Step 3:** update the audit doc Progress entry (item 5 done; the manual acceptance of the spec is the owner's; follow-up: dev `GET /api/images/meme/{unknown}` stub) and set the spec status to `done`; commit `docs(audit): mark shareable meme page done`.
