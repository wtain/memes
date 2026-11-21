# A5: MediaStore album source, design

Status: draft
Originates from: docs/superpowers/specs/2026-11-08-android-ingestion-sync/README.md (decision "Selection of the album", fallback named by spike S2)

Scope: let the owner choose an album that the Storage Access Framework (SAF) folder picker refuses, in particular Samsung Gallery
albums under `DCIM/`, by listing the phone's gallery albums through MediaStore. Android only; no backend change. Builds on A1 to A4,
which are merged.

## Problem

On the owner's Samsung phone the folder picker (`OpenDocumentTree`) shows "can't select this folder" for `DCIM/3 - memes`.
Pressing Allow on the following dialog returns no result to the app: the 2026-11-21 bug report from v1.32.0 logged
`Folder picker returned no result` at the moment of Allow, and the owner confirmed that a folder outside DCIM works. So the album
the owner wants to sync from cannot be chosen with the current design. Android restricts SAF tree grants on some paths and
Samsung's picker applies that restriction to DCIM; the app cannot change it.

Spike S2 (desk research, no device) chose SAF and named MediaStore as "the fallback only", with one concern: on Android 14 the
app's access is partial without the right permission. This spec is that fallback, with the concern handled explicitly (see
"Permission").

## Decision

Add MediaStore albums (a "bucket" is one gallery folder, for example `DCIM/3 - memes`) as a second album source next to SAF.
The picker button becomes two choices: gallery album (new, the default for the owner) and folder (existing, unchanged). Existing
SAF selections keep working and are never wiped by this change.

Alternatives considered:

| Option | Verdict |
|---|---|
| Replace SAF with MediaStore | Rejected: breaks existing selections and loses non-gallery folders (for example a cloud-synced folder). Can be revisited once MediaStore is proven on the phone. |
| `MANAGE_EXTERNAL_STORAGE` (all files access) | Rejected: a sensitive permission, a larger privacy footprint, and likely to raise the very scanner flags the owner is already seeing. |
| Move files out of DCIM by hand | A workaround for the owner today, not a product answer. |
| Android Photo Picker | Rejected: selects individual files, not an album, and gives no "X of Y in the album" count. |

## Album identity

The tracker row key stays `(environmentId, albumTreeUri, documentId)` and the Room schema does not change (no migration). The
two string columns become opaque keys with a scheme:

- SAF (existing): `albumTreeUri` = `content://<authority>/tree/...`, `documentId` = the provider's document id.
- MediaStore (new): `albumTreeUri` = `mediastore-bucket:<BUCKET_ID>`, `documentId` = the image's `MediaStore.Images.Media._ID` as a decimal string.

A small `AlbumKey` object (pure, JVM-testable, like `AlbumNames`) parses the scheme and answers `isMediaStore`, `bucketId`. The
column name `albumTreeUri` is kept for compatibility (renaming needs a migration and gives nothing); the KDoc says it is an album
key. The stored `displayName` is the bucket's display name (`3 - memes`).

Identity rules carry over from D2: an edited file keeps its `_ID` and is not re-sent; a renamed file keeps its `_ID` too (MediaStore
ids survive a rename, SAF document ids do not), which is better; a deleted file leaves Y and stays in X.
A file that the system re-inserts under a new `_ID` is re-sent once and answered `duplicate` by the backend.

## Components

Each existing seam gets a MediaStore implementation and a thin dispatcher that chooses by `AlbumKey`. The coordinator, tracker,
engine and screen keep their current contracts.

- **`AlbumRowSource` / `AlbumScanner`**: `MediaStoreAlbumRowSource` runs one `ContentResolver.query` on
  `MediaStore.Images.Media.EXTERNAL_CONTENT_URI` with selection `BUCKET_ID = ?` (and `IS_PENDING = 0` on API 30+; trashed rows are
  excluded by default), projection `_ID, DISPLAY_NAME, MIME_TYPE, SIZE, DATE_MODIFIED`, a `CancellationSignal` like today.
  `DATE_MODIFIED` is in seconds, so the row source multiplies by 1000 to match the tracker's milliseconds. Rows map to
  `ScannedFile` exactly as `SafAlbumScanner.map` does (documentId = `_ID` string). The 60 s timeout, `Complete / Incomplete /
  PermissionLost` results and the "never sync an incomplete listing" rule are unchanged.
- **`AlbumFileSource`**: `MediaStoreAlbumFileSource.open` builds `ContentUris.withAppendedId(Images.Media.EXTERNAL_CONTENT_URI, id)`
  and streams `openInputStream`. `FileNotFoundException` still means "vanished" (rejected `file_unavailable`).
  A dispatcher picks SAF or MediaStore from the album key.
- **`AlbumAccess`**: for a MediaStore key, `isGranted` is "the image-read runtime permission is currently granted", `persist`
  is true when it is, and `release` is a no-op (there is no per-album grant to give back). `releaseIfUnused` in the coordinator then
  needs no change.
- **`AlbumLister` (new)**: lists the gallery albums for the chooser: one query for `BUCKET_ID, BUCKET_DISPLAY_NAME` (plus
  `DATE_MODIFIED` for ordering) aggregated in memory to `(bucketId, name, imageCount, newestModified)`, newest first. One query
  for thousands of rows is fast; MediaStore on API 29+ has no `GROUP BY`. Results are `Albums(list) / PermissionDenied /
  Failed`.
- **`AlbumNames`**: `displayName` for a MediaStore key reads the stored name; `looksLikeAppOwnFolder` also fires for a bucket
  named `MemesBrowser-*` (the app's own `Pictures/MemesBrowser-*` gallery folders), keeping the existing warning.
- **`AlbumSyncCoordinator`**: unchanged in shape. `selectAlbum(env, key)` accepts either key kind. The plan, wipe and re-pick
  logic is key-agnostic. Choosing a MediaStore album while a SAF album is stored goes through the normal change-album
  confirmation (wipes that environment's rows), exactly as changing between two SAF folders does today.

## Target Android versions (decision, 2026-11-21)

The app targets contemporary Android, not one phone: behaviour is designed for Android 13 to 16 (API 33 to 36), where nearly all
current devices are, and tested against emulator images of API 33, 34 and the newest available. `minSdk` stays 29 (unchanged);
API 29 to 32 uses the legacy storage permission and must keep working, but gets no extra design effort beyond that. No
device-specific code: Samsung is only the place where the problem was seen.

## Permission

The app currently declares no storage permission. MediaStore needs one runtime permission for gallery images, which is a new
manifest entry:

- API 29 to 32: `READ_EXTERNAL_STORAGE` with `android:maxSdkVersion="32"`.
- API 33 and above: `READ_MEDIA_IMAGES`.
- API 34 and above: `READ_MEDIA_VISUAL_USER_SELECTED` is also declared and requested together with `READ_MEDIA_IMAGES`, so the
  app can tell "full access" from "partial access" (the user chose "Select photos"). Partial access is handled, see below.
- `ACCESS_MEDIA_LOCATION` is not requested: Android then hands the app the image with its GPS EXIF removed. That is a privacy
  advantage for memes (the upload does not leak a location). It is recorded as a decision, not an accident.
- No other permission is added; in particular nothing for all-files access.

Access levels (`MediaAccess`, pure enum): `FULL`, `PARTIAL` (API 34+: only `READ_MEDIA_VISUAL_USER_SELECTED` granted), `DENIED`
(not granted, can ask again), `BLOCKED` (denied permanently: the system will not show the dialog again). Only `FULL` lets an album
be chosen and scanned. `PARTIAL` shows a card "Allow access to all photos to sync a whole album" with a button that opens the app's
system settings, because a partial grant exposes only the photos the user picked and X of Y would be wrong. A scan that runs
while access is `PARTIAL` or `DENIED` returns `PermissionLost`, never `Complete`, so the tracker cannot be corrupted by a subset.

Flow on the sync screen: choose "Gallery album" -> if the permission is not granted, request it with
`ActivityResultContracts.RequestPermission` -> granted: show the album chooser; denied: show a card explaining why it is needed
with a button to ask again, and, after a permanent denial, a button to open the app's system settings. A revoked permission after
a MediaStore album was chosen is `PermissionLost`, which the screen already treats as "Choose album again".

## UI (A4 changes)

- The empty state and the "Change album" action offer two entries: "Gallery album..." and "Folder...". Strings stay inline English.
- Album chooser: a dialog or sub-screen listing albums as `name, image count`, sorted by newest change, each row at least 48 dp,
  with the album's path hint in secondary text when available (`RELATIVE_PATH`, API 29+, from the same query: first row per bucket).
  Empty list and error states have text, not a blank screen.
- Picking an album calls the same `onAlbumPicked`-style path with the bucket key, so busy handling, messages and the change-album
  dialog are reused. The cancelled-picker snackbar added on 2026-11-21 stays for the folder path.
- The header and the album card show the album name; nothing else on the sync screen changes.

## Behaviour changes to be aware of

- MediaStore only knows indexed files. A file copied into the folder and not yet indexed (or inside a `.nomedia` folder) does not
  appear in X/Y until the media scanner sees it. Samsung indexes quickly in normal use; a "refresh" does not force indexing.
- Subfolders are separate buckets, as they are separate folders in SAF (immediate children only), so nothing changes there.
- Videos in the album are not listed (`Images` collection); animated GIFs and WebP follow `SupportedImages` as today.
- Files the user deleted into Samsung Gallery's recycle bin: whether they remain visible in MediaStore is unverified (open
  question); the scan relies on the platform's trashed and pending filters.

## Out of scope

Backend or upload-format changes; video; recursive albums; background or automatic sync; removing the SAF path; a
`ContentObserver` for live Y updates (refresh stays on screen start and the button).

## Testing

- JVM: `AlbumKey` parsing; `MediaStoreAlbumRowSource` decision logic via a fake cursor reader (reuse `RowReader`/`readRows`
  style); seconds-to-milliseconds mapping; dispatcher choice by key; `AlbumLister` aggregation and ordering; coordinator tests
  with a MediaStore key (first selection, change from SAF, permission lost); ViewModel tests for the permission states
  (granted, denied, permanently denied).
- Existing tests must pass unchanged (SAF behaviour is not modified).
- androidTest: a query against the emulator's MediaStore with a seeded image and bucket, and the permission-denied path.
- Device check by the owner (not verifiable from here): `DCIM/3 - memes` appears as an album with the right count; one sync
  uploads and the inbox shows the files with their original names; revoking the permission in system settings shows "Choose
  album again"; behaviour on the phone's Android version (see below).

## Open questions

1. **(Resolved 2026-11-21: handled by design.)** Android 14+ partial photo access is detected through
   `READ_MEDIA_VISUAL_USER_SELECTED` and blocks the scan; see "Permission". Still to confirm on a device or emulator: the exact
   dialog wording and whether a later "Allow all" in system settings is picked up on the next refresh.
2. **Does `DCIM/3 - memes` appear as one bucket** on the owner's phone, with the expected count (Samsung Gallery sometimes shows
   merged or virtual albums that are not real folders)? Device check.
3. **Recycle bin and hidden albums.** Do Samsung's trash and "hidden albums" keep rows visible to `MediaStore.Images`?
4. **Scanner flags.** Adding a media permission changes the app's permission profile. The 2026-11-21 "harmful" flag predates this
   and its cause is not established (see the investigation notes in the session; the startup self-update and the telemetry
   beacons are the suspects). This change should be checked against the flagging scanner after release, and its text collected
   first, before assuming either way.
5. **Keep SAF long term?** Decide after the phone run whether the folder path is still wanted.

## Files expected to change

`data/album/` (`AlbumKey`, `MediaStoreAlbumRowSource`, `AlbumLister`, dispatching `AlbumAccess` and scanner wiring, `AlbumNames`),
`data/sync/` (file-source dispatcher), `di/AlbumModule` and `di/SyncModule`, `ui/sync/` (chooser, permission states, `SyncMappers`
texts, `SyncPreviews`), `AndroidManifest.xml`, `AndroidClient/CLAUDE.md` (album access notes), the epic `tracker.md`
and tests. No change under `Backend/`, `shared/schemas/` or `Storage/`.
