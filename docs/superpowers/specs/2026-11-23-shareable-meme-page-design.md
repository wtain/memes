# Shareable meme page with a link preview (UI/UX audit ranking item 5, I22)

Status: planned
Plan: `docs/superpowers/plans/2026-11-23-shareable-meme-page.md`
Originates from: `docs/superpowers/specs/2026-11-18-ui-ux-audit.md` (ranking item 5, idea I22, finding F6)

Approval: the owner chose option A (`<origin>/api/share/{id}`) and said "go ahead, worktree, subagent-driven" in the session of 2026-11-23.

## Intent

The app will be shared with a few friends on a public read-only stage. The first thing a friend does is paste a meme link into a chat. Today the copied link is `<origin>/memes/{id}`, a client-rendered page: a chat app's preview crawler does not run JavaScript, so it sees an empty HTML shell and the pasted link shows no meme. The link must show the meme (picture, a short title and the text on it) in the chat, and still open the real page when a person clicks it.

## Goals

1. A backend route `GET /api/share/{id}` that answers with a small HTML page carrying Open Graph and Twitter-card tags (`og:title`, `og:description`, `og:image` with an absolute URL, `og:url`, image size when known, `twitter:card=summary_large_image`) and sends a person on to the real page `/memes/{id}`.
2. The route is served on prod (`READER` in `ROUTE_POLICY`) and on dev, with no change to the prod edge (Caddy already proxies `/api/*`).
3. "Copy link" (and the copy-image fallback that copies the link when the browser cannot copy the picture) put the share URL on the clipboard, built from the configured backend base so it works on dev (frontend and backend on different origins) and on prod (same origin).
4. Memes that must not be shown get no preview: unknown ids, memes that are not `active`, flagged memes, a missing file.
5. Nothing client-chosen leaks into the preview: the file name is never written into the HTML.

## Non-goals

- A new frontend page, the two-column detail layout, or a per-meme `<title>`/meta for a direct visit of `/memes/{id}` (ranking item 7; the preview comes from the share route only).
- Thumbnails or resizing for the preview image: `og:image` is the stored image as it is.
- A prod edge (Caddy) change or a short `/m/{id}` URL (decided against, option B).
- Android: its share action is untouched.
- The native Share button, which shares the image file, not a link.
- Changing `GET /api/images/meme/{id}` (it still answers 200 with an empty stub for an unknown id; recorded as a follow-up in the audit doc).

## Anti-goals

- The page must never write unescaped data into HTML, echo the path parameter, or expose the file name or any internal path.
- A flagged, pending, rejected or recut-proposed image must never get a preview, even though `GET /api/images/{id}` serves any image by id today (not changed here).
- No new dependency and no database write; the route only reads.

## Constraints

- A new route needs a `(method, full path) -> READER` line in `Backend/app/stage_policy.py` and a router made with `make_router(...)` (project rule); `Backend/tests/test_stage_policy.py` must pass, and `backend_api.md` must document the route (project rule).
- Layers: Router -> Service/Repository, repositories never commit (CLAUDE.md "Backend pattern").
- Absolute URLs come from the incoming request (`request.base_url` already reflects the proxy headers, because prod starts gunicorn with forwarded headers trusted from the edge); no new setting is needed for the origin of the image.
- Frontend gate: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`; backend: `cd Backend && pytest` (its own root, never combined with other test roots).
- The service never calls the live databases with write rights in a test; tests use mocks as the other backend endpoint tests do.

## Approaches considered

Link shape and who serves the preview:
- A. `GET /api/share/{id}` on the backend (**chosen**): no edge change, works on dev and prod because the frontend already knows the backend base, one small route.
- B. `GET /m/{id}` with a new Caddy rule: prettier URL but a prod edge change the owner must deploy, and broken on dev where Vite proxies only `/api`.
- C. Pre-render at build time or serve per-meme `index.html` from the edge by user agent: needs infrastructure changes and does not fit dynamic content. Rejected.

Redirect to the real page:
- Relative `/memes/{id}` on prod (same origin as the share route), the configured `GENERAL.FRONTEND_ORIGIN` plus `/memes/{id}` on dev when it is set (frontend and backend are different origins there), relative otherwise.

## Design

### Backend

- New `Backend/app/api/share.py`: `router = make_router(prefix="/share", tags=["share"])`, `GET /{image_id}` returning an `HTMLResponse`, included in `main.py` with `prefix="/api"` (before nothing special; the path does not collide with `/api/images`), and `ROUTE_POLICY[("GET", "/api/share/{image_id}")] = READER`.
- Eligibility, in this order, each failure a plain `404 {"detail": "Not Found"}` (no echo of the input): `image_id` is a valid UUID; the image exists with `status == 'active'`; it is not flagged; its file exists on disk (reuse `image_exists`/`get_filename` as `get_image` does). The reads go through a small repository method on `ImageRepository` (one query: filename, status, flagged, width, height) and a thin service or the router, following how `get_image` is wired; the builder picks the lightest fit.
- Tags written (every value HTML-escaped with `html.escape(value, quote=True)`):
  - `og:type` = `website`, `og:site_name` = `Memes`
  - `og:title` = `A meme` (fixed; the file name is never used)
  - `og:description` = the meme's OCR text joined with spaces and cut to 200 characters with an ellipsis; if there is no text, `Open this meme`
  - `og:image` = `{base}/api/images/{id}` (absolute), `og:image:width`/`og:image:height` only when both are known
  - `og:url` = the share URL itself; `twitter:card` = `summary_large_image`, `twitter:image` and `twitter:title`/`twitter:description` repeat the values
  - `<link rel="canonical">` is not written (the share URL is the shared identity).
- The body: a `<meta http-equiv="refresh" content="0; url=...">`, a one-line script `location.replace(...)`, and a visible fallback link "Open this meme" to the redirect target. The redirect target is built as described above; the id in it is the validated UUID, so no unescaped input appears.
- Headers: `Cache-Control: public, max-age=300` (a flag takes effect within five minutes; chat apps cache previews on their side anyway), `Content-Type: text/html; charset=utf-8`.
- Logging: the route follows the existing convention of not logging searched text; there is none here. The image id is a path segment like in `get_image`.

### Frontend

- `MemesApi` gains `getShareUrlById(imageId: string): string`, implemented in `HttpMemesApi` next to `getImageUrlById` as an absolute URL (`new URL(`/api/share/${imageId}`, base)` where the base is the configured backend base resolved against `window.location.origin` when it is empty or relative), and in the mock API used by tests (`src/test/mockApi.ts`).
- `src/memeActions/links.ts`'s `memePageUrl(id)` is replaced by the API method: `useMemeActions` calls `memesApi.getShareUrlById(meme.id)` for "Copy link" and for the copy-image fallback that copies a link; the toast copy stays as it is. `memePageUrl` is deleted (nothing else uses it).
- No visual change.

### Docs

- `backend_api.md`: a `GET /api/share/{id}` section (purpose, eligibility, response, the tags, caching, 404).
- The audit spec's Progress section records item 5 as done and lists the manual acceptance below.

## Tests

- Backend (mocked, like `test_images_endpoints.py`): 200 with the expected tags and an absolute `og:image` for an eligible meme; description from OCR text, cut at 200 characters with an ellipsis, and `Open this meme` without text; HTML-escaping of text containing `<script>`, quotes and `&`; the file name never appears in the body; 404 for an invalid UUID, an unknown id, a non-active status, a flagged meme and a missing file; the dev redirect uses `GENERAL.FRONTEND_ORIGIN` when set and the relative path otherwise; the stage-policy tests and the route-policy guard test pass with the new route.
- Frontend: `HttpMemesApi.getShareUrlById` for an absolute base, a relative or empty base, and a base with a trailing slash; `useMemeActions` copies the share URL for "Copy link" and for the copy-image fallback.

## Manual acceptance (owner, after deploying to prod)

Paste `https://<prod host>/api/share/<id>` into Telegram, WhatsApp and Discord and see a card with the picture, "A meme" and the text; click it and land on the meme page; paste the link of a flagged meme and see no card. Previews are cached by the chat apps, so a changed meme keeps its old card for a while.

## Left to the builder

The repository/service split for the eligibility read, the exact HTML skeleton, the helper that cuts the description, the way the dev redirect origin is read (`settings.get("GENERAL.FRONTEND_ORIGIN")` as `main.py` does), and test file names.

## Decisions I made without asking (check these)

- `og:title` is the fixed text "A meme" (a file name or a generated title would be client-chosen or invented).
- The description is the OCR text (public library text) and not the Ollama description.
- The preview image is the original file (no resizing); a very large GIF may not show in some chat apps.
- `max-age=300`, and a plain JSON 404 for an unavailable meme (a person following a dead link sees the same answer as for any missing API path).
