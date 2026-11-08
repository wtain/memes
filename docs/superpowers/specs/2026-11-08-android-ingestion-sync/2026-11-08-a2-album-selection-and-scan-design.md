# A2: Album selection and scan, design

Status: implementation
Plan: docs/superpowers/plans/2026-11-08-a2-album-selection-and-scan.md
Originates from: docs/superpowers/specs/2026-11-08-android-ingestion-sync/README.md

Scope: album access, listing and the change-album flow logic. No screens (A4) and no upload logic (A3). Inputs: spike S2, decision D2,
and the A1 concurrency contract. Code: `com.memebrowser.app.data.album`, Hilt in `di/AlbumModule`.

## Components

- **`AlbumScanner`** (interface) and `SafAlbumScanner`. One cursor query on `buildChildDocumentsUriUsingTree` (document_id, display_name,
  mime_type, _size, last_modified), immediate children only, directories skipped, on an injected dispatcher (`Dispatchers.IO`).
  The Android part is a tiny `AlbumRowSource` (`ContentResolverAlbumRowSource`) that returns raw rows plus the EXTRA_LOADING flag, so the
  decision logic runs on the JVM with a fake source.
  Result: `Complete(files)` / `Incomplete(reason)` / `PermissionLost`. Never throws (cancellation excepted).
  - not granted (`AlbumAccess.isGranted` false) or `SecurityException`: `PermissionLost`; EXTRA_LOADING, null cursor, any other exception: `Incomplete`.
  - Row mapping (my call): no id, skipped; null mime becomes "" (rejected locally as unsupported); missing or negative size or time becomes 0;
    missing name falls back to the last segment of the id; a repeated id keeps the first row.
- **`AlbumAccess`**: `persist` (takePersistableUriPermission READ, false when refused), `isGranted` (persistedUriPermissions), `release`.
- **`AlbumNames`** (pure, string based so no `android.net.Uri`): `displayName` (last path segment of the decoded tree document id; volume root
  gives "Internal storage" for `primary`; fallback the URI) and `looksLikeAppOwnFolder` (`Pictures/MemesBrowser-*`, from `MediaStoreHelper`).
- **`SyncGate`**: `cancelAndAwait(environmentId)`, no-op default binding; A3 binds the real one. **`EnvironmentIdSource`**: all environment ids, to
  decide whether a tree grant is still shared.
- **`AlbumSyncCoordinator`**: see the CLAUDE.md section and the API below.

## Rules

- Only a `Complete` listing reaches `syncScan`, always with `allowEmpty = true` (an empty album is a real result).
- `PermissionLost` never wipes; same-tree re-selection re-takes the grant and rescans without a wipe (my call).
- Change album: re-read the stored album; new grant FIRST (a refusal changes nothing, my call: deviates from the brief's order), then
  `SyncGate.cancelAndAwait`, then `TrackerRepository.changeAlbum` under the environment lock, then release the old grant unless another
  environment stores the same tree, then rescan. Same album and intact grant: no-op.
- Per-environment lock covers a scan's whole body and the wipe; the album is read inside the lock, so a scan that waited for a change scans
  the new album. `refreshScan` is conflated: concurrent callers share the in-flight result; if the owner is cancelled, waiters get
  `Failed(CANCELLED)` and the state returns to Idle.

## Tests

JVM (run here): `AlbumNamesTest`, `SafAlbumScannerTest`, `AlbumSyncCoordinatorTest` (real `TrackerRepository` over `FakeTrackerDao`, fake scanner,
access and gate). androidTest `AlbumAccessTest` (needs a device; checks only the ungranted paths).

## Not covered / owner hand-check

Real SAF behaviour on the Samsung phone (tracker S2 list still applies). A deleted environment keeps its album grant (no cleanup hook; bounded
by the per-app grant limit, A4 or a later project may release it).
