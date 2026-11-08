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

## Review outcome (2026-11-08, one fix iteration)

Owner-level answers (controller's calls): (1) a hung listing must be abortable by "change album" (my call); (2) an empty listing is only accepted
after the tree root still resolves, otherwise it is `Incomplete("album unavailable")` and Y is not dropped (my call); (3) the new grant is
persisted at confirm time with the picker's temporary grant; if it is gone, `confirmChangeAlbum` returns `GrantFailed` and changes nothing (my call);
(4) the own-folder warning applies only to `com.android.externalstorage.documents` (my call).

Fixed:
- I1 cancellation safety: the grant is given back if the gate step is cancelled or fails; after the gate the commit (re-check, wipe/store, scan state
  reset, give back the old grant) runs in `NonCancellable`; only the final rescan is cancellable. A throwing `changeAlbum`/`setAlbum` gives the new
  grant back and returns `AlbumChangeResult.Failed`. Tests: cancel at the gate, right after the grant, after the wipe, first selection, storage failure.
- I2 hung scan: `refreshScan` runs in a child `Job` registered per environment; a change aborts it and keeps aborting until it owns the lock. The
  scanner runs the query on a detached task, aborts it through `ListingCancel` (a `CancellationSignal` in the real source) on cancellation and
  on a 60 s timeout (`Incomplete("listing timed out")`).
- I3 stale join: the in-flight entry is removed while the scan still holds the lock, and the change removes it when aborting.
- Minor: scan state reset to `Idle` on a change; `refreshScan` never throws (settings/database failure is `Failed(TRACKER_ERROR)`, a throwing scanner is
  `LISTING_INCOMPLETE`); the plan is compared with the stored album (`PlanStale`), re-checked under the lock; `releaseIfUnused` keeps the grant when the
  environment list cannot be read and now also counts the current environment; `DIR_MIME` replaced by `DocumentsContract.Document.MIME_TYPE_DIR`;
  `isGranted` compares parsed URIs (then authority and decoded path); own-folder check restricted to the external-storage authority; ejected-root guard
  (finding 5); cursor reading is the pure `readRows` over a `RowReader` and is tested.
- Finding 8 (process death of the temporary grant): follows from answer (3); A4 should keep the dialog in the picker's task and handle `GrantFailed`
  by re-picking.

Skipped: finding 12 (state maps grow by environment id; negligible); finding 10 display names for non-externalstorage providers (cosmetic). A fake
`DocumentsProvider` androidTest for the real cursor mapping and `persistedUriPermissions` comparison was not added (needs a device; the cursor mapping
itself is covered on the JVM through `RowReader`).
