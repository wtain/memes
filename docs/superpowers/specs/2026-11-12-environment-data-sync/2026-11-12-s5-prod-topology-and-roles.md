# S5: prod topology and roles

Status: draft
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/tracker.md

Source: code and config reading only (worktree `env-data-sync`, alembic head `a9d3e6b1c847`). No database, container, server or network
was touched; no `.env.*` file was opened. Everything marked **Proposal** is a judgement call for the owner. Things the code does not
settle are under "Uncertainties". Inputs: S1 (classes D/P/X/R), S2 (sizes), S3 (ADR: preconditions, pull/promote), `docs/security/admin-permissions-todo.md`
(the existing route-by-route security inventory; it predates the comics and recut routes being added to its tables and is superseded by section 1 for prod).

Legend. Verdict: **K** keep on prod, **D** disable on prod (not mounted or answered 404). "P write" = writes a class-P table, allowed. Any other write is named.

## Headline findings

1. **There is no authentication anywhere** (`docs/security/admin-permissions-todo.md`; no `Depends` on any credential in `Backend/app`). A prod backend on a public
   VPS exposes every route to the internet. "Fewer admin capabilities" therefore cannot mean "hidden behind a login": it must mean routes that are not served, plus DB
   grants that make the writes impossible even if the gate fails (defence in depth, section 5).
2. **Reader-facing routes that touch dev-owned data** (the surprises):
   - `GET /api/images/trending?window=30d` and `GET /api/images/meme/{id}/popularity` (breakdown) read **`popularity_absorptions`**, which S1 classifies X (never shipped)
     (`Backend/app/repositories/image_repository.py:36-50`, `repository/popularity.py:26-44`). On prod the table is empty: the 30-day trending window returns nothing and the
     breakdown is empty. `window=all` uses `images.popularity` (class D) and works. Also `GET .../impressions` folds copies through the same edges (`repository/meme_impressions.py:26`).
   - `GET /api/diagnostics/statistics` **writes `statistics_snapshots` on a GET** (recompute when older than 2 h, and always with `?live=true`,
     `Backend/app/api/diagnostics.py:99-126`). Its ~28 subqueries read many class-X tables (`tmp_clusters`, `image_classifications`, `ocr_text_embeddings`...), so on prod the numbers are wrong, not just slow.
     With no scheduler on prod nothing else refreshes it, so every reader hit after 2 h pays the full compute; `?live=true` is an unauthenticated load lever.
   - `GET /api/history` returns **every reader's search text**, including meme-from-text messages (`kind=meme_text|all`), to anyone. On prod that is a privacy leak of the exact data prod collects.
   - `GET /api/images?q=` carries the user's search text **in the URL**; a reverse proxy access log would record it (section 7).
3. **Mutations of class-D data exist on routes that look like normal UI**: description approve/reject (also deletes Ollama tags), description notes, flag/unflag (+ popularity edges), set popularity, profile PUT/confirm/DELETE (+ Ollama embed call), comics build (spawns `comics_pipeline`), ingestion resolve, recut approve/adjust (writes files). All must be unreachable on prod.
4. **The only ML needed at request time is Ollama for meme-from-text** (parse + 1024-d query embedding). Similar-image search, text search, recommendations, concepts all use stored vectors and lemma tables: no torch/CLIP/sentence-transformers on prod. Without Ollama, meme-from-text degrades to lexical-only (section 4).
5. **`Dockerfile.backend` is effectively the prod image already, but config would start the scheduler**: with `APP_ENV=general` (the default) `settings.general.yaml` sets `scheduler.enabled: true` (`environments/settings.general.yaml:5-6`); `Backend/app/main.py:109` would start `trends_batch`/`build_statistics`/`export_ratings` loops in every gunicorn worker. The image has no `batch.run_wrapper`, so the spawns fail, but a stage switch is still required.
6. **gunicorn `-w 4` with `pool_size=10, max_overflow=20` per worker = up to 120 DB connections** from one container (`Dockerfile.backend` CMD, `Storage/db.py:23-24`) against Postgres' default `max_connections=100`. Size it on prod (section 6).

## 1. Endpoint inventory with a prod verdict

90 routes (89 under `/api` plus `GET /health`, `Backend/app/main.py:166`). Mounted at `main.py:147-163`. "Reader use" = frontend page or Android screen that calls it.
Tables: R = reads, W = writes. Files in `BASE_PATH` unless said.

### diagnostics, history, root (`diagnostics.py`, `history.py`, `main.py`)

| Route (file:line) | Reads / writes | Reader use | Verdict |
|---|---|---|---|
| `GET /health` (main.py:166) | `SELECT 1` | Docker HEALTHCHECK, Android | K |
| `GET /api/diagnostics/health` (diagnostics.py:93) | `SELECT 1` | monitors | K |
| `GET /api/diagnostics/statistics` (:99) | R many incl. class X; **W `statistics_snapshots` on GET** (class R) | `/statistics`, Android Statistics | **D** (proposal; see 1.1) |
| `GET /api/history` (history.py:66) | R `search_history*` (all readers' text) | no page | **D** (privacy) |
| `POST /api/history/{id}/not-found` (:107) | W `search_not_found` (P) | `NotFoundButton` on `/search`, `/reply` | K |

### images (`images.py`)

| Route (line) | Reads / writes | Reader use | Verdict |
|---|---|---|---|
| `GET /api/images` (121) | R lemma/tag/ocr tables; **W P** `search_history`, `search_history_tags`, `meme_impressions` (when `q` or facets; `record=false` skips) | `/search`, `/explore`, Android | K (P write) |
| `GET /api/images/{id}/similar` (138) | R `embeddings` / description / note embeddings; W P `meme_impressions` (`record`) | MemePage, Android | K (P write) |
| `GET /api/images/{id}/descriptions` (151) | R `image_descriptions`, feedback | MemePage, Android sheet | K (read; shows approve/reject state, see next rows) |
| `PUT .../descriptions/{key}/approve` (161), `/reject` (173) | **W `image_description_feedback` (D)**; reject also deletes Ollama `tags` (D) | MemePage buttons, Android sheet | **D** |
| `PUT /api/images/{id}/description-note` (185), `DELETE` (196) | **W `description_notes` (D)** (+ note tags deleted on clear) | MemePage | **D** |
| `GET /api/images/meme/{id}` (206) | R | MemePage, Android | K |
| `GET .../meme/{id}/popularity` (216) | R `images.popularity`; **R `popularity_absorptions` (X)** | MemePage `PopularityBlock` | K with caveat (breakdown empty on prod) or D (open question 3) |
| `PUT .../meme/{id}/popularity` (226) | **W `popularity_absorptions` (X) + `images.popularity` (D)** | MemePage control | **D** |
| `POST .../meme/{id}/saves` (237) | W `meme_saves` (P) | MemePage, Android | K (P write) |
| `GET .../meme/{id}/saves` (247) | R `meme_saves` | MemePage | K |
| `GET .../meme/{id}/impressions` (257) | R `meme_impressions`, `popularity_absorptions` tree | MemePage | K (tree empty on prod) |
| `PUT .../mark_flagged` (267), `.../unmark_flagged` (278) | **W `image_extras` (D)**, `popularity_absorptions` (X), `process_events` (X) | MemePage, MemeCard, `/duplicates`, Android detail | **D** |
| `GET .../get_flagged` (286) | R `image_extras` | MemePage toggle | **D** (flagged images are never on prod) |
| `GET /api/images/flagged` (297), `/untagged` (309), `/no-ocr` (334) | R | Cleanup menu, Android Flagged | **D** |
| `GET /api/images/trending` (321) | R `images`; window=30d R `popularity_absorptions` (X) | `/trending-memes` | K for `window=all`; `30d` returns empty (open question 3) |
| `GET /api/images/duplicates` (346), `/duplicates/clusters/{id}/similarity` (373), `/duplicates/decisions` (409) | R `tmp_clusters`, `tmp_duplicates`, `duplicate_decisions` (X) | `/duplicates` | **D** |
| `POST /api/images/duplicates/clusters/{id}/dismiss` (359), `/duplicates/pairs/undo-dismiss` (398) | **W `duplicate_decisions`, `process_events` (X)** | `/duplicates` | **D** |
| `GET /api/images/{id}` (442) | R `images.filename`; file bytes, `Cache-Control: immutable` 1 y, `Access-Control-Allow-Origin: *` | everything | K |

Route-order hazard: the removed literal routes (`/flagged`, `/untagged`...) would fall through to `GET /api/images/{image_id}`, whose `Image.id == "flagged"` comparison probably raises a DB cast error (500) instead of 404 (`image_repository.py:206-210`; not verified). The gate must answer 404 itself (section 2).

### meme-from-text (`meme_from_text.py`, `meme_text_ratings.py`, `meme_profiles.py`)

| Route (line) | Reads / writes | Reader use | Verdict |
|---|---|---|---|
| `POST /api/meme-from-text/search` (meme_from_text.py:37) | R profiles, vectors, lemmas; Ollama (parse + embed); W P `search_history` (`query_log.enabled`), `meme_impressions` | `/reply` | K (P write; ML, section 4) |
| `GET /api/meme-from-text/taxonomy` (:56) | in-memory YAML, `Cache-Control: public, max-age=300` | `/reply` | K |
| `PUT /api/meme-from-text/ratings` (ratings.py:38), `DELETE` (:46), `GET` (:56) | W/R `meme_text_ratings` (P); reads `images.status` | `/reply` | K (P write) |
| `DELETE /api/meme-from-text/ratings/text` (:65) | W `meme_text_ratings.query_text=NULL`, **DELETE `search_history`** rows of that hash (P) | `/reply` Forget | K (anyone can erase a message's log; accepted, note in S4 pull: deletions arrive as mirror) |
| `GET /api/meme-from-text/profiles/{id}` (profiles.py:45) | R `meme_profiles*` | curator panel only | **D** |
| `PUT .../profiles/{id}` (:53), `POST .../confirm` (:62), `DELETE` (:70) | **W `meme_profiles*` (D)**, Ollama embed | curator panel (`ProfileEditor` on every MemePage) | **D** |

### recommendations, concepts, trends, uploads, bug reports

| Route (line) | Reads / writes | Reader use | Verdict |
|---|---|---|---|
| `GET /api/recommendations` (recommendations.py:30) | R | `/recommendations`, Android | K |
| `GET /api/concepts` (concepts.py:24), `/top-images` (:29), `/for-image` (:38), `/{id}` (:47) | R concept tables + `embeddings` | `/concepts`, MemePage | K if concepts are promoted (S1 Q6 provisional), else D |
| `GET /api/trends/dates` (trends.py:27), `/dates/{d}/runs` (:37), `/dates/{d}/runs/latest` (:46), `/runs/{id}` (:55), `/history` (:67) | R `trends_*` | `/trends*`, Android Trends | K if trends are shipped (S1 Q7 open), else D |
| `POST /api/uploads` (uploads.py:101) | R `images.content_hash` (any status); **file into `PATH_INGESTION_SOURCE`** (`.uploading/` staging, 507 under 1 GiB free); no DB write | `/upload`, Android album sync | K (prod inbox = class P files) |
| `POST /api/bug-reports` (bug_reports.py:24) | file into `BUG_REPORTS_PATH` or `BASE_PATH/bug_reports` | Android | K (class P file) |

### Dev-only routers (all D on prod)

| Router (file:lines) | Routes | What they do (tables) |
|---|---|---|
| `admin.py:69-92` | GET `/admin/batches/names`, POST `/{name}/run`, GET `/runs/{id}`, GET `/runs`, GET `/metrics` | `batch_runs`, `process_events`; **spawns `python -m batch.run_wrapper` subprocess** (`admin_batch_service.py:29-48`) |
| `admin_discovery.py:39-83` | GET `/admin/discovery/{memes,queries,misses}` | reads P tables, returns reader search text |
| `admin_profiles.py:40-48` | GET `/admin/profile-coverage`, `/images` | profile coverage, `batch_runs` |
| `ingestion.py:119-158` | GET `/ingestion/run`, `/pending`, `/clusters/{tier}`, `/review/tier_b`; POST `/clusters/{tier}/resolve`, `/images/{id}/undo-reject` | pending images, `tmp_duplicates`; resolve moves files to `rejected/`, changes `images.status`, popularity edges |
| `recut.py:33-73` | GET `/recut/proposals`, `/stats`, `/proposals/{id}`; POST `.../approve`, `/reject`, `/adjust` | `image_cuts`, `images`, writes cut files, copies source to `pre-cuts/` |
| `comics.py:64-162` | 15 routes: list/create/detail/preview/rename/delete/panels/order/build/candidates/addability/by-image | `comics*`, `comics_candidates*`, `images`; build writes a file and spawns `comics_pipeline` (`comics_pipeline_trigger.py`) |

Tally: **K 31 routes** (13 root/diagnostics/history/images, 6 meme-from-text, 1 recommendations, 4 concepts*, 5 trends*, 2 uploads/bug-reports; * conditional), **D 59 routes**.

### 1.1 Implicit and GET-side writes

| Where | Writes | Class | Acceptable on prod? |
|---|---|---|---|
| `GET /api/images?q=`/facets | `search_history`, `search_history_tags`, `meme_impressions`; `searchId` returned when `popularity.search_outcomes.enabled` | P | Yes, this is the point of prod; best effort in a savepoint, never fails the request (`impression_recorder.py`, `history_recorder.py`) |
| `GET .../{id}/similar` | `meme_impressions` | P | Yes |
| `POST /meme-from-text/search` | `search_history` (kind meme_text), `meme_impressions` | P | Yes |
| `GET /diagnostics/statistics` | `statistics_snapshots` | R | **Proposal: no.** Disable the route on prod (the numbers describe pipeline backlog that prod does not have). Alternative if the owner wants a corpus count page: serve the dev-computed snapshot as a promoted single row and remove the recompute path on prod (code change) |
| everything else with a P write | rate limiter `upload_limiter`/`bug_report_limiter` | memory | They are per process and keyed by `request.client.host` (`rate_limit.py`, `uploads.py:107`): behind a proxy every client is the proxy address and `-w 4` multiplies the limit by 4. Needs `--forwarded-allow-ips` / proxy headers (section 6) |

### 1.2 Tables the prod backend writes that are not class P

| Table | Via | Mitigation |
|---|---|---|
| `statistics_snapshots` (R) | `GET /diagnostics/statistics` | route disabled on prod, or grant INSERT/UPDATE on this one table |
| `image_extras`, `image_description_feedback`, `description_notes`, `tags`, `meme_profiles*`, `images.popularity`, `comics*` (D) | curator routes | routes disabled; **no grants** to the backend role |
| `popularity_absorptions`, `duplicate_decisions`, `process_events`, `batch_runs`, `image_cuts`, `tmp_*` (X) | admin, flag, duplicates routes | routes disabled; no write grants; SELECT on `popularity_absorptions` only (see 1 above) |

So with the listed 31 routes the backend writes exactly: `meme_saves`, `meme_impressions`, `search_history(+_tags)`, `search_not_found`, `meme_text_ratings`, plus `statistics_snapshots` if the statistics route stays.

## 2. Switching the admin features off

**Proposal: a stage setting plus a default-deny route gate.**

### 2.1 Stage selection

- Add `APP_STAGE` (`dev` default, `prod`) as a plain environment variable, read in `config/settings.py` `_build()`. When `prod`, append a third settings file to `settings_files`
  after the environment file: `environments/settings.prod.yaml` (common prod overrides) and optionally `environments/settings.<env>.prod.yaml`. Dynaconf with `merge_enabled=True` merges dicts
  key by key and **appends lists**, so a prod overlay cannot remove a `scheduler.jobs` entry; it turns the whole scheduler off with `scheduler.enabled: false` instead (the existing switch, `scheduler.py:14`).
  This keeps the one `settings` object, no new loader, and the tracked config stays in git (secrets still only from env).
- **Fail-safe default.** `Dockerfile.backend` is used only for prod (dev runs `uvicorn` from the venv, CLAUDE.md "Commands"), so bake `ENV APP_STAGE=prod` into the image. A forgotten variable on the VPS then still means prod. Add a single helper `config.stage.is_prod()` so no code reads the variable directly.
- Expose it: `settings.get("stage")` set by the loader (`stage: prod` in `settings.prod.yaml`, `dev` in `settings.yaml`).

### 2.2 The route gate (default deny)

- New `Backend/app/stage_policy.py`: a table `ROUTE_POLICY: dict[(method, path_template), "reader" | "dev_only"]` covering every route of section 1.
- New pure-ASGI middleware `StageGate` registered only when `is_prod()`: resolve the matching route the way Starlette will (`for route in app.routes: match, _ = route.matches(scope)`, first full match wins),
  look up `(method, route.path)`, and answer **404** (not 403: do not advertise) unless the policy says `reader`. A route missing from the table is treated as `dev_only`. This avoids the fall-through-to-`/images/{image_id}` hazard of simply not mounting routes.
- **Also do not mount** the pure-admin routers on prod in `main.py` (`admin`, `admin_discovery`, `admin_profiles`, `ingestion`, `recut`, `comics`, `meme_profiles`) so they vanish from OpenAPI, and set `docs_url=None, redoc_url=None, openapi_url=None` on prod (currently `/docs` and `/openapi.json` are open, `main.py:121-125`). The gate stays as the second layer for mixed routers (`images.py`, `diagnostics.py`, `history.py`).
- Conditional `include_router` + per-route entries (not a global flag inside each handler) keep the existing handlers untouched, so tests for dev behaviour do not change.

### 2.3 Background work on prod

| Startup item (`main.py`) | Prod behaviour | How |
|---|---|---|
| `start_scheduler()` (:109) | none | `scheduler.enabled: false` in `settings.prod.yaml`; additionally `if is_prod(): skip` in lifespan so a bad overlay cannot start it |
| admin batch trigger / `start_comics_pipeline` | unreachable | routes gated; image lacks `batch.run_wrapper` anyway (`Dockerfile.backend` copies only 4 batch modules) |
| `_warm_models` task (:105) | only if Ollama exists on the VPS | see 4.3; it never raises, logs at INFO (`main.py:79-88`); with 4 gunicorn workers there are 4 warm loops (one per worker) |
| `get_query_parser()` / `get_pipeline()` | built from settings | `parse.model` absent = no parser, `available:false` (reuse below) |
| `validate_*_settings()` | unchanged, must pass | prod overlay must keep the `popularity.*` and `search.*` keys |

### 2.4 Reusing the `it` pattern (`meta.available: false`)

`parse.model` is absent from `settings.it.yaml` (it appears only in metal and general overlays); `get_query_parser()` then returns `None`, `get_pipeline()` returns `None`, and `MemeFromTextService._unavailable()` answers HTTP 200 with
`meta.available false`, `fallback "unavailable"`, no items (`meme_from_text_service.py:21-23,118`). The frontend turns that (or a 404/405) into the `unavailable` state (`reply/messageSearchReducer.ts:63`, `reply/useMessageSearch.ts:51-52`).
This is reusable for **one** feature only: meme-from-text on a prod without Ollama. It does not generalise to admin routes (they have no "unavailable" body). A prod `general` has two honest choices (4.3): remove `parse.model` from `settings.general.prod.yaml` (feature off, like `it`), or keep it and run degraded.

### 2.5 Tests (Proposal)

1. **Verdict-completeness guard** in `tests/docs/`-style, no DB: import the FastAPI app, enumerate `app.routes`, assert every `(method, path)` has an entry in `ROUTE_POLICY` and that no entry names a route that no longer exists (same pattern as `test_image_derived_data_inventory.py`: new route fails until classified). Needs `DATABASE_URL` dummy (root `tests/conftest.py` already sets one).
2. **Gate behaviour** (`Backend/tests/`, mocked DB): build the app with `APP_STAGE=prod`, assert every `dev_only` route answers 404 for its method (including the `/images/flagged` fall-through case) and every `reader` route is reachable; assert `/docs` and `/openapi.json` are 404; assert the scheduler is not started.
3. **Write-set test** (integration, `ocrdb_test`): create the `backend_app` role with the section 5 grants, run the reader routes through the app against it, and assert no `permission denied`; then try each dev-only write and assert the DB rejects it. This is the only test that proves the grants and the route table agree.
4. **Docs guard**: a table in `docs/` (or this spec's successor) that lists the K routes, checked by test 1, so P5 documentation cannot drift.

## 3. Frontend and Android on prod

### 3.1 How the frontend learns the stage

No existing endpoint or config carries it: `VITE_BACKEND_API_URL` is the only per-environment frontend variable read in `src` (`app/router.tsx:35`); `VITE_ENV_NAME` is documented in `Environments.md` but not used in `src`; `GET /api/diagnostics/health` returns `{backend, database}` only (`diagnostics.py:34-36`).
**Proposal:** (a) a build-time flag `VITE_STAGE=prod` (new `.env.<env>.prod` for the prod build; static, no request on load, the backend still enforces); (b) optionally a runtime `GET /api/diagnostics/capabilities` returning `{stage, features}` (always K), added to `shared/schemas/` so the TypeScript and Kotlin types are generated. Android needs (b) because its backends are chosen at runtime (user-defined environments, `EnvironmentManagerScreen`); the web does not.

### 3.2 Web pages and entries (all in `src/app/router.tsx`, `AppLayout.tsx`)

| Item | On prod |
|---|---|
| Menu groups **Cleanup** (`/untagged`, `/no-ocr`, `/flagged`, `/duplicates`, `/ingestion`) and **Admin** (`/admin`, `/admin/profiles`, `/admin/recut`, `/admin/comics`, `/admin/discovery`) (`AppLayout.tsx:21-56`) | hide the groups; routes redirect to `/explore` (`router.tsx:42-64`) |
| `/statistics` ("Library" menu) | hide with the statistics route |
| `/concepts`, `/concepts/:id`, `/trends*` | hide when not shipped |
| `/memes/:id` curator parts: `ProfileEditor` (`MemePage.tsx:31`), description approve/reject and note editor (`MemeDetails.tsx:225-300`), flagged toggle, `PopularityBlock` set control, `MemeComicsBlock` (calls `/comics/by-image`, `/comics/addability`) | hide; a 404 from a hidden control must never surface as an error banner |
| `/search`, `/reply`, `/recommendations`, `/explore`, `/trending-memes`, `/upload`, `/memes/:id` core | stay |
| `/reply` | falls back to the `unavailable` state if the backend answers `available:false` or 404/405 (already handled) |
| `NotFoundButton` | stays (`POST /history/{id}/not-found` is K) |

Graceful failure when the build flag is missing: every hidden control should treat 404 as "feature off", not as an error. Today most pages `throw` on `!res.ok` (`HttpMemesApi.ts`), so the build flag is what keeps the UI clean.

### 3.3 Android (`AndroidClient/app/src/main/java/com/memebrowser/app`)

Calls to blocked endpoints (`data/api/MemeApiService.kt`): `markFlagged`/`unmarkFlagged` (`ui/detail/MemeDetailViewModel.kt:99`), `getFlagged` (`ui/flagged/FlaggedViewModel.kt`, Flagged screen), `approveDescription`/`rejectDescription` (`ui/detail/DescriptionsBottomSheet.kt`, `MemeRepository.kt:87`), `getStatistics` (`ui/statistics/StatisticsViewModel.kt`).
Everything else it uses stays: `api/images`, `meme/{id}`, `similar`, `descriptions` (read), `saves`, `uploads` (album sync and manual upload), `trends/*` (if shipped), `recommendations`, `bug-reports`, root `health`.
Built-in environments are hard-coded LAN addresses (`data/model/Environment.kt:13-31`, `192.168.1.41:8081-8083`); a prod entry is user-defined or a new built-in with a public hostname. **Proposal:** the app reads `/api/diagnostics/capabilities` once per environment and hides Flagged, Statistics and the flag/approve/reject controls on `stage=prod`; an older app against prod just sees 404 on those taps.
The album-sync epic uploads to whichever backend is selected: pointing it at prod means phone uploads land in the prod inbox and reach dev only through the pull (P4).

## 4. Backend runtime needs on prod

### 4.1 Packages, code, data

| Need | Evidence | Note |
|---|---|---|
| Python 3.11-slim, `Backend/requirements-backend.txt` (FastAPI, SQLAlchemy, asyncpg, pgvector, Pillow, pymorphy3 (+ru dicts), snowballstemmer, gunicorn, python-multipart...) | `Dockerfile.backend`, requirements header | **No torch, numpy, sentence-transformers, CLIP, opencv, YOLO.** `taxonomy/similarity.py` imports numpy but nothing in the backend imports it (`retrieval/similarity.py` reads JSON); `repository/ocr_text.py:44` imports numpy lazily in a batch-only method |
| Query-time lemmatisation | `rules.normalize` via `repository/ocr_lemmas.py:19` (`make_morph`) | pymorphy3 + dictionaries ship in the wheel set |
| Taxonomy YAML + `similarity.json` | `COPY batch/data/taxonomy` | must match the `meme_profiles.taxonomy_version` of the promoted data (S3 precondition) |
| `parse/`, `retrieval/`, `taxonomy/`, `rules/`, `repository/`, `config/`, `environments/` | Dockerfile | `environments/` is copied for the tracked settings YAML only; `.env.*` is excluded by `.dockerignore` |
| `batch/clusterize.py`, `run_tracking.py`, `registry.py`, `utils/{recut_cut,file_hash,comics_compose}.py` | Dockerfile comments | imported at module load by routers prod will not serve (`ingestion_service`, `admin_batch_service`, `recut_adjust_service`, `comics_*`); `file_hash.py` is also needed by uploads. Keeping them avoids touching imports; trimming is optional |
| `ai/`, `embeddingutils/`, `metrics/`, `graph/` | Dockerfile COPY | no import from `Backend`/`repository`/`retrieval` path found for `ai`, `embeddingutils`, `metrics` (`graph.uf` is used): dead weight in the image, harmless |
| Disk: `BASE_PATH` with the promoted files, `PATH_INGESTION_SOURCE` (writable, absolute, not inside `BASE_PATH` rules per `Environments.md`; the stated convention is `BASE_PATH/inbox`), `BUG_REPORTS_PATH` or `BASE_PATH/bug_reports`, `logs/` | `image_store.py`, `inbox_upload_service.py:158-164` | `BASE_PATH` is read at import; startup fails without it (`image_store.py:9`) |

**Image size (estimate from what was read, not measured):** python:3.11-slim base plus ~30 pure-Python/compiled wheels plus pymorphy dictionaries: on the order of a few hundred MB, versus several GB if torch were included. The `postgresql-client` and `curl` installs are small.

### 4.2 Does any reader route need an ML model at request time?

| Route | Needs | Verdict |
|---|---|---|
| similar (image, description, note, all) | stored vectors, pgvector cosine in SQL | none |
| text search `GET /images?q=` | lemma/tag/OCR tables, pymorphy3 | none |
| recommendations, concepts | stored vectors | none |
| upload by image | file stored; `validate_image_content` uses Pillow; **no embedding of the upload** | none |
| **meme-from-text search** | Ollama `parse.model` (qwen2.5:7b-instruct on metal and general) for the profile, Ollama `taxonomy.embedding_model` (bge-large, 1024-d) for the query vector | **Ollama** |
| profile PUT (curator) | Ollama embed | D on prod |

There is no text-to-image (CLIP text tower) search and no visual-search-by-upload route in `Backend/app/api`.

### 4.3 Ollama absent on the VPS

- `it` has no `parse.model` and answers `available:false` (2.4). A prod `general` or `metal` **with** `parse.model` but **without** a reachable Ollama does not fail: each search tries the parser (`parse.timeout_seconds` 5 s budget; connection refused is fast), the circuit breaker opens after 3 failures for 30 s (`parser_breaker`), the profile is a degraded one, and `lexical.only_when_degraded: true` switches the lexical source on (`environments/settings.yaml:90-102`, `retrieval/pipeline.py:234-245`). The page shows the degraded state; `degraded`, `fallback` are written to `search_history`.
- **Lost in lexical-only mode:** the situation-embedding candidates (the main signal, weights situation/emotion/stance/topic/tone are all profile-driven), the axis filters and facets derived from the parsed profile, and the parsed-profile editing in `/reply`. **Kept:** word overlap against `ocr_lemmas`/description lemmas (`repository/meme_retrieval.py:176-181`, `repository/ocr_lemmas.py`). Lexical costs ~250 ms p50, >1 s p95 (settings comment) and is per request when degraded.
- Because model-bound data (`meme_profile_situation_embeddings`, S3 Q6) is promoted, prod can only embed queries with the **same Ollama model name and digest** as dev. A different quantisation on the VPS would silently degrade ranking.
- **Options for the owner (open question 1):** (a) feature off on prod (remove `parse.model` in the prod overlay; the page shows unavailable, like `it`); (b) lexical-only by design (keep `parse.model`, nothing running; wasteful retries, so set a prod flag to skip the parser instead, a small code change); (c) Ollama on the VPS (qwen2.5:7b plus bge-large: the dev settings assume a GPU; CPU latency would break the 8 s p95 target; RAM in the several-GB range, unmeasured); (d) a remote Ollama: dev's workstation is not allowed by "prod never connects to the workstation", so this needs a third host.

## 5. Database roles on prod

All grants below are the **Proposal**. Prod has the full schema (equal alembic head, S3), so class X tables exist and are empty; grants decide who may touch them. PostgreSQL 18 is the version used in `Storage/docker-compose.yaml` and S3 (`pgvector/pgvector:pg18-trixie`); `MAINTAIN` needs PG 17 or later.

### 5.1 Roles

| Role | Used by | Login | Purpose |
|---|---|---|---|
| `memes_owner` | alembic, index DDL | yes, not reachable from the app host or the sync | owns all tables, sequences, extensions; runs migrations; only role with DDL |
| `backend_app` | the prod backend (`DATABASE_URL`) | yes | serve readers; writes only P tables |
| `sync_writer` | the promote tool, over the SSH tunnel | yes | writes class D tables and deletes retired images; no DDL |
| `sync_reader` | the pull (P4), drift report (P6), preconditions | yes | `SELECT` on P tables, `alembic_version`, catalogs |
| `ocr_readonly` (existing name on dev) | humans/agents | yes | SELECT everywhere; on prod prefer a stricter variant without `search_history` text (see below) |

Passwords and connection strings live outside the repo (CLAUDE.md rule); the roles are created by a SQL script in `Storage/` (P5), parameterised, run once by the VPS admin.

### 5.2 Grants per role (tables from S1)

Class D tables: `images`, `image_extras`, `ocr_texts`, `ocr_lemmas`, `embeddings`, `tags`, `image_descriptions`, `image_description_embeddings`, `description_lemmas`, `meme_profiles`, `meme_profile_values`, `meme_profile_situation_embeddings`, `description_notes`, `description_note_lemmas`, `description_note_embeddings`, `image_description_feedback`, `comics`, `comics_panels`, and (provisional) `concepts`, `concept_image_sets`, `concept_images`, `concept_text_sets`, `concept_texts`. Trends tables if shipped.

| Role | SELECT | INSERT / UPDATE | DELETE | Other |
|---|---|---|---|---|
| `backend_app` | all class D tables; **`popularity_absorptions`** (empty, read by trending/popularity/impressions); `statistics_snapshots` if the route stays; `alembic_version` not needed | `meme_saves`, `meme_impressions`, `search_history`, `search_history_tags`, `search_not_found`, `meme_text_ratings` (INSERT; UPDATE on `meme_text_ratings` for rating upsert and `forget_text`); `statistics_snapshots` INSERT/UPDATE only if kept | `meme_text_ratings`, `search_history` (cascades to `search_history_tags`) | `USAGE` on `meme_impressions_id_seq`; **no** grant on X tables except the one SELECT above, none on `images` writes, none on `batch_runs` |
| `sync_writer` | all D tables, `alembic_version` | INSERT/UPDATE on all class D tables | DELETE on D tables incl. `images` (retire) | `USAGE, UPDATE` on the integer sequences of D tables for `setval` after copy (`concepts*`, S3 Q6); `MAINTAIN` on D tables (REINDEX/ANALYZE without ownership, PG 17+; `CREATE`/`DROP INDEX` still needs the owner, so an HNSW drop-and-rebuild variant needs the owner role run locally: open question 4); `pg_advisory_lock` needs no grant; **no** access to P tables, no `TRUNCATE`, no DDL |
| `sync_reader` | P tables, `alembic_version`, `pg_catalog` | none | none | used by the dev-initiated pull under a REPEATABLE READ snapshot (S3 Q7) |
| `memes_owner` | all | all | all | runs `alembic upgrade head`, `CREATE EXTENSION` (vector, pg_trgm: `6fc209b37e8b` runs `CREATE EXTENSION IF NOT EXISTS pg_trgm`, `baseline.sql` runs `vector`) |

Notes:
- `ON DELETE CASCADE` from `images` into `meme_text_ratings` (the only P table with an FK to images, S3 backlog) runs with the privileges of the table owner, so `sync_writer` needs no DELETE on `meme_text_ratings`. Not verified in this spike: confirm in the S3-style throwaway container before relying on it (same reason the pull must keep ratings of retired images).
- `backend_app` reads `popularity_absorptions` only because of `image_repository.py:36-50` and `repository/popularity.py`; if the owner decides to ship it (open question 3) it simply becomes a D table in the registry.
- `search_history` holds reader messages: `ocr_readonly` on prod should be created without it, or never handed to agents (CLAUDE.md rule on subagents and live databases applies to prod a fortiori, and prod credentials are never in the repo).
- Defence in depth: the section 2.5 test 3 is what proves `backend_app` cannot write what the gate blocks.

### 5.3 Migrations on prod

- `alembic` is **not** in `Backend/requirements-backend.txt` and not in the backend image; `Storage/alembic/env.py` imports `Storage.config` (needs `DATABASE_URL` from the environment) and converts `postgresql+asyncpg` to a sync URL (psycopg2 is in the backend requirements). So migrations cannot run from the running backend container.
- **Proposal:** a separate one-shot migration image/venv (`Dockerfile.migrate` or a `docker run --entrypoint` of the backend image with `pip install alembic`), run by the **VPS admin with `memes_owner`**, never from dev over the sync tunnel. The sync role cannot do DDL by design.
- **Deploy order** (the S3 schema gate makes the order a contract; promote refuses on unequal heads, S3 Q6):
  1. Dev: write the migration, merge, release the backend image tagged with the code revision.
  2. Prod: pull the new image but keep the old container running; run `alembic upgrade head` with `memes_owner`. Only **additive, backward-compatible** migrations are allowed between steps 2 and 3 (new nullable columns, new tables); a breaking migration needs a short maintenance window.
  3. Prod: restart the backend on the new image (graceful gunicorn reload).
  4. Dev: `promote` (heads now equal on both ends) then the file sync.
  The reverse (backend new, migration not run) fails fast at the first query touching the missing column; the promote precondition (equal heads) catches it for sync, nothing catches it for readers, hence step ordering.
- Also compare on every deploy: `server_version_num` major, `vector` and `pg_trgm` versions (S3 Q6 list), and the code revision the backend reports (a `revision` field in `/api/diagnostics/capabilities` makes this checkable by the promote tool, which today has no way to read the prod code revision: S3 flags "git SHA, not in DB").

## 6. Per-environment prod configuration

### 6.1 Where it lives (Proposal, least invasive)

| Layer | File | Content |
|---|---|---|
| common | `environments/settings.yaml` | unchanged |
| env | `environments/settings.<env>.yaml` | unchanged (dev) |
| **prod stage, common** | `environments/settings.prod.yaml` (new, tracked) | `stage: prod`, `scheduler.enabled: false`, anything shared by the three prod instances |
| **prod stage, per env** | `environments/settings.<env>.prod.yaml` (new, tracked, only where needed) | e.g. `parse.model` removed/changed for prod `general`; `FRONTEND_ORIGIN` |
| secrets | `environments/.env.<env>.prod` on the VPS (gitignored, never in the image; `.dockerignore` already excludes `.env.*`) | `DATABASE_URL` (backend_app), `BASE_PATH`, `PATH_INGESTION_SOURCE`, `BUG_REPORTS_PATH`, `ALTERNATIVE_FRONTEND_ORIGIN`, `APP_ENV`, `APP_STAGE`, `OLLAMA_HOST` if used |

`_build()` becomes `settings_files=[base, env, (stage files if prod)]`. Because `load_env()` reads `.env.<name>`, the batch tooling would need `.env.<name>.prod` selection too; prod never runs batch jobs, so only the backend path matters.

### 6.2 Keys that must differ

| Key | Dev | Prod | Why |
|---|---|---|---|
| `APP_STAGE` | unset (`dev`) | `prod` (baked into the image) | gate |
| `scheduler.enabled` | `true` (all three overlays) | `false` | no jobs on prod |
| `DATABASE_URL` | `ocr` owner-like user | `backend_app` | least privilege |
| `BASE_PATH` | workstation drive | VPS data volume | the promoted active corpus only (S2: ~3.3 GB general) |
| `PATH_INGESTION_SOURCE` | the dev inbox read by ingestion | VPS inbox, **upload landing only**; dev pulls files from it (S4/P4) | the backend writes here (`inbox_upload_service`); needs a quota (the 1 GiB free-space check exists, `Environments.md`) |
| `BUG_REPORTS_PATH` | default `BASE_PATH/bug_reports` | explicit path outside `BASE_PATH` | `BASE_PATH` is a promote-managed allow-listed tree; keep reader files out of it |
| `general.frontend_origin` / `ALTERNATIVE_FRONTEND_ORIGIN` (CORS, `main.py:114-136`) | `http://localhost:517x` | the prod web origin(s); `localhost` entries stay hard-coded in `main.py:132-133` | tighten for prod; consider making `localhost` dev-only |
| `parse.model`, `parse.ollama_url`, `OLLAMA_HOST`, `meme_profiles.*` | Ollama on the workstation | per open question 1; `meme_profiles.*` is batch-only and irrelevant on prod | `meme_profiles.num_ctx` must keep equalling `parse.num_ctx` if the same model runs |
| `popularity.impressions.enabled` / `window_days` | `true` / 30 | `true` | impressions are the point of prod; the sub-key is validated at startup (`validate_impression_settings`) |
| `popularity.search_outcomes.enabled` | `true` | `true` | not-found reports |
| `search.meme_from_text.query_log.enabled` | `true` | `true`, owner confirms (message text is stored; a retention policy is an open item in `admin-permissions-todo.md`) | |
| Statistics | snapshot + hourly job | route disabled | 1.1 |
| gunicorn workers / pool | `-w 4`, pool 10+20 | `-w 2..4` with `pool_size` reduced so workers x (pool+overflow) fits `max_connections` (or PgBouncer) | headline 6 |
| proxy headers | n/a | `--forwarded-allow-ips` set to the proxy, so `request.client.host` is the real client for the rate limiters | `uploads.py:107`, `bug_reports.py` |

### 6.3 Ports and hostnames convention (no hosts invented)

The dev port table (`Environments.md`: 8081-8083 backends, 5173-5175 frontends, 5432/5434/5436 databases) stays for dev only. **Proposal:** on the VPS each prod instance listens on `127.0.0.1` only (one container per env, one Postgres database per env on one cluster or one Postgres per env: open question 5), the public side is a reverse proxy with TLS routing by hostname or path prefix, and the Postgres port is never exposed publicly (reached by the sync over `ssh -L`, as in S3). Use the same numbering with a different leading digit pattern recorded in `Environments.md` by P7 (for example the dev port + 1000 for loopback binds), so a port number always identifies the environment and the stage. Hostnames, TLS and the proxy product are owner decisions.

## 7. Operational surface

### 7.1 Backups of prod-origin data

The only copy of reader activity lives on the VPS until a pull runs (`meme_saves`, `meme_impressions`, `search_history*`, `search_not_found`, `meme_text_ratings`, plus files in the prod inbox and `bug_reports/`). Class D is rebuildable from dev (a full promote reseeds prod), so it needs no backup.
**Proposal:** nightly `pg_dump` of the P tables only (small today: a few MB, S2), retained on the VPS and copied off by the same dev-initiated SSH job that pulls, plus a filesystem snapshot of the inbox and `bug_reports/`. A full-database dump is optional. The pull itself (S3 Q7, mirror replace on dev) is not a backup of prod: if dev mirrors an empty prod after a failed prod restore, dev's last good mirror is lost, so keep dated dumps of dev's P tables too (cheap) and have the pull refuse to replace non-empty dev data with an empty source.

### 7.2 Logs and the user's text

- The application code never logs message text: `history_recorder` logs the type name only (`history_recorder.py:21-25`), ratings and profile routers map errors to fixed details (`meme_text_ratings.py:27-32`), the search route logs `type(error).__name__` (`meme_from_text.py:44-49`), `error_handlers.py` strips 422 echoes under `/api/meme-from-text` (CLAUDE.md), and `retrieval/pipeline.py:143` logs counts and timings only. Confirmed by reading; no grep for every `logger` call was exhaustive.
- **Gaps outside the app code:** `GET /api/images?q=<text>` puts the search text in the URL. uvicorn's access log (dev) and any reverse-proxy access log record the full request line. gunicorn in the Dockerfile CMD sets no `--access-logfile`, so the container's own access log should be off (verify that `UvicornWorker` honours it); the **proxy** must be configured to log the path without the query string for `/api/images`. A 5xx traceback from the DB layer on the lexical path can carry lemmas as statement parameters; the meme-from-text route already catches this, `GET /api/images` does not (not verified end to end).
- The existing gunicorn/uvicorn formatter in `main.py:48-76` adds a pid and logger name only.

### 7.3 Health checks

`GET /health` (root, SELECT 1; used by the image HEALTHCHECK on port 8000) and `GET /api/diagnostics/health` (`{backend, database}`) are both K and dependency-light. Add to the prod monitoring list: disk free on `BASE_PATH` and the inbox volume (uploads return 507 under 1 GiB), Postgres connection count (headline 6), and the pull's `last_success` age. A readiness check for "schema head equals the image's expected head" would catch the deploy-order failure in 5.3 (needs the head read from `alembic_version` and the head of the shipped `Storage/alembic/versions`).

### 7.4 What a promote must invalidate in the backend

| State | Where | Keyed by data? | After a promote |
|---|---|---|---|
| meme-from-text result cache (50 entries, TTL `cache_ttl_seconds` 120 s; degraded answers 10 s) | `retrieval/pipeline.py:29-33,279-299`, per worker | fingerprint = text, parser version, taxonomy version, config hash; **not data** | stale for at most 120 s per worker; acceptable, no action. If a promote must be visible at once: restart (cheap) |
| query-vector cache (200 entries) | same file | text + model | data-independent; none |
| `lru_cache`: `get_query_parser`, `get_pipeline`, `cached_taxonomy_payload`, `load_similarity`, `_get_morph`, `search_ranking` weights | `Backend/app/services/*`, `repository/*` | config / YAML only | only change with a **code or settings deploy** (restart), not with a promote |
| taxonomy `Cache-Control: public, max-age=300` | `meme_from_text.py:58` | YAML | same |
| image bytes `Cache-Control: public, max-age=31536000, immutable` | `cache.py:7-11` | by URL = image id | safe as long as a file under an id is never replaced. A retire-and-reuse of an id cannot happen (UUID), but a **replaced file for the same id** (recut `adjust` rewrites cut files on dev; `fix_image_formats` changes `filename`/`content_hash` in place) would be served stale by browsers and CDNs for a year. Rule for P3: promote a changed file as a new id, or never change bytes under an id |
| `statistics_snapshots` | DB row | | route disabled on prod |
| rate limiters, circuit breaker | per-worker memory | | irrelevant to promotes |

So: **no backend restart is needed for data-only promotes**; restart (rolling) only for a code, settings or taxonomy change. A promote that bumps `taxonomy_version` is a code deploy by definition (S3 precondition). No in-process cache holds image lists or counts. The promote tool needs no call into the backend (a "promote announced" hook is unnecessary), which keeps prod's surface free of a writer API.

## Uncertainties (not settled by the code)

1. Whether `GET /api/images/flagged` and the other removed literals reach a 500 via `Image.id == "flagged"`; not run.
2. Whether `UvicornWorker` under gunicorn without `--access-logfile` emits any access line; unverified.
3. `sync_writer` DELETE cascade through `meme_text_ratings` and `MAINTAIN` covering the exact REINDEX variant S3 chooses; PG 18 documentation recollection, to be tested in a container.
4. The actual image size, and Ollama on a CPU VPS: not measured.
5. Whether the comics `by-image` lookup and the concepts and trends pages have any reader value on prod (depends on S1 open questions 6, 7).
6. Whether `backend_api.md`, `docs/data-flow.md`, `docs/image-derived-data.md` need rows for the `capabilities` endpoint and the stage; left to P7.

## Open questions for the owner (only what changes what gets built)

1. **Meme-from-text on prod:** feature off (like `it`), lexical-only by design, Ollama on the VPS, or a third host? Decides the prod overlay, the VPS size and whether a "skip parser" flag is built (4.3).
2. **Statistics on prod:** drop the page and route (proposal), or serve a promoted dev snapshot? Decides one extra D table or none.
3. **`popularity_absorptions`:** ship it as class D (append-only, tiny: 1 row on general) so the 30-day trending window, the popularity breakdown and the impression folding work on prod, or leave `window=30d` and the breakdown empty and hide them on prod? This reverses an S1 decision if shipped.
4. **Index rebuild privilege:** may the promote run `REINDEX` with `MAINTAIN` only (no DROP/CREATE INDEX), or do you accept a second, owner-level step run locally on the VPS for HNSW drop-and-rebuild? Depends on S3's final index strategy.
5. **One Postgres cluster with three databases, or three clusters,** on the VPS? Affects `max_connections`, roles (roles are cluster-wide) and the backup job.
6. **Concepts and trends on prod:** ship or hide (S1 Q6, Q7)? Decides which of 9 routes are K.
7. **Public exposure:** is prod reachable by anyone, or only by named people (basic auth or IP allow-list at the proxy)? With no app-level auth this is the only access control; it changes whether reader write routes need extra rate limits.
8. **Message-text retention on prod** (`search_history`, `meme_text_ratings.query_text`): keep until pulled, or prune after a successful pull? (Extends S1 Q5.)
9. **Android:** should the app read stage capabilities (new endpoint + app release) or is "older app sees 404 on blocked taps" acceptable?

## Proposed work items

| Tracker project | Item |
|---|---|
| **P1 sync registry** | Add `stage_policy` columns beside origin/ship/order: for each table, which role may write it on prod (reuses the S1 classes); the registry test also checks `backend_app` grants are a subset of {P tables, R}. |
| **P2 promote data** | Connect as `sync_writer` only; refuse if the connected role is `backend_app` or owner; assert code revision via the new capabilities endpoint; no call into the backend after a promote. |
| **P4 pull** | Connect as `sync_reader`; document the "Forget text" delete race (anyone can erase a message's log between pulls); refuse to mirror-replace a non-empty dev P set with an empty source; pull prod inbox files and `bug_reports/`. |
| **P5 prod provisioning and hardening** | (a) `APP_STAGE` loader + `settings.prod.yaml` + baked `ENV APP_STAGE=prod`; (b) `stage_policy.py` + `StageGate` middleware + conditional `include_router` + docs off; (c) roles SQL (`Storage/prod_roles.sql`) and the write-set integration test; (d) migration image and runbook with the deploy order; (e) prod gunicorn/pool sizing and proxy-header config; (f) proxy log without query string; (g) backup job for P tables. |
| **P5 (new)** | `GET /api/diagnostics/capabilities` (stage, features, code revision) with schema in `shared/schemas/` and regenerated TS/Kotlin/Python types. |
| **P5 / frontend (new item, call it P5b)** | `VITE_STAGE` build flag; hide Cleanup and Admin menu groups, statistics, curator controls; redirect hidden routes; treat 404 on hidden controls as "feature off". |
| **P5 / Android (new item, P5c)** | Hide Flagged, Statistics, flag toggle and description approve/reject on `stage=prod`; a prod environment entry. |
| **P3 files** | Rule: never change bytes under an existing image id (immutable 1-year cache); promote a changed file as a new id; prod inbox and `bug_reports/` live outside the promote-managed `BASE_PATH` allow-list. |
| **P6 verification** | Add `GET /health` + capabilities revision + schema-head equality to the drift report; check `backend_app` grants against the registry. |
| **P7 docs** | `Environments.md` prod port/host convention; `backend_api.md` (capabilities endpoint, prod 404 matrix); `docs/security/admin-permissions-todo.md` (prod mitigation, history route); CLAUDE.md note that `Dockerfile.backend` means `APP_STAGE=prod`. |
| **Spike follow-up (small)** | Throwaway-container check of the 5.2 notes: `MAINTAIN` + REINDEX, cascade without DELETE on `meme_text_ratings`, gunicorn access log, `/images/flagged` fall-through. |
