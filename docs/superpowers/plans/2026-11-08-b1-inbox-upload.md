# B1 Inbox Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /api/uploads` writes sanitised-name files atomically into `PATH_INGESTION_SOURCE`, reporting `accepted` / `duplicate`.

**Architecture:** Router (validation, DI) -> `InboxUploadService` (sanitise, stage, lock, duplicate logic) -> `ImageRepository.existing_content_hashes`.

**Tech Stack:** FastAPI, SQLAlchemy async, pytest (Backend mocked DB; one integration test on `ocrdb_test`).

**Spec:** docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-b1-inbox-upload-design.md

## Global Constraints

- Limits unchanged: 20 MB, 50 files per request, rate limit, same rejection reasons.
- No fallback to `incoming/`; unset inbox is a 503, not an import-time crash.
- Repository never commits; no migration.
- Backend tests run from `Backend/` alone; integration tests only against `ocrdb_test`.

## Review Focus

- Two concurrent requests with the same name, different content: distinct names, no overwrite (Task 3).
- Two identical files in one request: second is `duplicate` (Task 3).
- Unicode name with forbidden characters and a very long Cyrillic name (Task 1).
- Failure mid-request leaves no staged file (Task 3).
- DB down: 503, nothing written (Task 3).

### Task 1: Name sanitising

**Files:** Create `Backend/app/services/inbox_upload_service.py`; test `Backend/tests/test_inbox_upload_service.py`.
**Produces:** `sanitize_filename(raw: str | None, ext: str) -> str`.

- [ ] Write tests: `../../x.jpg`, `a/b.jpg`, `a\b.jpg` -> `x.jpg`/`b.jpg`; `CON.jpg` -> `_CON.jpg`; `.hidden.png` -> `hidden.png`; `Мем 1.JPG` -> `Мем 1.jpg`; `a<b>:c?.png` -> `a_b__c_.png`; trailing dots/spaces; control chars and NUL; 300-char Cyrillic stem -> UTF-8 stem <= 200 bytes; empty stem -> `upload-xxxxxxxx.png`.
- [ ] Run, expect fail (import error). Implement per spec steps 1-8. Run, expect pass.

### Task 2: Repository method

**Files:** Modify `Backend/app/repositories/image_repository.py`; test `tests/integration/test_backend_image_repository.py`.
**Produces:** `ImageRepository.existing_content_hashes(hashes: Collection[str]) -> set[str]`.

- [ ] Integration test: images with statuses active, pending, rejected, cut_proposed and a NULL hash; query a mix of known/unknown hashes and an empty collection; assert exact subset.
- [ ] Run against `ocrdb_test`, expect fail (AttributeError). Implement (`IN`, no status filter, empty -> empty set). Pass.

### Task 3: Service and router

**Files:** Modify `Backend/app/services/inbox_upload_service.py`, `Backend/app/api/uploads.py`, `Backend/app/services/image_store.py`; rewrite `Backend/tests/test_uploads_endpoints.py`.
**Produces:** `InboxUploadService(repo).store(candidates) -> list[StoredFile]`, `InboxUnavailableError`, `DuplicateCheckUnavailableError`, `InboxWriteError`; router dependency `get_inbox_upload_service`.

- [ ] Write endpoint tests (tmp_path inbox via patched `settings`, fake repository through `dependency_overrides`): accepted keeps name and bytes; collision suffix; duplicate in inbox; duplicate in library; nothing written for duplicates (inbox and `.uploading` empty); atomicity (patch `os.replace` to inspect: inbox empty before replace; `hash_incoming_files` ignores `.uploading`); same-content twice in a request; concurrent same-name different-content requests via threads; 503 unset/missing/unwritable; 503 DB failure; 500 write error with staging cleaned; stale staging (>1 h) removed; old rejection tests; `total_accepted` counts duplicates; sha256 equals `sha256_file`.
- [ ] Run, expect fail. Implement service and router; remove `save_incoming`/`INCOMING_PATH`. Run Backend suite, expect pass.

### Task 4: Schema, generated types, docs

**Files:** `shared/schemas/uploadedfile.schema.json`, the three generated trees, `backend_api.md`, `docs/superpowers/specs/upload-endpoint.md`, `docs/batches/use-cases/ingestion.md`, `environments/Environments.md`, other `INCOMING_PATH` mentions.

- [ ] Edit schema description; run the three generators; check `git diff` only shows the description.
- [ ] Update docs; run `pytest tests/docs/`; web `tsc -b`, `eslint src/`, `vitest run` if the web page is touched.
