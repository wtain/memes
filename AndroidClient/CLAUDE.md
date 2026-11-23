# AndroidClient — dev notes for Claude

## Before committing

Always run unit tests before committing to catch compilation errors and
regressions that would otherwise fail in CI:

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew :app:testDebugUnitTest --no-daemon
```

If a ViewModel constructor changes, search for all usages in both
`src/test` and `src/androidTest` and update them before committing.
A quick grep to find every affected test file:

```powershell
grep -r "ViewModel(" app/src/test, app/src/androidTest
```

## Building locally

The system default Java is JRE 8, but AGP 9.4.0 (see `gradle/libs.versions.toml`) requires Java 17+.
Set `JAVA_HOME` in your shell before running Gradle — do **not** put it in
`gradle.properties` (that would leak into CI):

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"   # JDK 21
.\gradlew assembleDebug
```

Install to a connected device:
```powershell
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" install -r app\build\outputs\apk\debug\app-debug.apk
```

## Image loading

The backend returns `imageUrl` as a **relative path** (`/api/images/{id}`), not
an absolute URL. Coil requires an absolute URL, so callers must prepend
`http://localhost`:

```kotlin
AsyncImage(model = "http://localhost${meme.imageUrl}", ...)
```

The OkHttp application interceptor in `NetworkModule` rewrites `localhost` to
the configured server host/port at request time, so environment switching still
works correctly.

## CI runs instrumented tests on API 31, not the real minSdk (29) — temporary

`minSdk = 29` in `app/build.gradle.kts`, but `.github/workflows/android-ci.yml`'s
`instrumented-tests` job runs on an **API 31** emulator, not 29. This is a
workaround, not a design choice — don't "fix" it back to 29 without checking
whether the underlying issue is resolved (see below).

**Why:** `androidx.activity:activity-compose` 1.13.0+ makes `ComponentActivity`
implement a callback referencing `android.app.PictureInPictureUiState`, a class
that only exists on API 31+. MockK's Android proxy reflectively scans all
declared methods on a class when deciding whether to intercept a call, and that
scan touches the missing class on API <31, crashing with
`ClassNotFoundException: android.app.PictureInPictureUiState` — in *any*
instrumented test that pauses an Activity while any mock exists, not just PiP
tests. Confirmed upstream MockK bug: mockk/mockk#1518. Fix merged upstream
(mockk/mockk#1531) on 2026-06-12 but not in any released mockk version as of
2026-07-21.

**The old low-risk argument no longer holds for album sync (A5).** It used to be
true that the app had no `Build.VERSION.SDK_INT` branches and no permissions that
differ between API 29 and 31. A5 added both: `MediaAccesses.resolve` /
`permissionsToRequest`, `ContextMediaAccessSource` and `Build.VERSION.SDK_INT` in
`SyncScreen` choose between `READ_EXTERNAL_STORAGE` (API 29/30), `READ_MEDIA_IMAGES`
(API 33) and `READ_MEDIA_IMAGES` + `READ_MEDIA_VISUAL_USER_SELECTED` with partial
access (API 34+). CI exercises only API 31, so API 29/30 (READ_EXTERNAL_STORAGE path)
and API 33 and 34+ (READ_MEDIA_* and partial access) are NOT covered by any
automated instrumented test; the JVM tests cover the pure resolution logic only.
Outside album sync the old argument still applies.

**Coverage gap this leaves:** automated instrumented (Espresso/Compose) tests
no longer run against the real minSdk floor. The `smoke-test-min-sdk` CI job
(also in `android-ci.yml`) partially covers this: it installs the real debug
APK on an API 29 emulator and confirms the app launches without crashing —
no MockK, no Espresso, just `adb install` + `adb shell am start` + a logcat
crash check. It won't catch UI-level regressions, only gross launch/install
failures on API 29 specifically.

**To revert:** once mockk ships a release containing #1531, bump the `mockk`
version in `gradle/libs.versions.toml`, change `instrumented-tests`' `api-level`
back to 29, and remove the `smoke-test-min-sdk` job (no longer needed once
instrumented tests cover 29 again).

## Architecture notes

### Detail screen
`MemeDetailViewModel` fires two parallel coroutines on init — `loadMeme()` and
`loadSimilar()` — so the main image and similar-images strip load independently.
A failure in `loadSimilar` is intentionally silent (no error shown to the user).

### Similar images
Endpoint: `GET /api/images/{id}/similar` → `MemeSearchResponse`.
Rendered as a horizontal `LazyRow` of 80 dp thumbnails at the bottom of the
detail bottom sheet. Tapping a thumbnail pushes a new `detail/{id}` destination
onto the back stack.

### Album sync tracker (A1)
Epic: `docs/superpowers/specs/2026-11-08-android-ingestion-sync/`; design in its `2026-11-08-a1-tracker-data-layer-design.md`.
Code in `data/tracker/` (Room `TrackerDatabase`, schema JSON in `app/schemas/`, Hilt in `di/TrackerModule`), data layer only.
- One `tracked_files` row per `(environmentId, albumTreeUri, documentId)`; state `PENDING` / `UPLOADED` / `REJECTED` with a reason
  (`unsupported_type` and `too_large` are set locally by `SupportedImages`, the single place for the MIME set and 20 MB limit).
- D2: a scan (`TrackerRepository.syncScan`) never changes the state of an existing row, so an edited uploaded file is not re-sent;
  rows not listed get `presentInAlbum = false` (never deleted), leave Y but an `UPLOADED` one stays in X.
- Per-environment album and the global batch size (1..50, default 50) live in `TrackerSettingsStore` (DataStore `tracker_settings`).
- One Mutex serialises `syncScan`/`resetEnvironment`/`changeAlbum`/`clearAlbum`; A2 must cancel the sync job before an album change
  and must not call `syncScan` with a failed listing (an empty list needs `allowEmpty = true`). `markRejected` never demotes UPLOADED.
- The tracker DB and DataStore are excluded from Android backup (`res/xml/data_extraction_rules.xml`, `backup_rules.xml`).
- JVM tests use `FakeTrackerDao`; the SQL itself is only exercised by `androidTest/.../TrackerDaoTest` (needs a device).
- A7 (database version 2, `MIGRATION_1_2` in `TrackerDatabase.kt`, registered in `TrackerModule`, no destructive fallback): column `uploadAnswer`
  (`TrackerSql.ANSWER_ACCEPTED` / `ANSWER_DUPLICATE`, NULL = not uploaded). `markUploaded(..., answer)` only changes rows that are not UPLOADED yet, so the
  first answer is kept. `TrackerCounts.duplicates` / `unclassified` = UPLOADED rows answered `duplicate` / with no answer (rows uploaded before A7 stay
  unclassified; nothing re-classifies them). Migrations are checked by `androidTest/.../TrackerMigrationTest` (`room-testing`; `app/schemas` is an
  androidTest assets dir); a schema change needs a version bump, a migration and the exported `app/schemas/.../<n>.json` committed.

### Album access and scan (A2)
Design: `docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-a2-album-selection-and-scan-design.md`. Code in `data/album/`, Hilt in `di/AlbumModule`. No screens.
- `AlbumScanner` (`SafAlbumScanner`) lists the IMMEDIATE children of a persisted tree with ONE `ContentResolver.query` on `IO`, never throws, and returns
  `Complete(files)` / `Incomplete` (EXTRA_LOADING, failed query, null cursor) / `PermissionLost` (SecurityException or grant not in `persistedUriPermissions`).
- `AlbumAccess` wraps `persist` / `isGranted` / `release` of the persistable READ grant. `AlbumNames.displayName(treeUri)` and `looksLikeAppOwnFolder(treeUri)` are pure.
- `AlbumSyncCoordinator` (singleton, per environment id) is what A4 calls: `selectAlbum` (first selection), `requestChangeAlbum` -> `ChangeAlbumPlan` (counts to
  wipe, nothing changed) -> `confirmChangeAlbum(plan)`, `refreshScan` (screen open and the refresh button), flows `scanState` / `counts` / `album` / `rejectedFiles`.
- Only a `Complete` listing reaches `TrackerRepository.syncScan` (with `allowEmpty = true`); `Incomplete` and `PermissionLost` leave the tracker untouched.
  `PermissionLost` is a state for A4 to ask for a re-pick (`selectAlbum` / `confirmChangeAlbum` with the same tree re-take the grant and rescan, no wipe, even when the
  grant is still listed: while the scan state is `PermissionLost`, `needsRegrant` is true).
- An album change: take the new grant, `SyncGate.cancelAndAwait(env)`, wipe only that environment and store the new album, release the old grant unless
  another environment uses the same tree, rescan. A3 must bind the real `SyncGate` in `AlbumModule` (today `NoOpSyncGate`).
- `refreshScan` is conflated per environment (a concurrent call waits for and returns the in-flight result); environments never share locks or state.
  It never throws (except caller cancellation): `Failed(TRACKER_ERROR)` for settings/database errors; an album change aborts it (`Failed(CANCELLED)` for its waiters).
- Results A4 must handle: `AlbumChangeResult.Applied` / `Unchanged` / `GrantFailed` (temporary picker grant gone: re-pick) / `RequiresConfirmation` /
  `PlanStale` (stored album changed since `requestChangeAlbum`: rebuild the plan) / `Failed` (storage error, nothing changed that matters). Keep the confirm dialog in
  the picker's task. After the gate the commit runs in `NonCancellable`, so leaving the screen cannot leak a grant.
- The scanner has a 60 s timeout and aborts the provider query on cancellation; an empty listing whose root no longer resolves (ejected card, deleted folder)
  is `Incomplete("album unavailable")`, never an empty `Complete`. `looksLikeAppOwnFolder` only fires for the `com.android.externalstorage.documents` authority.

### Album source: MediaStore (A5)
Design: `docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-21-a5-mediastore-album-source-design.md`. Code in `data/album/` and `data/sync/`, UI in `ui/sync/SyncChooser.kt`.
Gallery albums (MediaStore buckets) are a second album source next to the SAF folder picker, for folders the picker refuses (Samsung `DCIM/...`).
- Two key kinds share the `albumTreeUri` column, no tracker migration: a SAF `content://...tree...` URI, or `mediastore-bucket:<BUCKET_ID>/<URL-encoded bucket name>`
  (`AlbumKey.mediaStore` / `isMediaStore` / `bucketId` / `bucketName`, pure). `documentId` of a MediaStore file is the decimal `_ID`.
- Three dispatchers pick the implementation by key shape and leave the SAF path unchanged: `DispatchingAlbumAccess` (MediaStore: `MediaStoreAlbumAccess`, no per-album
  grant, `release` is a no-op), `DispatchingAlbumRowSource` (`MediaStoreAlbumRowSource`, one query by `BUCKET_ID`, `DATE_MODIFIED` seconds x 1000) and
  `DispatchingAlbumFileSource` in `data/sync/` (`MediaStoreAlbumFileSource` opens `Images.Media.EXTERNAL_CONTENT_URI/<_ID>`; `FileNotFoundException` still means "vanished").
- `MediaAccess` (`FULL` / `PARTIAL` / `DENIED` / `BLOCKED`, `MediaAccesses.resolve` is pure, `MediaAccessSource` is the seam because `SDK_INT` is 0 on the JVM). Only `FULL`
  scans, lists albums and counts as granted; `PARTIAL` (Android 14+ "Select photos") shows the allow-all-photos card and never scans; `DENIED` can ask again; `BLOCKED` points to system settings.
  Permissions (`MediaAccesses.permissionsToRequest`): API 34+ `READ_MEDIA_IMAGES` + `READ_MEDIA_VISUAL_USER_SELECTED`, API 33 `READ_MEDIA_IMAGES`, below that `READ_EXTERNAL_STORAGE`.
- `AlbumLister` (`MediaStoreAlbumLister`) lists the albums for the chooser with one query on the images collection (`IS_PENDING = 0`), aggregated in memory by the pure
  `GalleryAlbums.aggregate`, newest first; results `Albums` / `PermissionDenied` / `Failed`, never throws except cancellation.
- An EMPTY bucket means the album is gone: `MediaStoreRows.listing` sets `rootResolves = rows.isNotEmpty()`, so the scanner answers `Incomplete("album unavailable")`, never an empty `Complete`.
- The chooser UI is `GalleryChooserDialog` (`ui/sync/SyncChooser.kt`, state `ChooserState` in `SyncViewModel`); the permission request is launched once per tap.
- JVM tests cover the pure parts and fakes; `androidTest/.../data/album/MediaStoreAlbumTest` seeds a JPEG into `Pictures/A5Test` and covers the real query, file open and
  millisecond mapping (needs an emulator, CI only). The real device behaviour is the owner's checklist in the A5 spec.

### Sync engine (A3)
Design: `docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-a3-sync-engine-design.md`. Code in `data/sync/`, upload seam in `data/repository/BatchUploader.kt`, Hilt in `di/SyncModule`. No screens.
- `SyncEngine` (singleton, per environment id) is what A4 calls: `start(env)` (non-suspending, runs in the engine's own scope, false if already running), `stop(env)`,
  `cancelAndAwait(env)`, `state(env): StateFlow<SyncState>`, `isRunning(env)`. States: `Idle`, `Running`, `Waiting(RATE_LIMITED | PACING)`, `Stopping`, `Finished`, `Stopped`, `Failed(SyncError)`.
- Loop: `nextPending(batch size read before each batch)` -> one multipart POST per batch -> results matched by the response's per-file `index` -> `markUploaded`
  (`accepted` and `duplicate`) / `markRejected(reason)`. Never UPLOADED without an explicit entry; unanswered files stay PENDING. No implicit scan (the user refreshes).
- Errors: network/5xx -> `Failed` and stay pending (unless auto-retry is on, see "Background sync (A6)"); the app paces itself to 9 requests per 60 s (`Waiting(PACING)`), a 429 anyway -> wait 60 s (Retry-After
  seconds or HTTP-date wins, max 300 s) and retry, 10 in a row -> `Failed`; 503/507 -> `Failed` with their own `SyncError`; other 4xx -> `Failed(UNEXPECTED)`.
  HTTP 500 -> the batch is retried file by file; a file failing alone is rejected `server_error`, 3 in a row -> `Failed(SERVER)`. Only a vanished document
  (`FileNotFoundException`) is rejected `file_unavailable`; any other read failure -> `Failed(FILE_READ_ERROR)` with files pending (grant gone: `PERMISSION_LOST`).
  A backend without per-file `index` -> `Failed(SERVER)` "backend too old". After `stop` a run always ends `Stopped`. Foreground-only here; the foreground service that keeps a run alive in the background is A6 (see "Background sync (A6)").
- Uploads use their own OkHttp client (long timeouts) and the SYNCED environment's base URL, not the selected one. `EngineSyncGate` is the `SyncGate` bound in `AlbumModule`.

### Background sync (A6)
Design: `docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-23-a6-background-sync-and-auto-retry-design.md`. Code in `data/sync/` and `service/`.
- Seams: `NetworkGate` (`status(wifiOnly)` -> `OK | NEEDS_WIFI | NONE`, `awaitChange()`; real `AndroidNetworkGate`, test default `AlwaysOnline`), `SyncServiceStarter` (`ensureRunning()`; real `AndroidServiceStarter`,
  `NoServiceStarter` in tests) and `SyncEngine.activeEnvironments` (StateFlow of the environments with an active run; one state writer under the engine lock). Bound in `SyncModule`.
- Settings `autoRetry` and `wifiOnly` live in `TrackerSettingsStore` (read via `TrackerRepository`), both default ON, are global, and are switched on the Album sync screen. The engine reads them
  at the moment they matter (each failure, each network check), so a change applies mid-run.
- Retry (only when `autoRetry`): retryable = network error, file read error with the grant intact, 429 after its 10 waits, 5xx (HTTP 500 goes through the isolate pass first). Not retried: 503, 507, permission lost, no album, backend too old,
  poison streak, other 4xx, unexpected. Delays 15/30/60/120/300/300... s (`RETRY_DELAYS_SECONDS`), at most 8 retries (`MAX_RETRY_ATTEMPTS`) then the `Failed`; the counter resets only when files were uploaded
  or found duplicate since the last failure (rejections, `server_error` included, never reset it). Each attempt is a new run, so the batch number and the 429 counter restart; the summary keeps adding up and the
  poison streak (in `Progress`) carries over, reset only by a mapped 200 response. State during the wait: `Waiting(RETRY_BACKOFF, resumeAt, attempt)`.
- Network waits: before each batch (and before each file of the HTTP-500 isolate pass) `awaitNetwork`: `NEEDS_WIFI` waits when Wi-Fi only is on and the network is metered; `NONE` waits only when auto-retry is on
  (else the upload fails NETWORK as before). Reasons `Waiting(NEEDS_WIFI | NO_NETWORK)`; rechecked on every network event and at least every 5 s.
- `SyncService` (foreground, type `dataSync`): started by the engine on `start`, one notification per environment, the oldest running environment holds the foreground slot and passes it on when it ends.
  Terminal states (finished, stopped, failed) post a dismissible notification that survives while another environment runs. Ongoing ones have a Stop action (`ACTION_STOP` + environment id; works with the
  app in the background). Tapping opens `MainActivity` with extra `route = sync`; `NavGraph` accepts only whitelisted routes from it (the activity is exported). Android 15 caps `dataSync` at 6 hours:
  `onTimeout` stops the runs and posts "Android paused the sync after 6 hours. Tap to continue." A run is not resumed after process death (the user restarts it; the tracker keeps the rest pending).
- `SyncNotificationMapper` (pure, JVM-tested) maps a `SyncState` + counts to the notification model; `SyncNotificationPlan` holds the id, foreground-choice and cleanup rules. Small icon: framework `stat_sys_upload`.
- `POST_NOTIFICATIONS` (Android 13+) is asked once per screen visit before "Sync now" while permission is missing (`shouldAskForNotifications`, `rememberSaveable`); Android itself stops showing the dialog
  after repeated refusals; a refusal shows a hint, the sync still runs.
- The foreground slot is chosen after `reconcile()`, from the followed environments only (`SyncNotificationPlan.reconcileForeground` -> `Keep | Switch | Stop`), so a run that ends between the start and the
  reconcile cannot leave the placeholder foreground forever. Swiping the app from recents: on stock Android the foreground service usually keeps going; on OEMs that kill it the sync stops (restart continues the rest).

### Sync screen (A4)
Design: `docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-a4-sync-screen-design.md`. Code in `ui/sync/` (`SyncViewModel`, `SyncScreen`, `SyncMappers`), route `sync`.
- The drawer entry (now "Album sync") opens it; it links to the unchanged manual `UploadScreen` (route `upload`) with "Upload individual files...".
- `SyncViewModel` exposes ONE `SyncUiState`; `flatMapLatest` on the selected environment id re-binds album, counts, scan, sync state and rejected list. The change-album plan
  lives in the ViewModel (the picker's temporary grant is process-bound; never recompute it from the Uri). Texts and button enablement are pure functions in `SyncMappers.kt`.
- `SyncContent` is stateless (`SyncActions` callbacks); `SyncScreen` owns the `OpenDocumentTree` launcher, the ON_START refresh (throttled 30 s per environment in the ViewModel)
  and `View.keepScreenOn` while a run is active. Strings are inline English like the rest of the app.
- Screen-local state (message, busy, dismissed result, rejected sub-view, plan) is per environment; actions use the environment id of the state on screen. The change dialog
  closes when the stored album is no longer the plan's old one (commit), the rescan shows as scan state. Unexpected errors show a generic message and are logged, never shown raw.
  "Choose album again" is shown once (`lostAccessCardVisible` / `repickInSyncCard`). Retry queries only touch rows still `presentInAlbum`. `SyncPreviews.kt` holds `@Preview`s.
- Status card (A7): below "X of Y uploaded" (X still includes duplicates) and the bar, `duplicatesText` ("N already on the server (duplicates)", tag
  `sync_duplicates`) and `unclassifiedText` ("N uploaded before this count existed", tag `sync_unclassified`) show only when their count is above 0.
- Rejected files stay skipped; only `server_error` rows can be retried: `TrackerRepository.retryServerErrors` / `retryServerError` (reset to PENDING, under the tracker Mutex).
- ViewModel tests mock the final coordinator/engine/tracker with mockk; the retry SQL itself is only checked on a device (`FakeTrackerDao` mirrors it).
- `androidTest/.../ui/sync/SyncScreenTest` needs a device to run (the androidTest source set compiles since `SearchScreenTest` was updated for `onMemeClick(memeId, searchId)`; verified with `compileDebugAndroidTestKotlin`).

### Settings and startup update check
- `AppSettingsStore` (DataStore `app_settings`): `checkUpdatesOnStartup` (default on), `resultsPerPage` (10..100, default 20, read by `SearchViewModel` before each search),
  `gridMinSizeDp` (100..300, default 160), `skippedUpdateTag`. The album-sync batch size stays in `TrackerSettingsStore` (the engine reads it); the settings screen
  (route `settings`, drawer entry "Settings") and the sync screen both edit that one value.
- `UpdateManager` (singleton, `data/update/`) owns the GitHub check, download and install and one `status` flow; `AboutViewModel` and `StartupUpdateViewModel` share it.
- `StartupUpdateDialogHost` (in `NavGraph`) + the activity-scoped `StartupUpdateViewModel` check once per launch when enabled and offer Yes/No. A silent failed check;
  "No" stores the tag in `skippedUpdateTag` (not offered again at startup; About still offers it).

### Navigation
All screen-to-screen navigation goes through `NavGraph.kt`. Screens receive
callbacks (`onBack`, `onNavigateToMeme`, …) and know nothing about
`NavController` directly.

## Moving synced photos to the trash

"Move synced photos to the trash..." on the Album sync screen (gallery albums only, Android 11+) moves photos the backend already has. Only files whose tracker row is UPLOADED for the SELECTED environment, still present, and not modified after `uploadedAt` qualify (`TrackerDao.removableFiles`); `PhotoRemover.verify` re-checks id, bucket and `DATE_MODIFIED` in MediaStore right before asking, because a file edited after the upload is never re-sent. Two confirmations: the app's dialog with the count, then the system's `MediaStore.createTrashRequest` dialog (chunks of `PhotoRemover.REQUEST_CHUNK`). They go to the system trash (about 30 days, restorable), not a permanent delete. The tracker rows stay UPLOADED, so the "X of Y uploaded" history keeps its count. Folder (SAF) albums are not supported on purpose.
