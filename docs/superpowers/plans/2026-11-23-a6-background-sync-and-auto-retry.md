# A6 Background Sync, Notification and Auto-Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Album sync keeps running in the background with a per-environment progress notification, retries transient failures on its own, waits for the network, and can be limited to unmetered (Wi-Fi) connections.

**Architecture:** `SyncEngine` keeps owning the work. It gains two seams (`NetworkGate`, `SyncServiceStarter`), two settings (`autoRetry`, `wifiOnly`), three new wait reasons and a retry wrapper around `runLoop`. A new foreground `SyncService` (type `dataSync`) only keeps the process alive and renders notifications from the engine's state flows through a pure `SyncNotificationMapper`.

**Tech Stack:** Kotlin, Hilt, DataStore, kotlinx-coroutines-test (virtual time), Compose, `ConnectivityManager`, `NotificationCompat`. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-23-a6-background-sync-and-auto-retry-design.md` (approved 2026-11-23). All paths below are under `AndroidClient/app/src/` unless stated; package root `com.memebrowser.app`.

## Global Constraints

- Defaults: `autoRetry = true`, `wifiOnly = true`. Both global (not per environment), stored in DataStore `tracker_settings` next to `batch_size`, read by the engine at the moment they matter (a change applies to a running sync).
- Retryable errors, only when `autoRetry` is on: `NETWORK`, `SERVER` raised from an HTTP 5xx other than 503/507, `FILE_READ_ERROR`, `RATE_LIMITED` (after its 10 waits). Never retried: `NO_ALBUM`, `PERMISSION_LOST`, `INBOX_FULL_OR_UNWRITABLE`, `DISK_LOW`, "backend too old", the 3-poison-files `SERVER` failure, `UNEXPECTED`.
- Retry delays: 15 s, 30 s, 60 s, 120 s, 300 s, then 300 s; at most 8 attempts in a row; the counter resets when a batch settles at least one file. After the 8th the run ends `Failed` as today. Stop during any wait ends the run `Stopped`.
- Waits: `Waiting(NO_NETWORK)` only when `autoRetry` is on; `Waiting(NEEDS_WIFI)` whenever `wifiOnly` is on and no unmetered validated network exists but a metered one does; `NO_NETWORK` when no validated network exists at all. Wi-Fi only = `NET_CAPABILITY_NOT_METERED`.
- Pacing (9 requests per 60 s), upload rules and tracker rules do not change. After every wait the pending files are re-read (existing `continue` pattern).
- Service: foreground type `dataSync`; manifest permissions `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_DATA_SYNC`, `POST_NOTIFICATIONS` (`ACCESS_NETWORK_STATE` already exists). One notification per environment (stable id from the environment id), channel id `album_sync` named "Album sync", importance LOW. Android 15 `onTimeout`: stop every run, final text "Android paused the sync after 6 hours. Tap to continue."
- Notification texts: Running "<Env>: syncing" / "120 of 400 uploaded"; waits "Waiting for Wi-Fi", "Waiting for network", "Retrying in 30 s (attempt 2 of 8)", "Pausing to respect the server limit"; finished "<Env>: sync finished, 400 uploaded"; stopped "<Env>: sync stopped"; failed uses `syncErrorMessage(error)`.
- `POST_NOTIFICATIONS` is requested the first time the user taps "Sync now" (Android 13+); refusal never blocks a sync.
- Strings are inline English (project convention). Before every commit: `$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"; .\gradlew :app:testDebugUnitTest --no-daemon` from `AndroidClient/`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01R83wsN3pgzbnys2j38vt7h`. Work on branch `android-sync-quick-fixes`; do not merge or push.

## Review Focus

- The user switches Wi-Fi only on or off while a sync waits: the wait must end or start without a restart (settings re-read, gate re-evaluated).
- A network drop that happens inside an upload (not between batches): the `NETWORK` failure must go through the retry path and not lose files (they stay PENDING).
- Stop pressed while the run is in `RETRY_BACKOFF`, `NO_NETWORK` or `NEEDS_WIFI`: ends `Stopped`, no further upload.
- Two environments running at once: independent retry counters, two notifications, the service stops only when both ended.
- Retry after a partial run: summary counts keep adding up across attempts (same `Progress`), not reset.

---

### Task 1: Settings `autoRetry` and `wifiOnly`

**Files:**
- Modify: `main/java/com/memebrowser/app/data/tracker/TrackerSettingsStore.kt`, `data/tracker/TrackerRepository.kt`, `data/sync/SyncEngine.kt` (`SyncSettingsSource`, `TrackerSyncSettings`)
- Modify: `test/.../data/sync/SyncFakes.kt` (`FakeSyncSettings`)
- Test: `test/.../data/tracker/TrackerSettingsStoreTest.kt` (create if absent, same DataStore-in-temp-folder pattern as `SyncHarness`)

**Interfaces:**
- Produces: `TrackerSettingsStore.autoRetry: Flow<Boolean>` (default true), `setAutoRetry(Boolean)`, `wifiOnly: Flow<Boolean>` (default true), `setWifiOnly(Boolean)`; same four on `TrackerRepository` (`autoRetry()`, `wifiOnly()` as `Flow<Boolean>`, `setAutoRetry`, `setWifiOnly`); `SyncSettingsSource.autoRetry(): Boolean` and `wifiOnly(): Boolean` (suspend); `FakeSyncSettings(var autoRetry: Boolean = true, var wifiOnly: Boolean = false)` — the fake defaults wifiOnly to false so existing engine tests keep their behaviour.

- [ ] **Step 1: Failing test** `TrackerSettingsStoreTest`: `defaults_areOn` (both flows emit true on an empty store), `setAutoRetry_false_isStored`, `setWifiOnly_false_isStored`.
- [ ] **Step 2: Run** `.\gradlew :app:testDebugUnitTest --no-daemon --tests "*TrackerSettingsStoreTest"`; expected FAIL (unresolved reference).
- [ ] **Step 3: Implement** the keys (`booleanPreferencesKey("auto_retry")`, `("wifi_only")`), flows, setters, repository wrappers, and the two `SyncSettingsSource` methods in `TrackerSyncSettings` (`.first()`); update `FakeSyncSettings`.
- [ ] **Step 4: Run** the full unit suite; expected PASS.
- [ ] **Step 5: Commit** `feat(android): auto-retry and Wi-Fi-only settings (A6)`.

### Task 2: `NetworkGate` seam and the network waits in the engine

**Files:**
- Create: `main/java/com/memebrowser/app/data/sync/NetworkGate.kt`
- Modify: `data/sync/SyncState.kt` (`WaitReason`), `data/sync/SyncEngine.kt`, `ui/sync/SyncMappers.kt` (the `when` over `WaitReason` must stay exhaustive: add the three new texts "Waiting for Wi-Fi", "Waiting for network", "Retrying after an error, resuming at <time>")
- Modify: `test/.../data/sync/SyncFakes.kt` (`FakeNetworkGate`, `SyncHarness.network`)
- Test: `test/.../data/sync/SyncEngineNetworkTest.kt`

**Interfaces:**
- Produces: `enum class NetworkStatus { OK, NEEDS_WIFI, NONE }`; `interface NetworkGate { fun status(wifiOnly: Boolean): NetworkStatus; suspend fun awaitUsable(wifiOnly: Boolean) }` (`awaitUsable` suspends until `status(wifiOnly) == OK`, re-evaluating on every network change); `object AlwaysOnline : NetworkGate`; `WaitReason` gains `NO_NETWORK`, `NEEDS_WIFI`, `RETRY_BACKOFF`. `SyncEngine` internal constructor gains `network: NetworkGate = AlwaysOnline` as its LAST parameter (after `outcomes`); the `@Inject` constructor takes `network: NetworkGate`.
- `FakeNetworkGate`: `var status: NetworkStatus`, `fun set(s)` that resumes waiters; `status(wifiOnly)` returns `NEEDS_WIFI` only if `wifiOnly` and the scripted status is `NEEDS_WIFI`.

- [ ] **Step 1: Failing tests** in `SyncEngineNetworkTest` (virtual time, `SyncHarness` extended with `network`): 
  - `wifiOnly_onMobile_waitsForWifiThenUploads`: status NEEDS_WIFI, wifiOnly true, 3 pending; start; state is `Waiting(NEEDS_WIFI)` and `uploader.calls` empty; `network.set(OK)`; run ends `Finished`, 3 uploaded.
  - `noNetwork_autoRetryOn_waitsThenResumes` (same with NONE, `Waiting(NO_NETWORK)`).
  - `noNetwork_autoRetryOff_isNotWaitedFor` (autoRetry false, NONE, uploader returns `NetworkError`): ends `Failed(NETWORK)` as today.
  - `wifiOnly_offWhileWaiting_endsTheWait`: waiting for Wi-Fi, set `settings.wifiOnly = false`, `network.set(NEEDS_WIFI)` again (gate re-evaluates with `wifiOnly=false`): uploads proceed. 
  - `stop_whileWaitingForNetwork_endsStopped`.
  - `pendingIsReReadAfterTheWait`: a file added during the wait is part of the run.
- [ ] **Step 2: Run** `--tests "*SyncEngineNetworkTest"`; expected FAIL.
- [ ] **Step 3: Implement** `NetworkGate.kt` and the gate in `runLoop`: after the pacing check and before `precheck`, `when (network.status(settings.wifiOnly()))`: `OK` goes on; `NEEDS_WIFI` → publish `Waiting(NEEDS_WIFI, resumeAt = 0, attempt = 0, summary)`, `network.awaitUsable(wifiOnly)`, `continue`; `NONE` → if `settings.autoRetry()` same with `NO_NETWORK`, else go on (the upload then fails `NETWORK`). `awaitUsable` must be cancellable and re-read `wifiOnly` through a lambda-free design: the engine loops `while (network.status(settings.wifiOnly()) != OK) delay/await` — choose the simplest that makes the `wifiOnly_offWhileWaiting` test pass (e.g. engine awaits `network.changes` signal then re-evaluates; the plan's contract only fixes `status` and `awaitUsable`).
- [ ] **Step 4: Run** the full unit suite; expected PASS.
- [ ] **Step 5: Commit** `feat(android): sync waits for the network and for Wi-Fi (A6)`.

### Task 3: Retry with backoff inside the run

**Files:**
- Modify: `data/sync/SyncEngine.kt`
- Test: `test/.../data/sync/SyncEngineRetryTest.kt`

**Interfaces:**
- Produces: `SyncEngine.RETRY_DELAYS_SECONDS = listOf(15L, 30L, 60L, 120L, 300L)` (index clamped to the last), `MAX_RETRY_ATTEMPTS = 8` in the companion; `Waiting(RETRY_BACKOFF, resumeAt, attempt, summary)` where `attempt` is 1-based.
- A retryable failure is recognised inside the engine (private `isRetryable(Failed)`): `error` in {NETWORK, FILE_READ_ERROR, RATE_LIMITED} or `SERVER` with `message` not equal to `BACKEND_TOO_OLD_MESSAGE` and not starting with the poison-streak text. Cleaner and preferred: carry a `retryable: Boolean` on a private result type from `terminalFor` instead of parsing messages; the builder chooses, behaviour is fixed by the tests.

- [ ] **Step 1: Failing tests** in `SyncEngineRetryTest`:
  - `networkError_retriesWithGrowingDelays`: uploader returns `NetworkError` 3 times then accepts; state sequence contains `Waiting(RETRY_BACKOFF)` with attempts 1,2,3 and `resumeAt - now` of 15 s, 30 s, 60 s; ends `Finished`; no file lost.
  - `attemptsAreCapped`: always `NetworkError`; exactly 8 retry waits, then `Failed(NETWORK)`; summary preserved.
  - `counterResetsAfterProgress`: fail, fail, accept a batch, fail again → the next wait is 15 s again (attempt 1).
  - `autoRetryOff_failsAsBefore`.
  - `notRetried`: `INBOX_FULL_OR_UNWRITABLE` (503), `DISK_LOW` (507), `PERMISSION_LOST`, backend-too-old, three poison files → each ends `Failed` immediately, no `RETRY_BACKOFF`.
  - `http5xx_retried_503_not`.
  - `stop_duringBackoff_endsStopped_noMoreUploads`.
  - `summaryKeepsAddingUpAcrossAttempts`: 100 files, batch 50, second batch fails once → final `Finished` summary `uploaded = 100`.
  - `settingChangeAppliesMidRun`: `autoRetry` switched off while waiting in backoff → after the wait the next failure ends the run.
- [ ] **Step 2: Run** `--tests "*SyncEngineRetryTest"`; expected FAIL.
- [ ] **Step 3: Implement** the wrapper in `runEnvironment`: loop `runLoop` while the result is a retryable `Failed` and `settings.autoRetry()` and attempts < 8; publish the backoff `Waiting`, `delay`, repeat with the same `Progress` and pacer; reset the attempt counter when `progress.summary().settled` grew since the previous attempt.
- [ ] **Step 4: Run** the full unit suite (existing engine tests must still pass: set `FakeSyncSettings.autoRetry = false` in the harness default if an old test expects an immediate `Failed(NETWORK)` and record that in the commit body).
- [ ] **Step 5: Commit** `feat(android): auto-retry with growing delays inside a sync run (A6)`.

### Task 4: Active environments and the service-starter seam

**Files:**
- Modify: `data/sync/SyncEngine.kt`
- Create: `data/sync/SyncServiceStarter.kt`
- Test: `test/.../data/sync/SyncEngineActiveTest.kt`

**Interfaces:**
- Produces: `SyncEngine.activeEnvironments: StateFlow<Set<String>>` (environments whose state `isActive`, updated on every `publish`); `interface SyncServiceStarter { fun ensureRunning() }`, `object NoServiceStarter`; `SyncEngine` internal constructor gains `starter: SyncServiceStarter = NoServiceStarter` after `network`; the `@Inject` constructor takes it. `start(env)` calls `starter.ensureRunning()` after the state is set to Running (only when it actually starts).

- [ ] **Step 1: Failing tests**: `activeEnvironments_tracksStartAndEnd` (empty → {env} → empty after Finished/Failed/Stopped), `twoEnvironments_areIndependent`, `start_callsTheStarterOnce_notWhenAlreadyRunning` (counting fake starter).
- [ ] **Step 2: Run** `--tests "*SyncEngineActiveTest"`; expected FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite; PASS.
- [ ] **Step 5: Commit** `feat(android): engine reports active environments and asks for the service (A6)`.

### Task 5: Notification mapper and wait texts

**Files:**
- Create: `main/java/com/memebrowser/app/data/sync/SyncNotificationMapper.kt` (pure, no Android types)
- Test: `test/.../data/sync/SyncNotificationMapperTest.kt`

**Interfaces:**
- Produces: `data class SyncNotificationModel(val title: String, val text: String, val progress: Progress?, val ongoing: Boolean, val canStop: Boolean)` with `data class Progress(val done: Int, val total: Int)`; `object SyncNotificationMapper { fun map(environmentName: String, state: SyncState, counts: TrackerCounts, now: Long): SyncNotificationModel? }` — null for `Idle`. `done = counts.uploaded + counts.rejected`, `total = counts.supported`; no progress when `total == 0`. Texts exactly as in Global Constraints; retry text uses `ceil((resumeAt - now)/1000)` seconds and `attempt of 8`; `Stopping` shows "Stopping...". Failed/Finished/Stopped: `ongoing = false`, `canStop = false`, no progress.
- Consumes: `syncErrorMessage(SyncError)` from `ui/sync/SyncMappers.kt`, `TrackerCounts`.

- [ ] **Step 1: Failing tests**: one per state and wait reason with the exact strings (`"General: syncing"`, `"120 of 400 uploaded"`, `"Retrying in 30 s (attempt 2 of 8)"`, `"General: sync finished, 400 uploaded"`, the failure uses `syncErrorMessage`), `idle_isNull`, `zeroSupported_hasNoProgress`.
- [ ] **Step 2: Run** `--tests "*SyncNotificationMapperTest"`; FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite; PASS.
- [ ] **Step 5: Commit** `feat(android): sync notification content mapper (A6)`.

### Task 6: Android plumbing: `NetworkGate`, `SyncService`, manifest, DI

**Files:**
- Create: `main/java/com/memebrowser/app/data/sync/AndroidNetworkGate.kt`, `service/SyncService.kt`, `service/SyncNotifications.kt` (channel creation, builder from `SyncNotificationModel`, ids, Stop and content `PendingIntent`s), `service/AndroidServiceStarter.kt`
- Modify: `main/AndroidManifest.xml`, `di/SyncModule.kt` (bind `NetworkGate`, `SyncServiceStarter`), `MainActivity.kt` + `ui/NavGraph.kt` (open route `sync` when launched from a notification: intent extra `route`), `data/sync/SyncEngine.kt` only if the Stop action needs nothing new (it uses `stop(env)`)
- Test: compile only (`.\gradlew assembleDebug` and `:app:compileDebugAndroidTestKotlin`); the mapper (Task 5) carries the logic tests.

**Interfaces:**
- Consumes: `SyncEngine.activeEnvironments`, `SyncEngine.state(env)`, `SyncEngine.stop(env)`, `AlbumSyncCoordinator.counts(env)`, `EnvironmentStore.environments` (id to display name), `SyncNotificationMapper`.
- Produces: `AndroidNetworkGate(context)` over `ConnectivityManager.registerDefaultNetworkCallback` (validated = `NET_CAPABILITY_VALIDATED`, unmetered = `NET_CAPABILITY_NOT_METERED`); `SyncService : Service` (`@AndroidEntryPoint`), `ACTION_STOP` intent with extra `environmentId`.

- [ ] **Step 1: Manifest**: permissions `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_DATA_SYNC`, `POST_NOTIFICATIONS`; `<service android:name=".service.SyncService" android:exported="false" android:foregroundServiceType="dataSync" />`.
- [ ] **Step 2: `AndroidNetworkGate`** implementing the Task 2 contract; `status` computed from the current default network's capabilities; `awaitUsable` resumes on `onCapabilitiesChanged`/`onAvailable`/`onLost` (a `MutableStateFlow` tick is enough).
- [ ] **Step 3: `SyncService`**: on start, `startForeground` immediately with a placeholder notification for the oldest active environment (Android requires it within seconds); collect `activeEnvironments` plus each environment's state and counts; update the foreground notification and `notify` the others; when the foreground environment ends, re-promote the next with `startForeground`; post terminal notifications (dismissible) and `stopSelf()` when no environment is active; `onTimeout(startId, fgsType)`: stop every active run, post "Android paused the sync after 6 hours. Tap to continue.". `ACTION_STOP` calls `engine.stop(envId)`. Notification tap: `PendingIntent` to `MainActivity` with extra `route = "sync"`.
- [ ] **Step 4: `AndroidServiceStarter.ensureRunning()`**: `ContextCompat.startForegroundService(context, Intent(context, SyncService::class.java))`; bind it and the gate in `SyncModule`.
- [ ] **Step 5: Route**: `MainActivity` passes `intent.getStringExtra("route")` into `NavGraph`, which navigates once to `sync` with a `LaunchedEffect`.
- [ ] **Step 6: Verify** `.\gradlew assembleDebug` and `:app:compileDebugAndroidTestKotlin` succeed; the full unit suite passes.
- [ ] **Step 7: Commit** `feat(android): foreground sync service with per-environment notifications (A6)`.

### Task 7: UI: switches, notification permission, hint

**Files:**
- Modify: `ui/sync/SyncViewModel.kt` (`SyncUiState.autoRetry`, `wifiOnly`; setters), `ui/sync/SyncScreen.kt`, `ui/sync/SyncMappers.kt` (hint text, wait texts if not done in Task 2)
- Test: `test/.../ui/sync/SyncViewModelTest.kt`, `androidTest/.../ui/sync/SyncScreenTest.kt`, `SyncMappersTest`

**Interfaces:**
- Consumes: `TrackerRepository.autoRetry()/wifiOnly()/setAutoRetry/setWifiOnly` (Task 1). Produces: `SyncActions.onAutoRetryChange(Boolean)`, `onWifiOnlyChange(Boolean)`; `SyncUiState.autoRetry: Boolean = true`, `wifiOnly: Boolean = true`.

- [ ] **Step 1: Failing tests**: ViewModel exposes both settings from the repository and writes changes (`setAutoRetry(false)` reaches the store); mapper: while a run is active the hint is "You can leave the app; progress shows in the notification" (replacing "Keep the app open while syncing"); androidTest: both switches render with their labels ("Retry automatically", "Wi-Fi only") and report changes; update the existing `running_showsStopAndKeepOpenHint` test for the new hint.
- [ ] **Step 2: Run** the ViewModel and mapper tests; FAIL.
- [ ] **Step 3: Implement**: two `Switch` rows under the batch size with one-line explanations ("Wait and try again after an error or a lost connection"; "Upload only on Wi-Fi or another unmetered connection; mobile data waits"); `POST_NOTIFICATIONS` request via `rememberLauncherForActivityResult(RequestPermission())` launched once before the first `onSyncNow` on API 33+ when not granted (the sync starts regardless of the answer; a one-time message "Notifications are off; the sync still runs" on refusal). Keep `keepScreenOn` as is.
- [ ] **Step 4: Run** the full unit suite and `:app:compileDebugAndroidTestKotlin`; PASS.
- [ ] **Step 5: Commit** `feat(android): retry and Wi-Fi-only switches, notification permission (A6)`.

### Task 8: Docs and final verification

**Files:**
- Modify: `AndroidClient/CLAUDE.md` (section "Background sync (A6)": seams, retry rules, notification, 6-hour cap, no process-death resume), the A3 bullet "Errors: network/5xx -> `Failed` and stay pending (no auto retry)" and "Foreground-only" (point to A6), the A6 spec status line (`implementation` while building, `done` after the owner's device check), `docs/superpowers/specs/2026-11-08-android-ingestion-sync/tracker.md` (add A6 row)
- Verify only: no new tests

- [ ] **Step 1: Edit the docs** as listed.
- [ ] **Step 2: Run** `.\gradlew :app:testDebugUnitTest --no-daemon`, `.\gradlew assembleDebug`, `.\gradlew :app:compileDebugAndroidTestKotlin`; all green.
- [ ] **Step 3: Write the device checklist** (the owner runs it; copy it into the A6 spec under "Device check"): leave the app during a 200-photo sync (keeps going); airplane mode on and off (waits, resumes); Wi-Fi off with Wi-Fi only on (waits for Wi-Fi); Wi-Fi only off on mobile data (uploads); Stop from the notification; two environments give two notifications; notification permission denied still syncs; kill the app from recents (sync stops, restart continues the rest).
- [ ] **Step 4: Commit** `docs(android-sync): A6 notes and device checklist`.
