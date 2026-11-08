# A4: Sync screen

Status: done
Originates from: docs/superpowers/specs/2026-11-08-android-ingestion-sync/README.md
Plan: docs/superpowers/plans/2026-11-08-a4-sync-screen.md

The screen of the album sync: choose the album of the selected environment, see "X of Y uploaded", run and stop the sync, set the batch size,
refresh Y, look at rejected files and change the album. It sits on A2 (`AlbumSyncCoordinator`) and A3 (`SyncEngine`); it adds no backend work.
Code: `ui/sync/` (`SyncViewModel`, `SyncScreen`, `SyncMappers`), one navigation change in `NavGraph.kt`, one additive retry API in `data/tracker/`.

## Decided by the owner

> **PLACEMENT = MERGE:** the new "Album sync" screen becomes what the existing Upload entry point opens (change the navigation target of
> onUploadClick; keep the route/screen list otherwise) and it carries a secondary action "Upload individual files..." that navigates to the
> EXISTING manual UploadScreen (kept as is, reachable by its own route). NavGraph stays the only place that touches NavController (screens get callbacks).

> **REJECTED LIST = view-only plus "Retry server errors":** rejected files stay skipped (type/size/content rejections are permanent per the owner);
> ONLY rows rejected with reason `server_error` get a Retry action (per row and a 'Retry all server errors' button) which resets them to PENDING.
> This needs a small additive tracker API: a DAO query + `TrackerRepository.retryServerErrors(environmentId)` (and per-key variant) that sets
> state PENDING for REJECTED rows with rejectionReason = the server_error constant, under the A1 Mutex like other writes, with the Fake DAO
> mirroring it and JVM tests.

The owner's own description of the screen: "I open the app, select the directory for the environment if not selected already (persisted in
state) and see the status (X out of Y uploaded). On the screen I see that status and a button to run sync now. When running it should be possible
to stop the sync running. The sync runs in batches, default 50, configurable (1 to 50). The Y number should be refreshed automatically or manually
by clicking another button. The user could also change the directory for the environment (different album), but it would wipe the tracker and
needs user confirmation."

## Layout

```
+--------------------------------------------+
| <-  Album sync                             |   TopAppBar (title "Rejected files" in the sub-view)
+--------------------------------------------+
| Environment: General                       |
| Album: Camera                              |
|                                            |
| +----------------------------------------+ |   StatusCard
| | 120 of 340 uploaded                    | |
| | [=========-----------------]           | |
| | 200 pending     [ 20 rejected ]        | |   "rejected" opens the rejected list
| | Last refreshed at 14:03:11             | |   scan state (or the problem text with a warning icon)
| | [ Refresh ]                            | |
| +----------------------------------------+ |
| (only when access was lost)                |
| | (!) Access to the album was lost.      | |
| | [ Choose album again ]                 | |
| +----------------------------------------+ |
| +----------------------------------------+ |   SyncCard
| | [ Sync now ]   |   [ Stop ] while running|
| | Sending batch 3 (50 files)... 100 up   | |   state text; Waiting / Finished / Failed texts
| | Keep the app open while syncing        | |   only while running
| +----------------------------------------+ |
| Batch size: 50 files per request           |
| 1 o---------------------------------o 50   |   Slider; "A change applies from the next batch." while running
| ------------------------------------------ |
| [ Change album ]                           |
| [ Upload individual files... ]             |
+--------------------------------------------+
```

No album: the header plus an empty-state card with `[ Choose album folder ]`, and `[ Upload individual files... ]`.
Rejected sub-view (a ViewModel flag, so it survives rotation; the system Back and the top-bar arrow close it): the explanation, `[ Retry all server errors (n) ]`
(only when n > 0), then one row per file: name, reason text, and `Retry` on `server_error` rows only.
Change-album dialog (an `AlertDialog` driven by `SyncUiState.changePlan`): old and new album names, the counts that will be forgotten, a warning
when the new folder looks like the app's own `Pictures/MemesBrowser-*` folder, a note when only the grant is restored, and a note that a running
sync will be stopped; buttons `Change and reset` / `Cancel`.

## State to UI

| Source | State | Rendered |
|---|---|---|
| environments | selected id + name | header; every flow below is re-bound with `flatMapLatest` on the id, a plan of another environment is hidden |
| album | none | empty state, `Choose album folder` (picker -> `selectAlbum`) |
| album | stored | album name, status card, sync card, batch size, `Change album` |
| counts | X, Y, R, P | "X of Y uploaded", progress bar (X/Y, 0 when Y = 0, capped at 1), "P pending", "R rejected" |
| scan | Idle | nothing |
| scan | Scanning | "Scanning the album..." with a spinner; Refresh disabled |
| scan | Done(at) | "Last refreshed at HH:mm:ss" |
| scan | Failed(LISTING_INCOMPLETE / TRACKER_ERROR / CANCELLED / NO_ALBUM) | warning icon + reason text, counts unchanged, Refresh enabled |
| scan | PermissionLost | warning card "Access to the album was lost", `Choose album again` (same tree: no wipe), Sync now disabled |
| sync | Idle | `Sync now` (enabled with an album, pending files and a granted album) |
| sync | Running | `Stop`, "Sending batch n (k files)... a uploaded, ..., so far", hint "Keep the app open while syncing", screen kept on |
| sync | Waiting(RATE_LIMITED) | "Server asked us to wait, resuming at HH:mm:ss" |
| sync | Waiting(PACING) | "Pacing requests to stay under the server limit, resuming at HH:mm:ss" |
| sync | Stopping | "Stopping...", `Stop` disabled |
| sync | Finished / Stopped | "Finished: ..." / "Stopped: ... Unsent files stay pending." with a dismiss (x) |
| sync | Failed(error) | warning icon + the message of the error (below) with a dismiss (x); `Choose album again` for PERMISSION_LOST / NO_ALBUM |
| batch size | 1..50 | slider, written on release through `TrackerSettingsStore`; a note while a run is active (the engine reads it before every batch) |

Error messages (`syncErrorMessage`): NETWORK (check connection and address, files stay pending), SERVER (also the "backend too old" text of the
engine -> "update the backend"), INBOX_FULL_OR_UNWRITABLE ("The server inbox is full or not writable - run ingestion or check the server"), DISK_LOW,
RATE_LIMITED (too many 429), UNEXPECTED (with the engine detail), NO_ALBUM, PERMISSION_LOST, FILE_READ_ERROR (nothing was rejected, try later).
Rejection reasons (`rejectionReasonText`): `unsupported_type`, `too_large` (20 MB), `file_unavailable`, `server_error` (retryable), the backend's own
reasons by prefix ("Unsupported file type", "File too large", "File content does not match"), unknown reasons shown as received.

## Behaviour

- **Environment:** the screen binds to `EnvironmentRepository.selectedEnvironmentId/Name`. The engine and coordinator are per environment id, so a switch
  re-binds album, counts, scan state, sync state and the rejected list, and never mixes two environments. The engine runs in its own scope: leaving the
  screen does not stop a sync; reopening shows the live state.
- **Picker:** one `OpenDocumentTree` launcher in the screen; the Uri string goes to `SyncViewModel.onAlbumPicked`. No album stored -> `selectAlbum`
  (`Applied`, `Unchanged`, `GrantFailed`, `Failed` map to a message or none; `RequiresConfirmation` falls into the change flow). An album stored ->
  `requestChangeAlbum`: no-op plan -> message "That album is already selected."; a plan that wipes nothing (same album with a lost grant) is confirmed at
  once; a plan that wipes -> the dialog. `PlanStale` -> the plan is requested again and the user is asked again with a note; `GrantFailed` / `Failed` close
  the dialog with a message.
- **Dialog survives rotation:** the plan lives in the (Hilt, back-stack-scoped) ViewModel, never recomputed from the Uri, so the picker's temporary grant
  (held by the process) is not needed again until Confirm.
- **Refresh:** `onScreenStarted()` on `ON_START` and when the environment changes: one `refreshScan` per environment per `AUTO_REFRESH_MIN_INTERVAL_MS` (30 s),
  skipped with no album or while a scan is running; rotation and double starts are absorbed by the interval. The Refresh button is disabled while scanning.
- **Retry server errors:** `TrackerRepository.retryServerErrors(env)` / `retryServerError(env, tree, documentId)` set REJECTED rows with reason `server_error`
  back to PENDING (reason cleared), under the tracker Mutex; every other rejection is untouched (permanent).
- **Rejected list load:** the list flow is subscribed only while the sub-view is open (it can be thousands of rows); the main screen only needs the count.

## My calls

- (my call) The drawer entry that opened Upload is renamed "Album sync" (same icon) because it now opens the sync screen.
- (my call) Foreground only: while a run is active the window stays on (`View.keepScreenOn` from a `DisposableEffect` keyed on the running state) and the card shows
  "Keep the app open while syncing". The engine dies with the process; a restart finds unsent files PENDING.
- (my call) The auto refresh is throttled to once per 30 s per environment instead of "once per open", so returning from the background later also refreshes
  while rotation and double starts do not.
- (my call) Strings are inline English, like every other screen of the app (there are no string resources besides the app name).
- (my call) Terminal sync states can be dismissed (hidden until the next run ends); a new `Sync now` clears the dismissal.
- (my call) Batch size is a slider (1..50) written on release; it applies from the next batch, as the engine reads it before every batch.
- (my call) Retry never runs a sync by itself: it resets the rows, "Sync now" sends them.
- (my call) The rejected list is a sub-view of the same screen (ViewModel flag + `BackHandler`), not a new route; NavGraph gains only the `sync` route.

## Tests

JVM: `SyncMappersTest` (progress fraction, button enablement, scan/sync/error/reason texts, dialog lines), `SyncViewModelTest` (binding, environment switch,
throttled refresh, every `AlbumChangeResult`, plan survival, dismissal, batch clamp, rejected list, retry, exceptions), `TrackerRepositoryTest`
(retry API), `TrackerSqlConstantsTest` (the `server_error` literal), the existing `UploadViewModel`-adjacent suites unchanged.
`androidTest`: `SyncScreenTest` renders the stateless `SyncContent` per state. Neither the Room SQL of the retry queries nor any Compose test can run on the JVM.

## Not verified here

Anything visual on a real device, the real folder picker flow and the temporary-grant behaviour, the Samsung hand-check list (E1), the Room SQL of the two
retry queries (`FakeTrackerDao` mirrors them by hand; `TrackerDaoTest` needs a device).
