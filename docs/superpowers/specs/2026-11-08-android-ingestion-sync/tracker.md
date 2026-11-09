# Android album sync into the ingestion inbox: project tracker

Status: approved
Originates from: docs/superpowers/specs/2026-11-08-android-ingestion-sync/README.md

Created 2026-11-08. Each project has its own design spec (written when the project is picked up, named
`YYYY-MM-DD-<name>-design.md` and linked below), then an implementation plan, then implementation. Project status uses the spec
lifecycle values (`draft`, `approved`, `planned`, `implementation`, `done`), with `not started` before a spec exists. Spikes and
deep dives use `open`, `in progress`, `done` or `dropped`.

## Working rules

- **Worktree:** `.claude/worktrees/android-ingestion-sync-epic`, branch `worktree-android-ingestion-sync-epic`. Another epic may be
  running in parallel: sync from local `main` before starting a project (`git merge --ff-only main` in the worktree).
- **Merging to `main` and every database migration need the owner's explicit confirmation**, per project. Nothing is merged or
  migrated otherwise. No migration is expected in this epic.
- **Ambiguities** are brainstormed with the owner, not guessed.
- **Development mode** is chosen per project by size and impact: inline for small, contained changes; subagent-driven for
  multi-task or cross-area work.
- **Review:** one review iteration per project by a fresh subagent whose prompt names the area and axis, saved to
  `docs/superpowers/reviews/`. Axes: backend (security of file handling, correctness against ingestion, maintainability);
  Android (readability, code smells, lifecycle and concurrency, maintainability); security review for the upload path.
- **Ports:** never bind the metal, general or it ports; use a free port for any temporary backend and the `ocrdb_test` database.
- **Live databases:** read-only role only, never handed the main `DATABASE_URL`.

## Projects

| ID | Project | Delivers | Depends on | Status | Spec / plan |
|---|---|---|---|---|---|
| A0 | Epic charter and tracker | This folder | none | done (approved 2026-11-08) | `README.md`, `tracker.md` |
| B1 | Inbox upload endpoint | `POST /api/uploads` writes to `PATH_INGESTION_SOURCE`: filename sanitising and preservation, collision suffix, atomic staging write, per-file outcome (`accepted`, `duplicate`, `rejected`), `INCOMING_PATH` retired, shared schema and generated Kotlin/TS/Python types, `backend_api.md`, tests | S1 | done (2026-11-08; review and fixes done, not merged). Verified: Backend suite 884 passed. Not verified: real inbox, OneDrive link/rename, backend account rights (E1) | `2026-11-08-b1-inbox-upload-design.md`, plan `docs/superpowers/plans/2026-11-08-b1-inbox-upload.md`, reviews `docs/superpowers/reviews/2026-11-08-b1-inbox-upload-review.md` and `-security-review.md`
| B2 | Ingestion pickup verification | Integration test and doc update proving a file written by the endpoint is picked up by `ingest_hash_dedup` and flows through the tiers; update `docs/batches/use-cases/ingestion.md` and the CLAUDE.md batch notes | B1 | done (2026-11-08, not merged). 22 new integration tests passed (re-run by the controller); `tests/integration -k ingest` 181 passed and the whole `tests/integration` root 1550 passed (reported). No production code touched; no formal review (tests and docs only). Not verified: real OneDrive inbox, live backend, real phone (E1) | `2026-11-08-b2-ingestion-pickup-verification-design.md`, plan `docs/superpowers/plans/2026-11-08-b2-ingestion-pickup.md`
| A1 | Android tracker data layer | Local database with per-environment rows (file identity, state `pending`/`uploaded`/`rejected`, reason), per-environment album setting and batch size, repository, unit tests | S2 | done (2026-11-08; review and fixes done, not merged). 97 unit tests passed (reported). Not verified: the real Room SQL (`TrackerDaoTest` needs a device) | `2026-11-08-a1-tracker-data-layer-design.md`, plan `docs/superpowers/plans/2026-11-08-a1-tracker-data-layer.md`, review `docs/superpowers/reviews/2026-11-08-a1-tracker-data-layer-review.md`
| A2 | Album selection and scan | Folder picker with persisted permission, per-environment album, Y count and refresh (auto and manual), change-album confirmation that wipes the tracker | A1 | done (2026-11-08; review and fixes done, not merged). Android unit tests 218 passed in total, 0 failures (controller counted the Gradle reports). Not verified: real folder-picker, query abort, root check and grant comparison on a device | `2026-11-08-a2-album-selection-and-scan-design.md`, plan `docs/superpowers/plans/2026-11-08-a2-album-selection-and-scan.md`, review `docs/superpowers/reviews/2026-11-08-a2-album-selection-and-scan-review.md`
| A3 | Sync engine | Batched upload (1 to 50), mark uploaded per successful batch, stop, error handling, mapping of the per-file outcomes into the tracker | A1, B1 | done (2026-11-08; review and fixes done, not merged). Same 218-test run; `assembleDebug` builds. Not verified: real network and chunked upload timings, streaming from the phone, the live backends | `2026-11-08-a3-sync-engine-design.md`, plan `docs/superpowers/plans/2026-11-08-a3-sync-engine.md`, review `docs/superpowers/reviews/2026-11-08-a3-sync-engine-review.md`
| A4 | Sync screen | "X of Y", Sync now / Stop, batch-size setting, refresh button, rejected list, change-album dialog, navigation entry; fits or replaces `UploadScreen` | A2, A3 | done (2026-11-09; two reviews and fixes done, not merged). Controller re-ran everything fresh: 280 Android unit tests passed, 0 failures, `assembleDebug` builds, lint clean in touched files. Not verified: anything visual or TalkBack, the real folder picker, the Room SQL (`TrackerDaoTest`, `SyncScreenTest` need a device and the androidTest source set does not compile because of the pre-existing `SearchScreenTest.kt:54`) | `2026-11-08-a4-sync-screen-design.md`, plan `docs/superpowers/plans/2026-11-08-a4-sync-screen.md`, reviews `docs/superpowers/reviews/2026-11-08-a4-sync-screen-logic-review.md` and `-ui-review.md`
| E1 | End-to-end check and rollout | Device run against a scratch backend on a free port, rollout note, the owner's go-ahead for applying the backend change to the always-on environments | B2, A4 | not started (needs the owner: restart of a backend, a phone, and the merge decision) | |

## Spikes and deep dives

| ID | Question | Status | Outcome |
|---|---|---|---|
| S1 | What feeds the inbox and `INCOMING_PATH` today (per environment, from the `.env` files read-only and the code): is anything else using `incoming/`, are `PATH_INGESTION_SOURCE` and `BASE_PATH` on one volume, can the backend process write to the inbox, does the Docker image see it? Does `ingest_hash_dedup` race with a file being written (confirm the staging-subdirectory design)? | done (2026-11-08) | Design confirmed. Inbox is `<BASE_PATH>\inbox` on all three (same volume as `BASE_PATH`, under OneDrive on C:); `INCOMING_PATH` is unset everywhere and `BASE_PATH\incoming` never existed, so only `uploads.py`/`image_store.py` and the docs (`backend_api.md`, `docs/superpowers/specs/upload-endpoint.md`) use it. `ingest_hash_dedup` lists the inbox with `os.listdir`, skips only directories and hashes everything else, so a half-written file is hashed (demonstrated); writing into `<inbox>\.uploading\` and `os.replace` into the parent is invisible until complete and atomic on one volume. Nothing else scans the inbox. B1 must: use the staging dir with a per-request unique name, sanitise names itself (`safe_move.available_filename` only appends `_N`), add a by-hash lookup over all statuses (none exists; `images.content_hash` is nullable and indexed, not unique), fail with 5xx when `PATH_INGESTION_SOURCE` is unset (no fallback to `incoming/`), clean stale staging files, check inbox writability. Risks to test in B1/E1: OneDrive sync or locks on `os.replace`; inbox writability for the backend process is unverified (ACL showed a deny on delete for Everyone and a reparse point; may be a sandbox artefact); a check-then-replace race between concurrent requests needs a lock | |
| S2 | Can the Storage Access Framework folder picker select a Samsung Gallery album folder on Android 10 to 14, keep the permission across reboots and list thousands of files fast? What is the stable identity of a file (document URI, MediaStore id, name plus size)? Is MediaStore bucket access a better fit? | done (2026-11-08, desk research only: no device was connected, nothing verified on a Samsung phone) | Use the SAF folder picker (`OpenDocumentTree`) with `takePersistableUriPermission(READ)`; it needs no storage permission or manifest entry on API 29 to 34, and the document URIs work with the existing `openInputStream` upload. MediaStore is the fallback only: on Android 14 the app's grant is temporary without `READ_MEDIA_VISUAL_USER_SELECTED`, which makes whole-album sync unreliable. Count Y with one `ContentResolver.query` on `buildChildDocumentsUriUsingTree` (document_id, display_name, mime_type, _size, last_modified), never `DocumentFile.listFiles()` (one provider call per file); check `EXTRA_LOADING`. Tracker key `(environmentId, albumTreeUri, documentId)` with name, size, lastModified, state, reason, uploadedAt; no content hash on the phone, since the backend `duplicate` outcome makes re-uploads harmless. D2 follows from this: an edited file keeps its id and is not re-sent, a renamed file is re-sent and answered `duplicate`, a deleted file leaves Y and stays in X. Repo has DataStore, KSP (Hilt), no Room, no WorkManager; A1 adds Room through the existing `ksp(...)` setup; A2 adds a persisted-grant check (revoked grant) and a NavGraph route in A4. The app's own `Pictures/MemesBrowser-*` folders will appear as pickable albums. Owner hand-check on the Samsung phone (unverified): the picker can select the album folder and enables 'Use this folder'; the grant survives a reboot; listing time on the real album; the album is a real directory, not a merged view; file names carry `YYYYMMDD_HHMMSS` | |
| D1 | Popularity on re-upload: block files already in the library at upload time or let `ingest_hash_dedup` count them | done | Block (owner, 2026-11-08): `duplicate`, nothing written |
| D2 | What does the tracker do when a file in the album is later edited, renamed or deleted on the phone (same identity, changed size/mtime)? Proposed: not re-uploaded; deleted files drop out of Y but stay counted in X | decided | Edited file keeps its id and is not re-sent; renamed file is re-sent and answered `duplicate`; deleted file leaves Y and stays in X (S2). Confirm in the A1 design |

## Notes carried forward

- **A3 must handle HTTP 429** (the upload rate limit is 10 requests per minute per IP, no `Retry-After` header): wait out the window, show a "waiting" state, retry; do not mark files failed. At batch size 1 the sync is rate-limited to 10 files a minute. Also handle 503 (inbox full / not writable), 507 (disk low) as stop-with-message, and use the new per-file `index` to match responses to sent files.
- **A4:** repoint `UploadViewModel` at the shared supported-types/size constants in `data/tracker/SupportedImages`; revisit the de-duplicated rejected-list streams (A1 review M4) if the list recomposes too often.
- **Fixed 2026-11-09:** `androidTest/.../ui/search/SearchScreenTest.kt` no longer breaks the instrumented source set (its `onMemeClick` lambda took one argument; the screen passes `(memeId, searchId)`); `compileDebugAndroidTestKotlin` now succeeds, including the never-compiled `SyncScreenTest`, `TrackerDaoTest` and `AlbumAccessTest`. They still have not RUN (no device). The worktree has no `local.properties` (set `ANDROID_HOME` in the shell).
- **E1 device checklist (collected from reviews):** hand-check list of S2; run `TrackerDaoTest` and `AlbumAccessTest` on a device; upload a Cyrillic-named file and see it in the inbox with the right name; real 50-file chunked upload timing; OneDrive hard-link/rename in the real inbox and backend write rights; a stop mid-upload on a phone.
- **Accepted risk (A3, M2):** if the server fails every file, three files are rejected for good as `server_error` before the run stops.
- **A2 coordinator amendment (from A4 review LR-I2):** re-picking the same album while the scan says permission lost re-takes the grant and rescans without a wipe.
- **Tech debt / follow-ups:** strings are inline English (no resources in the app); `SyncScreen.kt` is large; add the TalkBack slider and 2.0x text layout checks to E1.
- **Open for the owner:** hand-check the S2 list on the Samsung phone before A4 ships; E1 must run a device test of the real Room SQL.

## Order

```
A0 ──► S1 ──► B1 ──► B2 ──┐
  └──► S2 ──► A1 ──► A2 ──┤
                └──► A3 ──┴──► A4 ──► E1
```

S1 and S2 run in parallel. The backend track (S1, B1, B2) and the Android track (S2, A1, A2) are independent until A3, which needs
the B1 response shape; the B1 design fixes that shape first so A3 can start against it.
