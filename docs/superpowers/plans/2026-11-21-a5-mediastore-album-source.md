# A5 MediaStore Album Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner pick a gallery album (a MediaStore bucket such as `DCIM/3 - memes`) as the album sync source, next to the existing folder picker.

**Architecture:** The tracker, coordinator and sync engine are untouched. The tracker's `albumTreeUri` and `documentId` columns become opaque keys: a `mediastore-bucket:` key selects a MediaStore implementation of the three seams (`AlbumAccess`, `AlbumRowSource`, `AlbumFileSource`) through thin dispatchers that choose by key. A new `AlbumLister` feeds a chooser dialog; a pure `MediaAccess` model drives the permission cards.

**Tech Stack:** Kotlin, Jetpack Compose, Hilt, Room (unchanged), `MediaStore.Images`, JUnit4 + mockk (JVM), Android Gradle (JDK 21 from Android Studio).

**Spec:** `docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-21-a5-mediastore-album-source-design.md`

Work in a new worktree from local `main` (see the CLAUDE.md `EnterWorktree` gotcha: compare `git rev-parse main` with the worktree HEAD). All Gradle commands run from `AndroidClient/` with `$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"`. The unit-test command is `.\gradlew :app:testDebugUnitTest --no-daemon`; a single class: add `--tests "<fully qualified class>"`.

## Global Constraints

- `minSdk` stays 29; behaviour designed and tested for API 33 to 36; API 29 to 32 uses `READ_EXTERNAL_STORAGE` with `android:maxSdkVersion="32"`.
- Permissions added, nothing else: `READ_MEDIA_IMAGES`, `READ_MEDIA_VISUAL_USER_SELECTED`, `READ_EXTERNAL_STORAGE` (max 32). No `ACCESS_MEDIA_LOCATION`, no `MANAGE_EXTERNAL_STORAGE`.
- No Room schema change, no migration, no change under `Backend/`, `shared/schemas/` or `Storage/`.
- Album key format: `mediastore-bucket:<BUCKET_ID>/<URL-encoded bucket name>`; `documentId` is `MediaStore.Images.Media._ID` as a decimal string.
- `DATE_MODIFIED` is in seconds; the tracker uses milliseconds.
- Only access level `FULL` may produce `AlbumScanResult.Complete`; `PARTIAL` and `DENIED` produce `PermissionLost`.
- Existing SAF behaviour and its tests must pass unchanged. Strings are inline English like the rest of the app.
- Before each commit run the unit tests (AndroidClient/CLAUDE.md). Commit messages end with the repo's `Co-Authored-By` and `Claude-Session` lines.

## Review Focus

- An empty MediaStore bucket (every image deleted, SD card ejected) must never be a `Complete` empty listing that makes the tracker treat the album as emptied: expected `Incomplete("album unavailable")`, tracker untouched. (Task 3)
- A partial-access grant (Android 14+ "Select photos") must never reach `syncScan` with the subset: expected `PermissionLost` and the "allow all photos" card. (Tasks 1, 3, 6)
- A bucket name with spaces, a dash or Cyrillic (`3 - memes`, `Мемы`) must round-trip through the key unchanged, and a name containing `/` or `%` must not break parsing. (Task 1)
- Two albums with the same display name (internal storage and SD card `Screenshots`) must be distinguishable in the chooser. (Task 5)
- Permission revoked in system settings while a sync runs: the engine's existing `SecurityException` path ends in `Failed(PERMISSION_LOST)` for a MediaStore file too. (Task 4)

---

### Task 1: Album key, album names and access levels (pure)

**Files:**
- Create: `app/src/main/java/com/memebrowser/app/data/album/AlbumKey.kt`
- Create: `app/src/main/java/com/memebrowser/app/data/album/MediaAccess.kt`
- Modify: `app/src/main/java/com/memebrowser/app/data/album/AlbumNames.kt`
- Test: `app/src/test/java/com/memebrowser/app/data/album/AlbumKeyTest.kt`, `MediaAccessTest.kt`, and the existing `AlbumNamesTest.kt` (extend)

**Interfaces:**
- Produces:
  - `object AlbumKey { fun mediaStore(bucketId: Long, bucketName: String): String; fun isMediaStore(key: String): Boolean; fun bucketId(key: String): Long?; fun bucketName(key: String): String? }` (pure, no `android.net.Uri`, uses `URLEncoder`/`URLDecoder` UTF-8; `bucketId`/`bucketName` return null for a SAF key or a malformed key; the id is everything between the scheme prefix and the first `/`).
  - `enum class MediaAccess { FULL, PARTIAL, DENIED, BLOCKED }` and `object MediaAccesses { fun resolve(granted: Set<String>, sdkInt: Int, canAskAgain: Boolean): MediaAccess; fun permissionsToRequest(sdkInt: Int): Array<String> }`. Permission names are the string constants `android.permission.READ_MEDIA_IMAGES`, `READ_MEDIA_VISUAL_USER_SELECTED`, `READ_EXTERNAL_STORAGE`.
  - `AlbumNames.displayName(key)` returns the decoded bucket name for a MediaStore key; `AlbumNames.looksLikeAppOwnFolder(key)` is true for a MediaStore key whose bucket name starts with `MemesBrowser-` (case-insensitive). SAF behaviour unchanged.

- [ ] **Step 1: Write failing tests.** `AlbumKeyTest`: `mediaStore(123, "3 - memes")` round-trips through `bucketId == 123L` and `bucketName == "3 - memes"`; a name `"Мемы/100%"` round-trips; `isMediaStore` is false for `content://com.android.externalstorage.documents/tree/primary%3ADCIM`; `bucketId("mediastore-bucket:abc/x") == null`. `MediaAccessTest`: sdk 34 with both media permissions -> FULL; sdk 34 with only `READ_MEDIA_VISUAL_USER_SELECTED` -> PARTIAL; sdk 33 with `READ_MEDIA_IMAGES` -> FULL; sdk 31 with `READ_EXTERNAL_STORAGE` -> FULL; none granted and `canAskAgain = true` -> DENIED, `false` -> BLOCKED; `permissionsToRequest` returns 2 entries on 34+, `[READ_MEDIA_IMAGES]` on 33, `[READ_EXTERNAL_STORAGE]` on 29 to 32. `AlbumNamesTest` additions: `displayName(AlbumKey.mediaStore(1, "3 - memes")) == "3 - memes"`, `looksLikeAppOwnFolder` true for bucket `MemesBrowser-Trends`, false for `memes`.
- [ ] **Step 2: Run** `.\gradlew :app:testDebugUnitTest --no-daemon --tests "com.memebrowser.app.data.album.*"`. Expected: FAIL (unresolved references).
- [ ] **Step 3: Implement** the types above with the signatures given. `resolve` rule: FULL when `READ_MEDIA_IMAGES` (sdk >= 33) or `READ_EXTERNAL_STORAGE` (sdk <= 32) is in `granted`; PARTIAL when sdk >= 34 and only `READ_MEDIA_VISUAL_USER_SELECTED` is granted; otherwise DENIED if `canAskAgain` else BLOCKED. Extend `AlbumNames` to dispatch on `AlbumKey.isMediaStore`.
- [ ] **Step 4: Run** the same command. Expected: PASS, and the whole `data.album` package still passes.
- [ ] **Step 5: Commit** `feat(android): album key and media access levels (A5)`.

### Task 2: Manifest and the MediaStore access seam

**Files:**
- Modify: `app/src/main/AndroidManifest.xml`
- Create: `app/src/main/java/com/memebrowser/app/data/album/MediaPermissions.kt`, `DispatchingAlbumAccess.kt`
- Modify: `app/src/main/java/com/memebrowser/app/di/AlbumModule.kt`
- Test: `app/src/test/java/com/memebrowser/app/data/album/DispatchingAlbumAccessTest.kt`

**Interfaces:**
- Consumes: `AlbumAccess` (existing interface: `persist`, `isGranted`, `release`), `AlbumKey`, `MediaAccesses` (Task 1).
- Produces:
  - `fun interface MediaAccessSource { fun current(canAskAgain: Boolean): MediaAccess }` and `class ContextMediaAccessSource(context: Context, sdkInt: Int = Build.VERSION.SDK_INT) : MediaAccessSource` (collects the granted ones of the three permission names with `ContextCompat.checkSelfPermission`, then `MediaAccesses.resolve`). The seam exists because `Build.VERSION.SDK_INT` is 0 in JVM tests.
  - `class MediaStoreAlbumAccess(private val access: MediaAccessSource) : AlbumAccess`: `isGranted(key)` and `persist(key)` are true only when `access.current(canAskAgain = true) == MediaAccess.FULL`; `release` does nothing.
  - `class DispatchingAlbumAccess(private val saf: AlbumAccess, private val media: AlbumAccess) : AlbumAccess` routing by `AlbumKey.isMediaStore`.

- [ ] **Step 1: Write failing tests** `DispatchingAlbumAccessTest`: a media key goes to the media delegate and a SAF key to the SAF one (mockk `verify`, each method); `MediaStoreAlbumAccess` returns false for PARTIAL, DENIED and BLOCKED and true for FULL (fake `MediaAccessSource`); `release` never throws.
- [ ] **Step 2: Run** `--tests "com.memebrowser.app.data.album.DispatchingAlbumAccessTest"`. Expected: FAIL.
- [ ] **Step 3: Implement** the classes. Manifest: add `READ_EXTERNAL_STORAGE` with `android:maxSdkVersion="32"`, `READ_MEDIA_IMAGES` and `READ_MEDIA_VISUAL_USER_SELECTED`. In `AlbumModule`, bind `AlbumAccess` as `DispatchingAlbumAccess(ContentResolverAlbumAccess(resolver), MediaStoreAlbumAccess(ContextMediaAccessSource(context)))` and also provide `MediaAccessSource` itself for the lister and the ViewModel.
- [ ] **Step 4: Run** the unit tests for the package and `.\gradlew :app:assembleDebug --no-daemon`. Expected: PASS and a debug APK.
- [ ] **Step 5: Commit** `feat(android): MediaStore access seam and permissions (A5)`.

### Task 3: MediaStore listing source

**Files:**
- Create: `app/src/main/java/com/memebrowser/app/data/album/MediaStoreAlbumRowSource.kt`, `DispatchingAlbumRowSource.kt`
- Modify: `app/src/main/java/com/memebrowser/app/di/AlbumModule.kt`
- Test: `app/src/test/java/com/memebrowser/app/data/album/MediaStoreRowsTest.kt`, `DispatchingAlbumRowSourceTest.kt`

**Interfaces:**
- Consumes: `AlbumRowSource`, `RawListing`, `ListingCancel`, `AlbumRow`, `RowReader`, `readRows`-style mapping (`AlbumScanner.kt`), `AlbumKey`.
- Produces:
  - `object MediaStoreRows { fun toRows(reader: RowReader): List<AlbumRow> }` reading `_ID`, `DISPLAY_NAME`, `MIME_TYPE`, `SIZE`, `DATE_MODIFIED` (seconds multiplied by 1000; a null or negative value reads as null like `readRows`).
  - `class MediaStoreAlbumRowSource(resolver: ContentResolver) : AlbumRowSource`: one query on `MediaStore.Images.Media.EXTERNAL_CONTENT_URI`, selection `bucket_id = ? AND is_pending = 0`, the `CancellationSignal` wired to `ListingCancel` as in `ContentResolverAlbumRowSource`; returns `RawListing(rows, loading = false, rootResolves = rows.isNotEmpty())` (an empty bucket never exists for a live album, so empty means gone, which the scanner turns into `Incomplete("album unavailable")`).
  - `class DispatchingAlbumRowSource(saf: AlbumRowSource, media: AlbumRowSource) : AlbumRowSource` routing by key.

- [ ] **Step 1: Write failing tests.** `MediaStoreRowsTest` with a fake `RowReader`: `DATE_MODIFIED = 1700000000` becomes `lastModified = 1_700_000_000_000`; a row without `_ID` is kept as null id (the scanner skips it); `documentId` is the decimal `_ID`. Extend a scanner-level test (new `MediaStoreScannerTest`) using `SafAlbumScanner(source, access, dispatcher)` with a fake media source: an empty listing from `MediaStoreAlbumRowSource`'s rule gives `Incomplete("album unavailable")`; access `PARTIAL` (fake `AlbumAccess.isGranted` false) gives `PermissionLost`; three rows give `Complete` with three `ScannedFile` whose names are intact (including `3 - мем.jpg`). `DispatchingAlbumRowSourceTest`: routing by key.
- [ ] **Step 2: Run** `--tests "com.memebrowser.app.data.album.*"`. Expected: FAIL.
- [ ] **Step 3: Implement** the classes; rebind `AlbumRowSource` in `AlbumModule` to the dispatcher (the existing SAF source stays the `saf` delegate, `SafAlbumScanner` is unchanged).
- [ ] **Step 4: Run** the album package tests. Expected: PASS, existing SAF scanner tests unchanged.
- [ ] **Step 5: Commit** `feat(android): MediaStore album listing (A5)`.

### Task 4: MediaStore file source

**Files:**
- Create: `app/src/main/java/com/memebrowser/app/data/sync/MediaStoreAlbumFileSource.kt`, `DispatchingAlbumFileSource.kt`
- Modify: `app/src/main/java/com/memebrowser/app/di/SyncModule.kt`
- Test: `app/src/test/java/com/memebrowser/app/data/sync/DispatchingAlbumFileSourceTest.kt`

**Interfaces:**
- Consumes: `AlbumFileSource.open(treeUri: String, documentId: String): InputStream` (in `data/repository/BatchUploader.kt`), `AlbumKey`.
- Produces: `class MediaStoreAlbumFileSource(resolver: ContentResolver) : AlbumFileSource` opening `ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, documentId.toLong())` (a non-numeric id throws `FileNotFoundException`; a null stream throws a plain `IOException` exactly like `ContentResolverAlbumFileSource`); `class DispatchingAlbumFileSource(saf: AlbumFileSource, media: AlbumFileSource) : AlbumFileSource`.

- [ ] **Step 1: Write failing tests:** routing by key (mockk); the media source with a non-numeric document id throws `FileNotFoundException`; a `SecurityException` from the delegate propagates unchanged (the engine maps it to `PERMISSION_LOST`).
- [ ] **Step 2: Run** `--tests "com.memebrowser.app.data.sync.DispatchingAlbumFileSourceTest"`. Expected: FAIL.
- [ ] **Step 3: Implement** both classes and bind the dispatcher in `SyncModule.provideAlbumFileSource`.
- [ ] **Step 4: Run** the `data.sync` package tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(android): MediaStore file source (A5)`.

### Task 5: Album lister

**Files:**
- Create: `app/src/main/java/com/memebrowser/app/data/album/AlbumLister.kt`
- Modify: `app/src/main/java/com/memebrowser/app/di/AlbumModule.kt`
- Test: `app/src/test/java/com/memebrowser/app/data/album/AlbumListerTest.kt`

**Interfaces:**
- Consumes: `AlbumKey.mediaStore`, `RowReader`.
- Produces:
  - `data class GalleryAlbum(val key: String, val name: String, val imageCount: Int, val pathHint: String?, val newestModified: Long)`.
  - `sealed interface AlbumListing { data class Albums(val albums: List<GalleryAlbum>) : AlbumListing; data object PermissionDenied : AlbumListing; data object Failed : AlbumListing }`.
  - `fun interface AlbumLister { suspend fun list(): AlbumListing }` and `class MediaStoreAlbumLister(resolver: ContentResolver, access: MediaAccessSource, dispatcher: CoroutineDispatcher) : AlbumLister` plus the pure `object GalleryAlbums { fun aggregate(rows: List<BucketRow>): List<GalleryAlbum> }` where `BucketRow(bucketId: Long?, bucketName: String?, relativePath: String?, modifiedSeconds: Long?)`.
  - Behaviour of `aggregate`: group by bucket id (rows without id are skipped), count per bucket, `name` falls back to the last segment of `relativePath` then `"Album"`, `pathHint` = the relative path of the newest row (trailing slash removed), sorted by `newestModified` descending. Ties: by name.

- [ ] **Step 1: Write failing tests** `AlbumListerTest`: three rows in two buckets aggregate to two albums with the right counts and newest-first order; two buckets both named `Screenshots` with relative paths `Pictures/Screenshots/` and `DCIM/Screenshots/` yield two albums with distinct `pathHint` (`Pictures/Screenshots`, `DCIM/Screenshots`); a row without bucket id is ignored; the key of each album equals `AlbumKey.mediaStore(id, name)`.
- [ ] **Step 2: Run** `--tests "com.memebrowser.app.data.album.AlbumListerTest"`. Expected: FAIL.
- [ ] **Step 3: Implement.** The Android class does ONE `query` on `Images.Media.EXTERNAL_CONTENT_URI` with projection `BUCKET_ID, BUCKET_DISPLAY_NAME, RELATIVE_PATH, DATE_MODIFIED` and `is_pending = 0`, off the main thread, returns `PermissionDenied` when `access.current(true)` is not `FULL` or a `SecurityException` is thrown, `Failed` on any other exception (never throws except cancellation). Provide it in `AlbumModule`.
- [ ] **Step 4: Run** the album package tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(android): list gallery albums (A5)`.

### Task 6: ViewModel state for the chooser and permissions

**Files:**
- Modify: `app/src/main/java/com/memebrowser/app/ui/sync/SyncViewModel.kt`, `SyncMappers.kt`
- Modify: `app/src/test/java/com/memebrowser/app/ui/sync/SyncViewModelTest.kt` (constructor gains `AlbumLister` and `MediaAccessSource`; update every construction site, per AndroidClient/CLAUDE.md grep both `src/test` and `src/androidTest`)

**Interfaces:**
- Consumes: `AlbumLister`, `AlbumListing`, `GalleryAlbum`, `MediaAccessSource`, `MediaAccess`, existing `onAlbumPicked(treeUri: String)`.
- Produces on `SyncViewModel`: `fun onGalleryRequested()` (resolves access with `access.current(canAskAgain = true)`; FULL loads the albums, otherwise sets the chooser state to `NeedsPermission(access)`), `fun onPermissionResult(canAskAgain: Boolean)` (re-resolves with `access.current(canAskAgain)`; FULL loads albums, else `NeedsPermission(DENIED|BLOCKED|PARTIAL)`), `fun onGalleryAlbumChosen(album: GalleryAlbum)` (closes the chooser and calls `onAlbumPicked(album.key)`), `fun dismissChooser()`. `SyncUiState` gains `chooser: ChooserState?` with `sealed interface ChooserState { data object Loading; data class Albums(val albums: List<GalleryAlbum>); data class NeedsPermission(val access: MediaAccess); data object Failed; data object Empty }`. Pure mapper in `SyncMappers.kt`: `fun permissionCardText(access: MediaAccess): String` with the PARTIAL text "Allow access to all photos to sync a whole album" and a BLOCKED text that names system settings.

- [ ] **Step 1: Write failing tests** (mockk, same style as the file): FULL access + lister returns two albums -> `chooser` is `Albums` with both; lister `PermissionDenied` -> `NeedsPermission(DENIED)`; a fake `MediaAccessSource` answering PARTIAL -> `NeedsPermission(PARTIAL)` and the lister is never called; `onPermissionResult(canAskAgain = false)` with a fake source answering BLOCKED for `canAskAgain = false` -> `NeedsPermission(BLOCKED)`; `onGalleryAlbumChosen` closes the chooser and forwards the key to the coordinator (`coordinator.selectAlbum("env-a", album.key)` verified); an empty album list -> `Empty`; `dismissChooser` -> null.
- [ ] **Step 2: Run** `--tests "com.memebrowser.app.ui.sync.SyncViewModelTest"`. Expected: FAIL.
- [ ] **Step 3: Implement.** The chooser state is per environment screen-local state like `plan`; the lister runs inside the existing `guarded(env)` helper so unexpected errors become the generic message.
- [ ] **Step 4: Run** the whole `ui.sync` tests and `.\gradlew :app:compileDebugAndroidTestKotlin --no-daemon`. Expected: PASS and compiles.
- [ ] **Step 5: Commit** `feat(android): gallery chooser and permission state (A5)`.

### Task 7: Screen: entries, chooser dialog, permission launcher

**Files:**
- Modify: `app/src/main/java/com/memebrowser/app/ui/sync/SyncScreen.kt`, `SyncPreviews.kt`
- Test: `app/src/androidTest/java/com/memebrowser/app/ui/sync/SyncScreenTest.kt` (extend; needs a device or emulator, compile-checked locally)

**Interfaces:**
- Consumes: Task 6 state and actions, `MediaAccesses.permissionsToRequest(Build.VERSION.SDK_INT)`.
- Produces: `SyncActions` gains `onChooseGallery: () -> Unit`, `onChooserDismiss: () -> Unit`, `onChooseGalleryAlbum: (GalleryAlbum) -> Unit`, `onOpenAppSettings: () -> Unit`. `onChooseAlbum` stays and means the folder picker.

- [ ] **Step 1: Write the failing UI test** in `SyncScreenTest`: with a state whose `chooser` is `Albums(two albums)` the dialog shows both names, the count and `pathHint`; tapping one calls `onChooseGalleryAlbum` with that album; a state `NeedsPermission(PARTIAL)` shows the partial-access text and an "Open settings" button calling `onOpenAppSettings`.
- [ ] **Step 2: Run** `.\gradlew :app:compileDebugAndroidTestKotlin --no-daemon`. Expected: FAIL to compile (new actions missing). The test itself runs in CI's emulator job.
- [ ] **Step 3: Implement.** (a) Empty state and "Change album" entries offer "Gallery album..." (primary) and "Folder..." (existing picker). (b) `SyncScreen` owns a `rememberLauncherForActivityResult(RequestMultiplePermissions())` using `permissionsToRequest`; the "Gallery album..." tap calls `viewModel.onGalleryRequested()` and, when the state is `NeedsPermission(DENIED)`, launches the request; its result calls `viewModel.onPermissionResult(canAskAgain = shouldShowRequestPermissionRationale(...) for any requested permission)`. (c) `onOpenAppSettings` starts `Settings.ACTION_APPLICATION_DETAILS_SETTINGS` for the package. (d) Chooser as an `AlertDialog` with a `LazyColumn` of rows at least 48 dp with `name`, count and `pathHint`; Loading, Empty and Failed states have text. (e) Add `@Preview`s to `SyncPreviews.kt` for the chooser and the three permission cards.
- [ ] **Step 4: Run** the unit tests and `.\gradlew :app:assembleDebug :app:compileDebugAndroidTestKotlin --no-daemon`. Expected: PASS and builds. Manual: install on an emulator (API 34 and the newest image), grant, partial-grant, deny and block in turn and watch the three cards.
- [ ] **Step 5: Commit** `feat(android): gallery album chooser screen (A5)`.

### Task 8: Docs, tracker and the device check

**Files:**
- Modify: `AndroidClient/CLAUDE.md` (album access section: the two key kinds, dispatchers, `MediaAccess`, the permission list), `docs/superpowers/specs/2026-11-08-android-ingestion-sync/tracker.md` (A5 row: status and review links), the A5 spec (status `done` after the merge; open questions 2, 3 and 4 updated with what the device run showed).
- Test: `app/src/androidTest/java/com/memebrowser/app/data/album/MediaStoreAlbumTest.kt` (new; emulator: insert one image into `Pictures/A5Test` through `MediaStore`, assert `MediaStoreAlbumLister` lists the bucket and `MediaStoreAlbumRowSource` returns its row).

- [ ] **Step 1: Write the emulator test** described above; run `.\gradlew :app:compileDebugAndroidTestKotlin --no-daemon` (it runs in CI).
- [ ] **Step 2: Update the docs** listed above, keeping the CLAUDE.md prose consistent with the existing A2 section.
- [ ] **Step 3: Run everything:** `.\gradlew :app:testDebugUnitTest :app:assembleDebug --no-daemon` (expect PASS) and one review iteration by a fresh subagent on the Android axis (readability, lifecycle and concurrency, maintainability) saved to `docs/superpowers/reviews/`, per the epic's working rules.
- [ ] **Step 4: Owner device check** (cannot be done by the agent): `DCIM/3 - memes` appears as one album with the right count; one sync uploads and the inbox shows the original names; revoke the permission in system settings during idle and see "Choose album again"; "Select photos" on Android 14+ shows the partial card and Allow all in settings is picked up by the next refresh.
- [ ] **Step 5: Commit** `docs(android-sync): A5 notes, tracker and tests` and wait for the owner's go-ahead before any merge to `main`.
