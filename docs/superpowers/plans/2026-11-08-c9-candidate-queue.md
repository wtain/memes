# C9 Candidate Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A batch job stores suggested comics groups (images saved seconds apart that look alike), an API lists, accepts and dismisses them, and a page lets the owner act on them.

**Architecture:** New tables `comics_candidates` and `comics_candidate_images` (one migration after `f3a8c1d6b952`); a pure detector module plus a run-tracked batch job `detect_comics_candidates`; a repository `repository/comics_candidates.py`; a backend service and router endpoints under `/api/comics/candidates`; a React page. Accept reuses the existing comics repository/service for create and add-panels.

**Tech Stack:** SQLAlchemy 2 async + Alembic + Postgres/pgvector, FastAPI, React 19/Vite/vitest, shared JSON schemas with generated TS/Kotlin/Python types.

**Spec:** `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-08-c9-candidate-queue-design.md` (read it in full: defaults, rulings and the API contract are there and are not repeated below).

## Global Constraints

- Tests use only `postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test`. Never touch a live database. Backend, `tests/integration/` (whole root), `batch/tests/`, `tests/docs/`, frontend run as separate commands; set `PYTHONIOENCODING=utf-8` for Backend pytest. The worktree has no venv: use `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe`.
- This harness rejects compound shell commands in the worktree: run plain separate commands.
- Repositories never commit (callers do). ORM models only in `Storage/models.py`. Settings keys `comics_candidates.*` (`gap_seconds`, `min_distance`, `max_distance`, `max_group`, `max_candidates_per_run`) are required in `environments/settings.yaml` (common) and read through `settings.COMICS_CANDIDATES`; follow how `settings.COMICS`/`settings.RECUT` are declared and tested (`batch/tests/test_comics_settings.py`).
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01R83wsLxHmwhVYSwcWknqmV`.
- `backend_api.md`, `shared/schemas/` and the three generated trees (TS, Kotlin, Python; see CLAUDE.md "Type generation") stay in sync. Batch conventions: registry entry, `docs/batches/inventory.md` row (and its counts), CLAUDE.md batch list entry, use-case note; `tests/docs/` stays green.

## Review Focus

- Two images 2 s apart with distance 0.0998 and different sizes form one group; sizes never matter.
- A group is never inserted twice (re-run) and never when it shares 2+ images with a dismissed or accepted candidate.
- A group of more than `max_group` images (a batch save) is dropped, not truncated.
- Epoch-shape and `YYYYMMDD_HHMMSS` timestamps are never compared with each other; a 13-digit run inside a longer digit string is not an epoch.
- Accepting a candidate whose image became flagged, inactive or part of a comics leaves everything unchanged (409, no half-created comics).
- A subset accept needs at least 2 images in the group's set; unknown ids are a 422.

---

### Task 1: Tables, models, repository

**Files:**
- Create: `Storage/alembic/versions/<new id>_add_comics_candidates.py` (down_revision `f3a8c1d6b952`), `repository/comics_candidates.py`, `tests/integration/test_comics_candidates_repository.py`, `tests/integration/test_comics_candidates_migration.py`
- Modify: `Storage/models.py`, `tools/rollout_preflight.py` (EXPECTED columns), `tests/integration/` fixtures only if the schema is built from models (follow `test_comics_migration.py` and how the test DB gets its schema)

**Interfaces:**
- Produces ORM `ComicsCandidate` (columns per spec: `id`, `key` unique, `state` check in open/accepted/dismissed, `detector_version`, `max_gap_seconds`, `max_distance`, `comics_id` FK `comics.id` ON DELETE SET NULL, `created_at`, `decided_at`) and `ComicsCandidateImage` (`candidate_id` cascade, `image_id` cascade, `position`, PK both ids), constants `CandidateState.OPEN/ACCEPTED/DISMISSED`.
- Produces `ComicsCandidatesRepository(session)` with: `candidate_key(image_ids) -> str` (module function, md5 of the sorted id strings); `insert_group(image_ids_in_order, max_gap_seconds, max_distance, detector_version) -> uuid.UUID | None` (None when the key exists); `overlaps_decided(image_ids) -> bool` (shares 2+ images with a `dismissed`/`accepted` candidate); `list_open(offset, limit) -> list[CandidateRow]` (open candidates whose every image is active, not flagged, in no comics panel and still exists; ordered by `max_distance` asc, `id`; each row carries its images in order with `id`, `filename`, `width`, `height`); `get(candidate_id, lock=False)`; `decide(candidate_id, state, comics_id=None)` (sets state, `decided_at`, `comics_id`; raises `CandidateError("candidate_decided")` if not open, `CandidateError("not_found")`).

- [ ] **Step 1:** Write failing integration tests: migration shape/downgrade (mirror `test_comics_migration.py`), `insert_group` + duplicate key returns None, `overlaps_decided` for a shared pair, a superset and a single shared image (false), `list_open` hides a group once an image is flagged/inactive/in a comics/deleted and orders by `max_distance`, `decide` twice raises `candidate_decided`.
- [ ] **Step 2:** Run them (`DATABASE_URL=...ocrdb_test ... -m pytest tests/integration/test_comics_candidates_*.py`), expect FAIL.
- [ ] **Step 3:** Add models, migration, repository; add the two tables' columns to the preflight `EXPECTED`.
- [ ] **Step 4:** Run the new tests and the whole `tests/integration/` root; expect all pass.
- [ ] **Step 5:** Commit `feat(comics): candidate tables and repository (C9)`.

### Task 2: Detector and batch job

**Files:**
- Create: `batch/utils/comics_candidates.py` (pure: timestamp parsing and grouping), `batch/detect_comics_candidates.py`, `batch/tests/test_comics_candidates_detector.py`, `batch/tests/test_detect_comics_candidates_main.py`, `tests/integration/test_detect_comics_candidates.py`, `docs/batches/use-cases/comics-candidates.md`
- Modify: `environments/settings.yaml` (+ per-environment overrides only if others do so), `config/settings.py` only if a typed accessor is the convention, `environments/batch_registry.yaml`, `docs/batches/inventory.md` (row and counts), `CLAUDE.md` batch list entry, `batch/tests/test_comics_settings.py` or a sibling for the new keys

**Interfaces:**
- Consumes Task 1: `ComicsCandidatesRepository.insert_group`, `overlaps_decided`.
- Produces in `batch/utils/comics_candidates.py`: `parse_timestamp(filename: str) -> tuple[str, float] | None` (shape `"datetime"` or `"epoch"`, seconds as a float; `YYYYMMDD_HHMMSS` anywhere in the name with year 2000-2099 and a valid date/time, or a run of exactly 13 digits between 2005 and 2035 as epoch milliseconds); `group_candidates(items, distance_fn, gap_seconds, min_distance, max_distance, max_group) -> GroupingResult` where `items` is a list of `(image_id, filename, shape, ts)`, `distance_fn(a_id, b_id) -> float | None` is injected (the DB lookup lives in the job), and the result holds `groups` (each: ordered image ids, max_gap, max_distance), `pairs_linked`, `dropped_oversized`.
- Produces `batch/detect_comics_candidates.py` `main(trigger=None, run_id=None)` like `detect_recut_crops.py` (KIND `detect_comics_candidates`, `tracked_run`/`finish_existing_run`, `SimpleMetricsListener` stats named in the spec), `DETECTOR_VERSION = 1`.

- [ ] **Step 1:** Write failing unit tests: `parse_timestamp` (both shapes; invalid month/hour; 14-digit and 12-digit runs; no timestamp); `group_candidates`: the known pair (2 s, distance 0.0998, different sizes) forms one group; distance below `min_distance` or above `max_distance` links nothing; gap above `gap_seconds` links nothing; chain A-B-C forms one group with max edge values; a component larger than `max_group` is dropped and counted; shapes are never mixed; ordering by timestamp then filename.
- [ ] **Step 2:** Run, expect FAIL. Implement the pure module; run, expect PASS.
- [ ] **Step 3:** Write failing integration tests for the job on `ocrdb_test`: eligible-image selection (active, unflagged, in no comics, has embedding, parsable name), distances taken from `embeddings` with pgvector (`<=>`) only for pairs inside the time window (never an all-pairs query), insert, re-run inserts nothing (`groups_skipped_existing`), a group overlapping a dismissed/accepted candidate is skipped (`groups_skipped_decided`), `max_candidates_per_run` caps insertions, nothing else in the database changes.
- [ ] **Step 4:** Implement the job; settings, registry, inventory, CLAUDE.md, use-case note. Run `batch/tests/`, `tests/docs/` and the whole `tests/integration/` root separately; expect PASS.
- [ ] **Step 5:** Commit `feat(comics): detect_comics_candidates batch job (C9)`.

### Task 3: Service, API, schemas

**Files:**
- Create: `Backend/app/services/comics_candidates_service.py`, `Backend/tests/test_comics_candidates_endpoints.py`, `tests/integration/test_comics_candidates_service.py`, schemas `shared/schemas/comicscandidate.schema.json`, `comicscandidateimage.schema.json`, `comicscandidatepage.schema.json`, `comicscandidateacceptrequest.schema.json`
- Modify: `Backend/app/api/comics.py` (routes declared **before** `/{comics_id}`), `Backend/app/main.py` only if wiring needs it, `backend_api.md`, generated trees (TS `Frontend/memes-frontend/src/types/generated/all.d.ts`, Kotlin, Python under `Backend/app/types/generated/`), `Frontend/memes-frontend/src/api/MemesApi.ts` + `http/HttpMemesApi.ts` + `src/test/mockApi.ts` (methods `listComicsCandidates(offset, limit)`, `acceptComicsCandidate(id, imageIds?)`, `dismissComicsCandidate(id)`)

**Interfaces:**
- Consumes Task 1 repository; the existing `ComicsService` create/add-panel path and `describe`/error mapping in `comics_service.py` (`comics_service.py:36` status mapping) and C6's naming rule (`Comics <first file name>`, retry once with the 8-char id prefix on `name_taken`; the server-side twin of `Frontend/.../comicsNaming.ts`, put the helper next to `repository/comics_name.py`).
- Produces `GET /api/comics/candidates?offset&limit` -> `ComicsCandidatePage {items: ComicsCandidate[], hasMore}`; `ComicsCandidate {id, maxDistance, maxGapSeconds, images: ComicsCandidateImage[]}` (`ComicsCandidateImage {id, filename, width, height}`); `POST /api/comics/candidates/{id}/accept` body `{imageIds?: string[]}` -> `ComicsDetail` (201); `POST /api/comics/candidates/{id}/dismiss` -> 204. Errors: 404 `not_found`, 409 `candidate_decided`, 409 with the existing image-not-addable body for an ineligible image, 422 for a subset with fewer than 2 ids or ids outside the group.

- [ ] **Step 1:** Write failing endpoint and service tests covering every case in the spec's API paragraph, including the accept transaction (a failure after create leaves no comics and the candidate open) and the route order (`/candidates` is not captured by `/{comics_id}`).
- [ ] **Step 2:** Run, expect FAIL; implement; regenerate the three type trees and verify `git diff` on generated TS is only the new types; update `backend_api.md`.
- [ ] **Step 3:** Run Backend `pytest`, the whole `tests/integration/` root, `tests/docs/`; expect PASS.
- [ ] **Step 4:** Commit `feat(comics): candidate queue API (C9)`.

### Task 4: Suggestions page

**Files:**
- Create: `Frontend/memes-frontend/src/pages/ComicsCandidatesPage.tsx` and `.test.tsx`
- Modify: `src/app/router.tsx` (route `/admin/comics/candidates`, before `/admin/comics/:id`), `src/pages/ComicsListPage.tsx` (link "Suggestions"), `src/app/AppLayout.tsx` only if the comics nav lives there

**Interfaces:**
- Consumes Task 3 API methods on `MemesApi`. Produces the page described in the spec: rows of thumbnails with file name and size, each thumbnail switchable off, "Make comics" (accept with the enabled ids, disabled below 2; then navigate to `/admin/comics/<id>`), "Dismiss" (removes the row), "Show more", empty state naming the job, error text via `describeComicsError`.

- [ ] **Step 1:** Write failing vitest tests: loads and shows rows; toggling a thumbnail changes the ids sent; Make comics sends the subset and navigates; fewer than 2 enabled disables it; Dismiss removes the row and calls the API; a 409 shows the reason and keeps the row; empty state; Show more appends.
- [ ] **Step 2:** Implement; run `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`; expect PASS.
- [ ] **Step 3:** Commit `feat(comics): suggestions page (C9)`.

### Task 5: Rollout, docs, tracker

**Files:**
- Modify: `docs/batches/use-cases/comics-rollout.md` (new section for the C9 migration: revision id, tables, order, same per-environment steps, note the job is run by the owner after the upgrade), `docs/batches/use-cases/comics-build.md` or `comics-candidates.md` cross-links, `docs/superpowers/specs/2026-11-02-multi-image-comics/tracker.md` (C9 row; follow-up "measure precision from the decisions"; mark S2 `done` with outcome), design Status.

- [ ] **Step 1:** Make the edits; run `tests/docs/`.
- [ ] **Step 2:** Commit `docs(comics): C9 rollout and tracker`.
