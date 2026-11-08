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

**Why it's low-risk right now:** this app has zero `Build.VERSION.SDK_INT`
branches anywhere in `app/src/main`, and its only permissions (`INTERNET`,
`ACCESS_NETWORK_STATE`, `REQUEST_INSTALL_PACKAGES`) don't behave differently
between API 29 and 31 — no scoped-storage, media, notification, or Bluetooth
permission changes apply here. There's no version-conditional code path for
the API-29-vs-31 gap to actually hide a bug in.

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

### Album access and scan (A2)
Design: `docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-a2-album-selection-and-scan-design.md`. Code in `data/album/`, Hilt in `di/AlbumModule`. No screens.
- `AlbumScanner` (`SafAlbumScanner`) lists the IMMEDIATE children of a persisted tree with ONE `ContentResolver.query` on `IO`, never throws, and returns
  `Complete(files)` / `Incomplete` (EXTRA_LOADING, failed query, null cursor) / `PermissionLost` (SecurityException or grant not in `persistedUriPermissions`).
- `AlbumAccess` wraps `persist` / `isGranted` / `release` of the persistable READ grant. `AlbumNames.displayName(treeUri)` and `looksLikeAppOwnFolder(treeUri)` are pure.
- `AlbumSyncCoordinator` (singleton, per environment id) is what A4 calls: `selectAlbum` (first selection), `requestChangeAlbum` -> `ChangeAlbumPlan` (counts to
  wipe, nothing changed) -> `confirmChangeAlbum(plan)`, `refreshScan` (screen open and the refresh button), flows `scanState` / `counts` / `album` / `rejectedFiles`.
- Only a `Complete` listing reaches `TrackerRepository.syncScan` (with `allowEmpty = true`); `Incomplete` and `PermissionLost` leave the tracker untouched.
  `PermissionLost` is a state for A4 to ask for a re-pick (`selectAlbum` with the same tree re-takes the grant, no wipe).
- An album change: take the new grant, `SyncGate.cancelAndAwait(env)`, wipe only that environment and store the new album, release the old grant unless
  another environment uses the same tree, rescan. A3 must bind the real `SyncGate` in `AlbumModule` (today `NoOpSyncGate`).
- `refreshScan` is conflated per environment (a concurrent call waits for and returns the in-flight result); environments never share locks or state.
  It never throws (except caller cancellation): `Failed(TRACKER_ERROR)` for settings/database errors; an album change aborts it (`Failed(CANCELLED)` for its waiters).
- Results A4 must handle: `AlbumChangeResult.Applied` / `Unchanged` / `GrantFailed` (temporary picker grant gone: re-pick) / `RequiresConfirmation` /
  `PlanStale` (stored album changed since `requestChangeAlbum`: rebuild the plan) / `Failed` (storage error, nothing changed that matters). Keep the confirm dialog in
  the picker's task. After the gate the commit runs in `NonCancellable`, so leaving the screen cannot leak a grant.
- The scanner has a 60 s timeout and aborts the provider query on cancellation; an empty listing whose root no longer resolves (ejected card, deleted folder)
  is `Incomplete("album unavailable")`, never an empty `Complete`. `looksLikeAppOwnFolder` only fires for the `com.android.externalstorage.documents` authority.

### Sync engine (A3)
Design: `docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-a3-sync-engine-design.md`. Code in `data/sync/`, upload seam in `data/repository/BatchUploader.kt`, Hilt in `di/SyncModule`. No screens.
- `SyncEngine` (singleton, per environment id) is what A4 calls: `start(env)` (non-suspending, runs in the engine's own scope, false if already running), `stop(env)`,
  `cancelAndAwait(env)`, `state(env): StateFlow<SyncState>`, `isRunning(env)`. States: `Idle`, `Running`, `Waiting(RATE_LIMITED)`, `Stopping`, `Finished`, `Stopped`, `Failed(SyncError)`.
- Loop: `nextPending(batch size read before each batch)` -> one multipart POST per batch -> results matched by the response's per-file `index` -> `markUploaded`
  (`accepted` and `duplicate`) / `markRejected(reason)`. Never UPLOADED without an explicit entry; unanswered files stay PENDING. No implicit scan (the user refreshes).
- Errors: network/5xx -> `Failed` and stay pending (no auto retry); 429 -> wait 60 s (Retry-After wins) and retry the same batch, 10 in a row -> `Failed`; 503/507 -> `Failed`
  with their own `SyncError`; other 4xx -> `Failed(UNEXPECTED)`; unreadable file -> rejected `file_unavailable`.
- Uploads use their own OkHttp client (long timeouts) and the SYNCED environment's base URL, not the selected one. `EngineSyncGate` is the `SyncGate` bound in `AlbumModule`.

### Navigation
All screen-to-screen navigation goes through `NavGraph.kt`. Screens receive
callbacks (`onBack`, `onNavigateToMeme`, …) and know nothing about
`NavController` directly.