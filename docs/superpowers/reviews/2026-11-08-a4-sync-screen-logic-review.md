# A4 Sync screen: logic and lifecycle review

Reviewer axes: ViewModel correctness against the owner's requirements and the A1/A2/A3 APIs; lifecycle, concurrency, state.
Scope: commit ef2ff686. Unit tests run: `com.memebrowser.app.ui.sync.*` and `com.memebrowser.app.data.tracker.*` pass (BUILD SUCCESSFUL, no other Gradle run was active).
Paths below are relative to `AndroidClient/app/src/main/java/com/memebrowser/app/` unless they start with `test/`.

## Verdict

Ship after fixes. The ViewModel is sound on the hard parts (environment re-binding, plan survival, throttle, idempotent taps, retry SQL). There is no Critical finding. There are three Important findings, all cheap to fix, plus minors and test gaps.

## Findings

### Important

**I1. The change-album dialog stays open, with disabled buttons, for the whole rescan after the wipe (proven by reading).**
`ui/sync/SyncViewModel.kt:~170` (confirmChangeAlbum) and `data/album/AlbumSyncCoordinator.kt` (`finish` calls `refreshScan` inside `confirmChangeAlbum`).
`confirmChangeAlbum(plan)` returns only after the commit and the rescan (scanner timeout 60 s). `local.plan` is cleared in `handle` after that. Scenario: the user confirms; the tracker is already wiped and the new album stored; for up to 60 s on a big or slow album the dialog still shows "Change and reset" and "Cancel", both disabled by `busy`, and `dismissChangeAlbum` is also a no-op while busy. The screen behind shows an empty tracker with the old dialog on top. Nothing is wrong data-wise, but it looks hung and Cancel is dead although the change is already irreversible.
Fix: show an explicit "Changing album..." progress state in the dialog (a busy line), or clear `plan` as soon as the wipe is committed. The coordinator API gives no such callback, so the cheap fix is the busy text plus a spinner in the dialog. It could instead observe `album(env)` changing to `plan.newTreeUri` and close the dialog then.

**I2. "Choose album again" can be a dead end when the scanner reports PermissionLost but the persisted grant is still listed (suspected, not reproduced).**
`SyncViewModel.kt:onAlbumPicked` -> `requestChange`; `AlbumSyncCoordinator.requestChangeAlbum` computes `needsRegrant = same && !access.isGranted(tree)`.
`SafAlbumScanner` returns PermissionLost on a SecurityException, not only on a missing grant. If the grant is still in `persistedUriPermissions` but the provider throws, then re-picking the same folder gives `isNoOp` -> message "That album is already selected." and `ScanState.PermissionLost` stays. The only exit is Change album with a different folder (a wipe). The state-to-UI table promises "same tree: no wipe" for exactly this card.
Fix: for a same-album pick while `scan == PermissionLost` (or sync Failed with PERMISSION_LOST), force the re-grant path: call `coordinator.selectAlbum(env, tree)` (it only re-persists when not granted, so it also needs A2 to re-persist unconditionally), or have the ViewModel call `refreshScan` after the pick and say so. Ask the A2 owner whether `persist()` should be retaken in this case. Test it on a device (E1).

**I3. Retry-all counts and resets rows that the list does not show (proven by reading the SQL).**
`data/tracker/TrackerDao.kt:~72` (`retryRejectedByReason`), `TrackerRepository.kt:~91`, `SyncViewModel.retryServerErrors`.
`observeRejected` (the list) and the R count filter `presentInAlbum = 1`; the retry UPDATE does not. Scenario: 3 files failed with `server_error`, one was later deleted from the album (presentInAlbum = 0). The button says "Retry all server errors (2)", the snackbar says "3 files will be sent again", the third row goes PENDING but stays out of P and out of `nextPending` (both filter presentInAlbum = 1). If the file is restored to the album, it silently becomes pending. That is harmless but inconsistent; the number is wrong.
Fix: add `AND presentInAlbum = 1` to both retry queries (and the FakeTrackerDao mirror), or accept and document it. I recommend the filter. `nextPending` already ignores absent rows, so a reset absent row would otherwise come back with a stale reason cleared.

### Minor

**M1. Transient wrong environment name on a switch (proven by code).** `SyncViewModel.kt:selectedEnvironment` combines `selectedEnvironmentId` with `selectedEnvironmentName`; the latter is itself derived from the same store. On a switch the first emission can be (newId, oldName), then (newId, newName). Result: header shows the old name for a frame, and `flatMapLatest` re-binds twice. Counts and album are correct (they are keyed by id). Fix: derive id and name from one flow (`environmentsWithSelection`), or key `flatMapLatest` on the id only (`map { it.id }.distinctUntilChanged()`) and combine the name afterwards.

**M2. Actions resolve the environment when the coroutine runs, not from the state that was on screen (design smell, window is milliseconds).** `startSync`, `stopSync`, `refresh`, `retry*`, `dismissSyncResult` call `currentEnvironmentId()` (a DataStore read). During an environment switch `uiState` keeps showing environment A's snapshot until B's flows emit (stateIn keeps the last value), so a tap in that window acts on B while the user sees A. Stop/Start otherwise act on the right id, and A's engine run is untouched (engine and coordinator are keyed by id; nothing in the ViewModel stops A on a switch, which is the intended behaviour). Reaching this needs the environment to change while this screen is alive, which the current navigation hardly allows (the switch lives on another route and the ViewModel is back-stack scoped). Low risk. Cheap fix: pass `state.environment.id` from the UI, or read `uiState.value.environment?.id` and no-op when it is null.

**M3. Screen-local state is global, not per environment.** `Local.message`, `busy`, `dismissedSync` and `rejectedVisible` are shared. A message from A can show on B; `busy` from an album pick in A disables controls in B; the open rejected sub-view stays open on B. The plan is correctly filtered by `environmentId` (and tested) and reappears when switching back to A, with possibly stale counts (it shows the counts from when it was built; PlanStale only compares the tree). Consider clearing `plan`/`rejectedVisible` on environment change.

**M4. Throttle stamp is consumed even if the refresh did nothing useful.** `onScreenStarted` sets `autoRefreshAt` before checking album/scan and before the result. A refresh that ends `Failed(LISTING_INCOMPLETE)` (provider still loading right after boot) is not retried for 30 s except by the Refresh button. Also a start while a scan is running consumes the slot. Acceptable by the spec ("once per 30 s"); mention in the UI via the failure text (already there).

**M5. The rejected list is mapped on the Main dispatcher on every table change.** `snapshotOf` maps all rows inside `combine` collected in `viewModelScope`. Room re-queries on any `tracked_files` write, so with the sub-view open during a running sync, thousands of rows are remapped on Main after every batch. Add `.flowOn(Dispatchers.Default)` to the mapping, or map in the composable. Also, on first open the list is empty for a few ms (the combine keeps the previous empty value until Room emits), so "No rejected files." can flash.

**M6. Brittle string coupling.** `ui/sync/SyncMappers.kt:81` detects "backend too old" with `message.contains("backend too old")`. Use `SyncEngine.BACKEND_TOO_OLD_MESSAGE` (it exists) or add a `SyncError.BACKEND_TOO_OLD`; otherwise a reworded engine message silently changes the text to the generic one. Also add a mapper test that feeds the real constant.

**M7. Theoretical unbounded recursion** `handle` -> `requestChange` -> `handle` on repeated `PlanStale` (a race that changes the stored album on every attempt). Not reachable in practice; cap the depth to 2 or return to the dialog.

**M8. `requestChange` overwrites `message` with `note` (null)** when it opens the dialog, dropping a pending snackbar message. Use `note ?: it.message`.

**M9. Time formatting.** `SimpleDateFormat("HH:mm:ss", Locale.getDefault())` is created on each call, so locale and time zone changes are picked up; fine. In locales with non-Latin digits it prints those digits; use `Locale.ROOT` digits only if that matters. Not a defect.

### Checked and fine (proven by reading unless noted)

- Environment switch: `flatMapLatest` cancels A's flows; no A emission reaches B (tested). The engine run for A is not touched; `engine.stop` and `engine.start` receive the id resolved at call time.
- Double taps: `startSync` is idempotent (`engine.start` returns false while running); `stopSync` is idempotent; Confirm and Pick are guarded by `busy`, which is set synchronously before the first suspension (`Dispatchers.Main.immediate`), so a second tap is dropped; after completion `plan` is null. Refresh is conflated by the coordinator.
- Auto refresh: ON_START and the env-keyed LaunchedEffect both fire on open; the check and stamp in `onScreenStarted` has no suspension point between read and write, so the second call is absorbed. The map is main-thread only. A refresh during a running sync only touches `presentInAlbum` and new rows; it can conflict with nothing in the ViewModel.
- Plan survival: plan lives in the Hilt ViewModel; rotation re-collects (tested). Process death loses the plan and the picker grant together, so the user simply picks again; nothing is wiped before Confirm. Suspected, not provable on JVM: the picker's temporary grant surviving activity recreation. If it does not, Confirm ends with `GrantFailed` and a clear message (handled).
- PermissionLost never wipes; `canSync` is false only while the scan state says so, and the engine's `Failed(PERMISSION_LOST)` covers the cold-start case.
- Exhaustiveness: `syncErrorMessage`, `scanStatusText`, `syncStatusText`, `handle` are `when` expressions/statements over enums and sealed types without `else`, so a new `SyncError`/`WaitReason`/`AlbumChangeResult` fails compilation rather than falling through. Only `rejectionReasonText` has an `else` and it is deliberate (unknown backend text is shown raw).
- keep-screen-on: derived from `sync.isActive` of the displayed environment, reset in `onDispose`. A sync running for another environment than the displayed one does not keep the screen on; acceptable.
- Retry SQL: only `state = REJECTED AND rejectionReason = 'server_error'` goes to PENDING with the reason cleared; per-file variant additionally keys the triple; both run under the A1 Mutex; the `server_error` literal is pinned in `TrackerSqlConstantsTest` against both `RejectionReasons` and `SyncEngine.REASON_SERVER_ERROR`. After retry the row is picked up by `nextPending` and counted in P (and Y), and no longer in R (repository test asserts counts and `nextPending`). Room SQL itself is not run on the JVM (`FakeTrackerDao` mirrors it by hand); see test gaps.
- Rejected flow subscription: really only while `rejectedVisible` (inner `flatMapLatest`), and unsubscribed after `WhileSubscribed(5s)` when the screen is gone.
- `UploadViewModel` repoint: `SupportedImages.ALLOWED_MIME_TYPES` and `MAX_FILE_SIZE_BYTES` equal the old private constants.

## What is good

- One immutable `SyncUiState`, with `controls` derived by a pure function from the same state, so buttons cannot disagree with the data.
- The plan is keyed by environment id and filtered at render time; the dialog never appears on the wrong environment.
- Clear separation: data-layer calls wrapped in `guarded` (cancellation rethrown, other exceptions become a message).
- The terminal-state dismissal compares the full state including the timestamp, so a new run's identical-looking result is not hidden. `startSync` also resets it.
- Mappers are pure with an injectable time formatter; all enum `when`s are compiler-enforced.
- The retry API is additive, mutex-protected and has literal-pinning tests.

## Test gaps

1. `MainDispatcherRule` uses `UnconfinedTestDispatcher`, so the `busy` window, double taps and "confirm twice" are never exercised. Add a test with a coordinator mock that suspends (CompletableDeferred): two `confirmChangeAlbum()` calls produce one coordinator call; `dismissChangeAlbum()` is ignored while busy; a second `onAlbumPicked` during a pick is dropped.
2. No test for Stop/Start/retry after an environment switch (verify `engine.stop("env-b")`, and that env A's engine is never called). The existing switch test checks only state.
3. No test of the transient (newId, oldName) emission (M1); the test sets id and name one after the other under an unconfined dispatcher and only checks the end state.
4. The "no automatic refresh" test mixes four scenarios and relies on clock arithmetic; split it. No test that a refresh requested while `Scanning` is not retried for the 30 s slot, or that a failed refresh still consumes the slot.
5. `retryServerError` returning false ("That file cannot be retried") and `n == 0` ("No files with server errors") are not tested. No test for the present-in-album mismatch (I3).
6. No test of the `PlanStale` -> second plan when the rebuilt plan is non-wiping (auto-confirm path), nor of `handle` recursion bounds.
7. `PermissionLost` re-pick when the grant is still listed (I2) is untested; needs a coordinator-level test.
8. `SyncMappersTest` does not feed `SyncEngine.BACKEND_TOO_OLD_MESSAGE` to `syncErrorMessage` (M6).
9. The Room retry queries have no JVM test; add them to `androidTest/TrackerDaoTest` (can only run on a device).

## Questions for the owner

1. After "Change and reset" the rescan can take up to a minute. Should the dialog stay with a progress text, or close at once and show the scan in the status card?
2. For a lost album (PermissionLost) where re-picking the same folder changes nothing, is a "re-grant and rescan" path acceptable, or should the app always point to Change album?
3. Should "Retry all server errors" include files no longer in the album (currently yes, silently)?
4. Should switching environment close an open rejected sub-view and a pending change-album dialog (currently the sub-view stays; the dialog is hidden but remembered)?
