# Epic: Android album sync into the ingestion inbox

Status: approved
Originates from: user request, 2026-11-08 (brainstorming session)
Follow-ups: project design specs are listed in `tracker.md` and written when each project is picked up.

The owner collects memes in a phone gallery album (for example a Samsung Gallery album). Today the only way to get them into the
library is to pick files by hand on the Upload screen, with no memory of what was already sent. This epic lets the Android app
sync one chosen album per environment (`general`, `metal`, `it`) into the backend's ingestion inbox, so the files go through the
normal ingestion flow (`docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md`) like any other dropped file. The app keeps
a tracker per environment of what is already uploaded, what is pending and what the backend rejected.

This folder is the epic: charter and design in this file, project tracker in `tracker.md`. One design spec per project is written
when the project is picked up and linked from the tracker.

## Intent

Make phone-to-library transfer a repeatable, resumable, low-effort action: open the app, see "X of Y uploaded", press Sync.
Nothing about ingestion review changes; the phone only becomes another way to fill the inbox.

## Decisions made (by the owner, in brainstorming)

- **Target directory is the ingestion inbox.** Uploaded files land in `PATH_INGESTION_SOURCE` and follow the normal ingestion
  process. They no longer go to `INCOMING_PATH` / `BASE_PATH/incoming`.
- **One album per environment, chosen explicitly.** The app has a tracker for each of general, metal and it. The album choice is
  persisted per environment; the user must select it if none is stored yet.
- **Status screen.** Shows "X out of Y uploaded" and a "Sync now" button. X is the number of images of the album that were
  uploaded; Y is the number of supported images currently in the album.
- **Stoppable.** A running sync can be stopped by the user.
- **Batches.** Files are sent in batches. Default size 50 (today's cap), configurable from 1 to 50. When a batch transfers
  successfully its files are marked uploaded in the app and X goes up.
- **Y refreshes** automatically and also by an explicit refresh button.
- **Changing the album** for an environment is allowed. It wipes that environment's tracker and needs explicit confirmation.
- **Rejected files.** A file the backend rejects (wrong type, too large, bad content) is marked skipped and shown in a "rejected"
  list. It is not retried on later syncs.
- **Manual only.** The sync runs when the user presses the button. No background or scheduled sync.
- **Process.** Separate worktree; the owner confirms every merge to `main` and every database migration; ambiguities go through
  brainstorming; each project gets a fresh-subagent review matching its area and axis.

## Decisions I made (guesses, to be confirmed in the project designs)

- **Original file name is kept** in the inbox instead of a UUID. Samsung names such as `20241108_123456.jpg` carry the timestamp
  that `detect_comics_candidates` reads. A name collision with different content gets a numeric suffix. The client name is
  sanitised (basename only, no path separators or control characters, Windows-forbidden characters and reserved names replaced,
  length capped, a leading dot removed, extension must still be an allowed image extension).
- **Atomic write into the inbox.** `ingest_hash_dedup` hashes every non-directory entry of the inbox, so a half-written file could
  be ingested corrupted. The backend writes to a staging subdirectory of the inbox (directories are skipped by the scan, and the
  same volume is guaranteed) and renames into place.
- **Three per-file outcomes** in the upload response: `accepted` (written), `duplicate` (identical content already in the inbox, or
  already an active library image: nothing is written) and `rejected` (type, size or content). The app treats `accepted` and
  `duplicate` as uploaded and `rejected` as skipped. This also makes a crash between "backend saved" and "app marked" harmless.
- **Selection of the album** uses Android's folder picker (Storage Access Framework) with a persisted permission, so no broad
  storage permission is needed. Spike S2 may replace this with MediaStore if the picker cannot reach a Samsung album.
- **Tracker storage** is a local database on the phone (rows keyed by file identity per environment), not DataStore, because an
  album can hold thousands of files. Which identity (document URI, MediaStore id) is decided in S2.
- **No backend database change** is expected: the tracker lives on the phone and the inbox is a directory. If a design needs a
  migration, the owner is asked first.
- **Batch of failures.** A transport error (no network, server down, 5xx, 429) fails the batch, leaves its files pending, stops
  the sync and shows the error. Files already marked uploaded stay marked.

## Facts established from the code (builder check, 2026-11-08)

- **Environments already exist in the app.** `BackendEnvironment` (`id`, `name`, `baseUrl`, `isBuiltIn`) with three built-ins
  (General `:8082`, IT `:8083`, Metal `:8081` on the LAN host) and a switcher backed by `EnvironmentStore`. The tracker is keyed by
  the `BackendEnvironment.id`, so a custom environment gets its own tracker too. No new network configuration is needed.
- **There is no authentication** on `/api/uploads` (only the per-IP rate limit). This epic adds none.
- **Callers of `/api/uploads`:** the web `UploadPage` (via `HttpMemesApi.ts`) and the Android `UploadScreen`. Both will now write to
  the inbox, as the owner decided. Nothing else reads `BASE_PATH/incoming`.
- **Filename collisions in `BASE_PATH`** are already handled by ingestion (`move_without_overwrite` renames on collision), so the
  endpoint only needs to avoid collisions inside the inbox itself.

## More calls I made (from the builder check)

- **The response stays backward compatible.** `uploaded[]` and `failed[]` keep their meaning; `UploadedFile.status` (already in the
  model, today always `ok`) becomes `accepted` or `duplicate`, and rejections stay in `failed[]`. Existing web and Android callers
  keep working; B1 still regenerates the three type trees.
- **What counts as `duplicate`:** the upload's SHA-256 matches an `images.content_hash` of any status (active, pending, rejected,
  cut_proposed), or a file already in the inbox. Counting rejected images keeps a meme the owner rejected in review from coming
  back through the phone. The inbox is normally small because ingestion moves files out of it; B1 caps and measures the scan. A
  read of `images` needs no migration (`content_hash` exists).
- **A per-file write error** (disk, permissions) fails the whole request with a 5xx. Files already written by that request are
  `duplicate` on the retry, so the retry is safe. There is no fourth per-file outcome.
- **Y** counts the supported images (the endpoint's allowed types) directly in the chosen folder, not in its subfolders. A file of
  an unsupported type or over 20 MB is not uploaded: the app marks it rejected locally (shown in the rejected list, with the
  reason). The screen reads "X of Y uploaded" plus a separate "R rejected" count, so X does not need to reach Y. HEIC and other
  unsupported formats therefore show up as rejected, which is the visible signal that the backend cannot take them.
- **Rollout order:** backend first (owner go-ahead and restart), then the app. No old-backend compatibility window and no version
  check: an old backend would accept the files into `incoming/`, which the app cannot detect, so the rollout note says to update
  the backend before the first sync.

## Decided by the owner after the builder check (2026-11-08)

- **Popularity on re-upload: block.** The upload endpoint reports content that already matches a library image (any status) or a
  file in the inbox as `duplicate` and writes nothing, so a phone sync never changes popularity. B1 is planned against this.

## Non-goals

- Background, scheduled or automatic sync; watching the album for new files.
- Syncing more than one album per environment, or videos.
- Deleting or moving files on the phone after upload.
- Replacing the review UI, or auto-promoting phone uploads past ingestion review.
- Two-way sync (downloading from the library).

## Anti-goals (failure even if it technically works)

- A corrupted or half-written file reaching ingestion.
- Re-uploading the whole album on every sync, or losing the record of what was uploaded.
- A path traversal or overwrite through a crafted file name.
- Silent popularity inflation, or any change to how the existing ingestion tiers behave.
- Work leaking into the always-on environments: no binding of ports 8081-8083 / 5173-5175 / the three database ports.

## Constraints

- Android: minSdk 29, AGP 8.5.2, Java 21 JBR (see `AndroidClient/CLAUDE.md`); unit tests before every commit; screens never touch
  `NavController`. `backend_api.md` stays authoritative; schema changes regenerate the TypeScript, Kotlin and Python trees.
- Backend: Router, Service, Repository layering; the upload endpoint keeps its rate limit and size limits (20 MB, 50 files per
  request). The backend image already carries what the endpoint needs.
- The metal, general and it backends are always running: any change that needs a restart or a changed `.env` value
  (`PATH_INGESTION_SOURCE` already exists there) is applied only on the owner's go-ahead.
- Live databases: read-only role for any agent, never the main `DATABASE_URL`.

## What is left to the builder

Exact schema of the upload response and tracker tables, the Android database library, the screen layout, naming, the collision
suffix format, how Y is counted efficiently for thousands of files, and the test split.
