# A6: Background sync, progress notification and auto-retry (Android)

Status: implementation
Plan: docs/superpowers/plans/2026-11-23-a6-background-sync-and-auto-retry.md
Originates from: `2026-11-08-a3-sync-engine-design.md` (the engine is foreground-only and never retries), owner's improvement list items 4 and 5 (2026-11-22).

## Intent

A long album sync (thousands of photos, paced at 9 requests per 60 s) should survive the user leaving the app, show its progress in a notification per environment, and recover on its own from a dropped connection or a server hiccup, without sending thousands of photos over mobile data unless the user allows it.

## Goals

1. The sync keeps running while the app is in the background (foreground service of type `dataSync`).
2. One ongoing notification per running environment: environment name, "N of M uploaded", a progress bar, a Stop action. Tapping it opens the Album sync screen.
3. Option "Retry automatically" (default on): transient failures wait with growing delays and continue, inside the same run.
4. When auto-retry is on, a lost connection pauses the sync ("Waiting for network") and continues when the network returns.
5. Option "Wi-Fi only" (default on): the sync never uploads over a metered connection; it waits ("Waiting for Wi-Fi") and continues when one is available.

## Non-goals

- Coming back by itself after Android kills the process or the service (WorkManager). Possible later as "option C"; not designed here.
- Android's floating "bubbles" API. "Bubble notification" is read as an ongoing notification with a progress bar.
- Per-environment option values. Both switches are global, like the batch size.
- Retrying errors that need a human: `NO_ALBUM`, `PERMISSION_LOST`, `INBOX_FULL_OR_UNWRITABLE`, `DISK_LOW`, "backend too old", the 3-poison-files-in-a-row `SERVER` failure, and `UNEXPECTED`.

## Anti-goals

- A sync that restarts forever against a broken server (retries are capped).
- A notification the user cannot stop, or one that stays after the run ended without saying how it ended.
- Any change to what is uploaded, to the tracker rules or to the pacing (9 per 60 s).

## Findings (how it works today)

- `SyncEngine` is a `@Singleton` with its own `@SyncScope` scope, one job per environment, `StateFlow<SyncState>` per environment. The scope dies with the process, so a backgrounded app can lose a sync. The sync screen only keeps the display on.
- Failures end the run (`SyncState.Failed`); the files stay pending and the user presses "Sync now" again. Only a 429 waits and retries inside the run.
- The manifest has no service, no `POST_NOTIFICATIONS`, no `FOREGROUND_SERVICE*`, no `ACCESS_NETWORK_STATE`. WorkManager is not a dependency.
- Android 15 limits a `dataSync` foreground service to about 6 hours per 24; the system then calls `Service.onTimeout`.

## Approaches considered

| | What it is | Trade-off |
|---|---|---|
| **A. In-process only** | Keep today's engine, add retry loops and a notification posted while the app process lives | Android still freezes or kills a backgrounded process; the notification would lie. Rejected. |
| **B. Foreground service around the existing engine (chosen)** | The engine keeps owning the work; a `SyncService` only keeps the process alive and renders notifications from the engine's state flows | Smallest change to a well-tested engine; limited by the 6 h cap and by process death (accepted, see non-goals). |
| **C. WorkManager (long-running worker per environment)** | Android schedules the work, survives process death, retries with its own backoff | Needs a new dependency, rewrites the run loop around `Worker` lifecycle, per-environment unique work and progress updates; riskiest. Deferred. |

The owner chose scope "B" (retry inside the run plus waiting for the network) and the Wi-Fi-only switch.

## Design

### Settings

`TrackerSettingsStore` (DataStore `tracker_settings`, next to the batch size) gets two booleans: `autoRetry` (default `true`) and `wifiOnly` (default `true`). Both are read by the engine at the moment they matter (like the batch size), so changing them applies to a running sync. Two switches under "Batch size" on the Album sync screen. Not duplicated in the Settings screen.

### Engine changes (`data/sync/`)

New seam `NetworkGate` (interface, Android implementation over `ConnectivityManager.registerDefaultNetworkCallback`, fake in tests):
- `suspend fun awaitUsable(wifiOnly: Boolean)`: returns at once when a validated network exists (and, if `wifiOnly`, it is not metered, `NET_CAPABILITY_NOT_METERED`); otherwise suspends until one appears.
- `fun usable(wifiOnly: Boolean): Boolean` for the immediate check.

`WaitReason` gains `NO_NETWORK`, `NEEDS_WIFI` and `RETRY_BACKOFF`.

In `runLoop`, before each batch (after the pacing wait, before reading files): if the gate is not usable, publish `Waiting(NEEDS_WIFI)` (wifiOnly and some network exists) or `Waiting(NO_NETWORK)`, `awaitUsable`, then `continue` (pending is re-read after any wait). `NO_NETWORK` is only entered when `autoRetry` is on; with it off and no network at all, today's behaviour stays (the upload fails with `NETWORK`). `NEEDS_WIFI` applies regardless of `autoRetry`.

Retry: `runLoop` is wrapped. When it returns `Failed` with a retryable error and `autoRetry` is on, the engine publishes `Waiting(RETRY_BACKOFF, resumeAt, attempt)`, delays, and runs the loop again with the same `Progress` and pacer (so counts keep adding up and the request budget is respected). Retryable: `NETWORK`, `SERVER` raised from an HTTP 5xx other than 503/507, `FILE_READ_ERROR` (transient provider failures), `RATE_LIMITED` (after its 10 waits). Delays: 15 s, 30 s, 60 s, 2 min, 5 min, then 5 min; at most 8 attempts in a row; the counter resets when a batch settles at least one file. After the 8th the run ends `Failed` as today. Stop during any wait ends the run `Stopped` as today.

`Waiting.attempt` already exists and is reused for the retry number.

### Foreground service (`service/SyncService`)

- Manifest: `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_DATA_SYNC`, `POST_NOTIFICATIONS`, `ACCESS_NETWORK_STATE`; `<service android:foregroundServiceType="dataSync" android:exported="false">`.
- New seam `SyncServiceStarter` (interface, `ContextCompat.startForegroundService` implementation, no-op fake). `SyncEngine.start(env)` calls it. Starting happens while the user is in the app (button tap), which is the case Android allows. Retries and waits happen inside the already-running service.
- `SyncService` collects `engine.activeEnvironments: StateFlow<Set<String>>` (new) and each active environment's state. It stops itself when the set is empty and the final notifications are posted.
- One notification per environment, id derived from the environment id (stable), channel "Album sync" (importance LOW, no sound). Android allows one foreground notification: the service uses the oldest running environment's notification as the foreground one and posts the others with `NotificationManager.notify`; when the foreground one ends, the service re-promotes the next with `startForeground`.
- `onTimeout` (Android 15, 6 h cap): `engine.stop` for every environment with a distinct final text ("Android paused the sync after 6 hours. Tap to continue.").
- Stop action: a `PendingIntent` to a broadcast receiver (or `ACTION_STOP` service intent) that calls `engine.stop(env)`.

### Notification content (pure mapper, unit tested)

`SyncNotificationMapper.map(envName, state, counts)`:
- Running: title "General: syncing", text "120 of 400 uploaded", determinate progress (uploaded + rejected over supported), action Stop.
- Waiting: text from the reason: "Waiting for Wi-Fi", "Waiting for network", "Retrying in 30 s (attempt 2 of 8)", "Pausing to respect the server limit"; same progress bar, Stop.
- Finished / Failed / Stopped: a normal dismissible notification ("General: sync finished, 400 uploaded", the existing `syncErrorMessage` for failures, "Sync stopped"), no progress, tap opens the sync screen.

### Permission

`POST_NOTIFICATIONS` (Android 13+) is requested the first time the user taps "Sync now". Denied: the sync still runs in the foreground service (Android shows it in the Task Manager), the screen says once that notifications are off. No other behaviour depends on it.

### Sync screen

Two switches ("Retry automatically", "Wi-Fi only") with one-line explanations; the `Waiting` text mappers (`SyncMappers.kt`) learn the three new reasons; the "Keep the app open while syncing" hint changes to "You can leave the app; progress shows in the notification" while the service is running.

## Testing

- Engine (JVM, fake clock and virtual time): retry sequence and delays, cap after 8, counter reset after progress, non-retryable errors end the run, `autoRetry` off keeps today's behaviour, stop during backoff and during network wait, no-network and Wi-Fi-only waits resume at the callback, pending re-read after every wait, settings change applies mid-run.
- `SyncNotificationMapper` (JVM): every state and reason.
- `NetworkGate` and the service are Android code: thin, checked on a device with the checklist below; compile-checked in CI.
- Device checklist: leave the app during a 200-photo sync (keeps going), airplane mode on/off (waits, resumes), Wi-Fi off with Wi-Fi-only on (waits for Wi-Fi), Stop from the notification, two environments at once give two notifications, notification permission denied.

## Left to the builder

Exact notification texts, small icon (reuse the launcher monochrome), channel name, internal class layout beyond the seams above, whether the Stop action is a service intent or a receiver.

## My calls (change any of these)

- Defaults: auto-retry on, Wi-Fi only on.
- Retry delays and the cap of 8 attempts.
- Switches live only on the Album sync screen and are global.
- Wi-Fi only means "not metered", so a phone hotspot or a metered Wi-Fi counts as mobile data.
- After the Android 15 6-hour cap the sync stops and waits for a manual restart.

## Device check

Status note: implementation is done and unit-tested; this checklist is the owner's device run. The spec becomes `done` after it.

- [ ] Leave the app during a 200-photo sync: it keeps going and the notification shows progress.
- [ ] Airplane mode on, then off: the sync waits ("Waiting for network"), then resumes.
- [ ] Wi-Fi off with Wi-Fi only on: it waits for Wi-Fi.
- [ ] Wi-Fi only off, on mobile data: it uploads.
- [ ] Stop from the notification, also with the app in the background.
- [ ] Two environments at once: two notifications; the first one's "finished" notification survives while the second keeps running.
- [ ] Notification permission denied: the sync still runs.
- [ ] Swipe the app from recents: the sync stops; restarting continues the rest. A leftover ongoing notification can be cleared with its Stop button.
- [ ] Android 15: the 6 hour limit shows "Android paused the sync after 6 hours. Tap to continue." (only checkable on an Android 15 device or by reading the logs).
- [ ] The small icon looks acceptable in the status bar.

## Deviations and deferred notes

- The `NetworkGate` contract is `status(wifiOnly)` + `awaitChange()` with a 5 s recheck in the engine, not `awaitUsable`.
- Each retry attempt is a new `Run`, so the batch number, the 429 counter and the poison counter restart per attempt (the summary keeps adding up).
- The small icon is the framework `stat_sys_upload`; there is no monochrome app drawable yet.
- `NavGraph` accepts only whitelisted routes from the `route` extra (the activity is exported).
- Known minors: the two simultaneous network waiters share one gate version (bounded by the 5 s recheck); after a process kill a leftover ongoing notification stays until its Stop is tapped.
