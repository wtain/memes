# A1: Android tracker data layer, design

Status: done
Plan: docs/superpowers/plans/2026-11-08-a1-tracker-data-layer.md
Originates from: docs/superpowers/specs/2026-11-08-android-ingestion-sync/README.md

Scope: the on-phone data layer only (Room tracker, per-environment settings, repository). No screens, no folder picker, no upload
logic (A2, A3, A4). Inputs: spike S2 (key shape) and decision D2 in `tracker.md`.

## Storage

- **Room 2.8.4** through the existing KSP setup (`ksp(libs.room.compiler)`), schema JSON exported to `AndroidClient/app/schemas/`
  (`ksp { arg("room.schemaLocation", ...) }`) and committed. (my call: 2.8.x is the line with KSP2 support.)
- Database `TrackerDatabase` (`tracker.db`, version 1), one table `tracked_files`, one entity `TrackedFileEntity`.
- **Key:** `(environmentId, albumTreeUri, documentId)`. `environmentId` is `BackendEnvironment.id`.
- **Columns:** `displayName`, `mimeType`, `sizeBytes`, `lastModified`, `state` (`PENDING` / `UPLOADED` / `REJECTED`, stored by name),
  `rejectionReason` (nullable), `uploadedAt` (nullable), `updatedAt`, `presentInAlbum` (boolean), `lastSeenScan` (long).
- Index on `(environmentId, state)`.

## Rules

- **Classification** (`SupportedImages`, one place): allowed MIME set `image/jpeg, png, gif, webp, bmp, tiff` (lower-cased compare)
  and a 20 MB limit (`size > 20 * 1024 * 1024` is too large). These mirror `UploadViewModel` (`ALLOWED_MIME_TYPES`,
  `MAX_FILE_SIZE`); they are copied, not shared, because `ui/**` is out of scope; A4 should repoint `UploadViewModel` at this file.
  A new file of an unsupported type is stored `REJECTED` with reason `unsupported_type`; too large with `too_large`; otherwise
  `PENDING`. Backend rejections (A3) use the free-text reason the backend gives.
- **D2 on scan:** an existing row keeps its `state`, `rejectionReason` and `uploadedAt` untouched, even when size or lastModified
  changed (an edited file is not re-sent). Its `displayName`, `mimeType`, `sizeBytes`, `lastModified` are refreshed (my call). SAF
  document ids are path based (`primary:DCIM/Album/x.jpg`), so a renamed or moved file is a NEW row (PENDING) beside the old
  row (UPLOADED, absent). It costs one `duplicate` answer from the backend (S2/D2), and X can exceed Y after renames.
- **Presence:** a scan stamps every file it lists with `presentInAlbum = true` and a scan id. After the last chunk, every row of the
  environment not stamped with that scan id gets `presentInAlbum = false` (scoped to the whole environment, not one album, so a
  stale old album never lingers as present). Rows are never deleted by a scan; a file that reappears is present again with its
  old state (a rejected file stays rejected).
- **Counts** (all SQL aggregates, one row):
  - `uploaded` (X) = rows `UPLOADED`, present or not (D2: a deleted file stays in X).
  - `supported` (Y) = present rows except `REJECTED` with reason `unsupported_type`. So Y includes a too-large file and a
    backend-rejected file (their type is supported); uploaded files that left the album do not count. (my call)
  - `rejected` (R) = present rows `REJECTED` (shows unsupported and too large too, as the README says).
  - `pending` (P) = present rows `PENDING`.
- `nextPending(env, limit)`: present `PENDING` rows ordered `lastModified DESC, documentId ASC`.
- `markUploaded(env, album, documentIds, at)`: transactional, chunked; sets `UPLOADED`, clears the reason, keeps an existing
  `uploadedAt` (idempotent), bumps `updatedAt`. `markRejected(env, album, documentId, reason, at)`: sets `REJECTED` + reason.
- `rejectedFiles(env)`: Flow of present rejected rows, newest `lastModified` first.
- `resetEnvironment(env)`: deletes only that environment's rows. The album-change flow (confirmation, clearing the album setting)
  is A2.
- **Scale:** `syncScan` runs in chunks of 500 files, one transaction per chunk (below SQLite's 999 variable limit for the key
  lookup), plus a final transaction for the missing marking. A crash mid-scan leaves a consistent table (some presence flags
  stale until the next scan).

## Settings (DataStore, `tracker_settings`)

- Per environment: album tree URI string and album display name (both null until selected; set and cleared together).
- Batch size: **global**, not per environment (my call; one knob is enough), default 50, valid 1..50, anything else is clamped
  (below 1 gives 1, above 50 gives 50; an unset value gives 50).
- `TrackerSettingsStore` takes a `DataStore<Preferences>` so it is testable on the JVM with a temp file.

## Code layout (`com.memebrowser.app.data.tracker`)

`ScannedFile`, `SupportedImages`, `TrackedFileState`, `TrackedFileEntity`, `TrackerCounts`, `TrackerDao` (interface),
`TrackerDatabase`, `ScanReconciler` (pure), `TrackerSettingsStore`, `TrackerRepository`; Hilt `di/TrackerModule`.

## Tests

- JVM (run here): `SupportedImages`, `ScanReconciler`, `TrackerSettingsStore` (temp-file DataStore), `TrackerRepository` over an
  in-memory fake `TrackerDao` (including a 5,000-file scan).
- androidTest (cannot run here, no device): `TrackerDaoTest` against an in-memory Room database, which is the only check of the
  SQL (counts, ordering, idempotent markUploaded, markMissing). Robolectric is not in the catalog and is not added.

## Migrations and backup

- Schema version 1 is exported to `AndroidClient/app/schemas/`. Any later schema change needs a version bump, a Room `Migration`
  and the new exported JSON; there is deliberately no `fallbackToDestructiveMigration`.
- Backup (M8): `AndroidManifest.xml` references `res/xml/data_extraction_rules.xml` (API 31+, cloud backup and device transfer)
  and `res/xml/backup_rules.xml` (`fullBackupContent`, API 30 and below). They exclude `tracker.db`, `tracker.db-wal`,
  `tracker.db-shm`, `tracker.db-journal` (domain `database`) and `datastore/tracker_settings.preferences_pb` (domain `file`), so a
  restored tracker never points at a SAF grant that no longer exists. The rest of the app keeps `allowBackup="true"`.

## Concurrency contract (for A2/A3)

- One `Mutex` in `TrackerRepository` serialises `syncScan`, `resetEnvironment`, `changeAlbum` and `clearAlbum`. A2 must still cancel
  the running sync job before an album change (the lock orders calls, it does not stop a waiting scan from running afterwards).
- `changeAlbum` / `clearAlbum` wipe the environment's rows first, then write the album setting.
- `syncScan` refuses an empty listing (`IllegalArgumentException`) unless `allowEmpty = true`; never pass a failed listing.
- Scan ids are `max(now, highest stored id + 1)` read under the lock, so they are strictly increasing.
- `markRejected` is a no-op for an UPLOADED row.

## Review outcome (2026-11-08, one iteration)

Fixed: I1 (Mutex + interleaving tests), I2 (spec sentence + rename test), M1 (COALESCE in the SQL, Fake mirrors it, androidTest case),
M2 (SQL uses `TrackerSql` constants; `TrackerSqlConstantsTest` fails on a rename), M3 (SQL guard + Fake + tests), M5 (`changeAlbum`,
`clearAlbum`), M6 (`ScannedFile`/`TrackerCounts` in `TrackerModels.kt`; the entity stays public because the repository returns it),
M7 (documented above), M8 (exclusions above), M9 (`allowEmpty`), M10 (monotonic scan id, added `maxScanId`).
Skipped: M4 (Flow de-duplication; acceptable now, revisit in A4 if the rejected list recomposes too often).
Still not runnable here: `TrackerDaoTest` (needs a device); it gained three cases for M1, M3 and `maxScanId`.

## Questions for the owner

None blocking. Open for A2/A4: whether a too-large file should count in Y (I count it).
