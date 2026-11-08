# A2 album selection and scan: review

Reviewer: independent, commit b6944a13. Ran `:app:testDebugUnitTest --tests "com.memebrowser.app.data.album.*"` (no other Gradle process was active): BUILD SUCCESSFUL. The androidTest was not run (no device). "Proven" below means shown by reading the code; "suspected" means it depends on Android or provider behaviour I could not execute.

## Verdict

Ship after fixes. The ordering contract (new grant, gate, wipe under lock, store, release, rescan), the "Incomplete never reaches syncScan" rule and per-environment isolation are implemented as specified, and there is no lock-order deadlock. The Important items are cancellation of the change flow midway (leaked or lost grants) and an unbounded wait on a hung provider while the lock is held; both are cheap to fix before A4 wires the flows.

## Findings

### Important

1. **confirmChangeAlbum is not cancellation-safe midway** (AlbumSyncCoordinator.kt:179-195, also 197-201). Proven: it runs as a plain suspend call (A4 will call it from a viewModelScope). If the scope is cancelled (screen left, process pressure) after `access.persist(new)` but before `changeAlbum`, the new grant stays persisted and nothing records it (leak; the persisted-grant limit is 128/512). If cancelled after `tracker.changeAlbum` returned but before `releaseIfUnused`, the OLD grant is leaked for good: the album now says B, nobody ever knows A was held. Same for `applyFirstSelection` (grant taken, `setAlbum` cancelled). Fix: run the mutating section (persist .. releaseIfUnused) in `withContext(NonCancellable)`, or run it on an application-scope coroutine, and keep only the final `refreshScan` cancellable. Also: if `tracker.changeAlbum` or `setAlbum` throws (DataStore IOException), the freshly taken grant is not released; wrap and release it on failure.

2. **A hung or slow provider blocks the change-album flow with no way out** (AlbumSyncCoordinator.kt:188, 233). Proven by structure: a scan holds the per-environment lock across `scanner.scan`, and `confirmChangeAlbum` waits for that lock to wipe. `ContentResolver.query` is blocking and not interruptible by coroutine cancellation (`withContext` only notices on return), so a stuck cloud/USB provider freezes "change album" for as long as the query runs. The class KDoc says "cancel the in-flight sync BEFORE the wipe", but an in-flight refresh is waited for, not cancelled. Fix: track the running refresh `Job` per environment and cancel it first in confirm (the scan result is about to be discarded anyway), and give the query a timeout (or cancel signal: pass a `CancellationSignal` to `ContentResolver.query` and cancel it from `invokeOnCompletion`/`suspendCancellableCoroutine`).

3. **Stale join in refreshScan after a change** (AlbumSyncCoordinator.kt:226-243). Proven window, small in practice: `inFlight` is only removed in `finally`, after the lock is released and `mine.complete(result)`. Sequence: refresh R (old album A) finishes `withLock` and completes; the confirm thread takes the lock, wipes and stores B; if R's `finally` has not yet removed its entry when confirm calls `refreshScan`, confirm joins R's already-completed `Done` for album A and returns `Applied(Done)` without ever scanning B. The tracker is then empty for B with state Done. Fix: remove the in-flight entry before releasing the lock (do the `remove` inside the `withLock` block, or key the entry by album/generation so a post-change call never joins an older generation), or have confirm/first-selection start a refresh that ignores conflation (`force`).

### Minor

4. **Misleading `Done`/`PermissionLost`/`Failed` state survives an album change** (AlbumSyncCoordinator.kt:188-192, 197-200). After the wipe, `scanState` still shows the old album's state (for example `Done(at)` with X/Y at 0, or `PermissionLost` from album A) until the rescan assigns `Scanning`. A4 may flash "up to date" or the re-pick prompt for the new album. Reset the state to `Idle` inside the lock together with the wipe.

5. **Empty cursor from an unmounted/unreachable volume is treated as a real empty album** (AlbumScanner.kt:62-66). Suspected (provider dependent): some providers return an empty, non-loading cursor for an ejected SD card or a deleted folder instead of throwing. That is `Complete(empty)` with `allowEmpty=true`, which marks every tracked row absent (Y becomes 0). Not data loss (`markMissing` only flips `presentInAlbum`; X is kept per D2, and the next good scan restores the flags), but the owner sees Y drop to 0. Possible guard: when the listing is empty and the tracker has present rows, verify the tree root still exists (query `DocumentsContract.buildDocumentUriUsingTree` for the root, `FileNotFoundException`/empty means Incomplete) before passing it on; or accept and document.

6. **"Never throws" is not true for the coordinator** (AlbumSyncCoordinator.kt:219-226, 248). `tracker.album(env).first()` (DataStore IOException) and a scanner that violates its contract propagate out of `refreshScan`. The `finally` then reports waiters `Failed(CANCELLED)`, which is the wrong reason. Catch `Exception` (not Cancellation) in `scanLocked` and map it to a new `ScanFailure.UNEXPECTED`/`TRACKER_ERROR`, or fix the KDoc. Likewise `releaseIfUnused` (line 210) can throw after the wipe (EnvironmentStore read), which loses the rescan and returns an exception for a change that was applied.

7. **Plan is not bound to the state it was built from** (AlbumSyncCoordinator.kt:179-195). `confirmChangeAlbum` re-reads the album, which is right, but it does not compare it with `plan.currentAlbum`. If the album changed between dialog and confirm (to C), confirming "A to B" wipes C silently, with counts the user never saw. Return `RequiresConfirmation`/a `PlanStale` result when `current != plan.currentAlbum`. Also `selectAlbum`/`confirmChangeAlbum` read the album outside the lock, so two concurrent first selections can both pass the `current == null` check (last write wins; harmless but the grant of the loser leaks).

8. **Process-death risk for the temporary picker grant** (AlbumSyncCoordinator.kt:155, 186). `requestChangeAlbum` takes no grant; `persist` happens only at confirm. The picker's temporary grant lasts only while the receiving task lives, so a confirm after process death or after the dialog survives a config change into a new process throws `SecurityException`, giving `GrantFailed` with no way to retry other than re-picking. Fine if A4 keeps the dialog in the same task; otherwise persist the grant when the plan is built (and release it on cancel). Worth stating in the A4 notes.

9. **`DIR_MIME` duplicated** (AlbumScanner.kt:66). `DocumentsContract.Document.MIME_TYPE_DIR` is a compile-time constant and is inlined, so the JVM tests could use it directly; the copy invites drift. Same for the column projection being the only Android-bound part, which is fine.

10. **AlbumNames on non-externalstorage authorities** (AlbumNames.kt:30-41). Suspected, cosmetic: the Downloads provider uses ids like `msf:1234` or `raw:/storage/emulated/0/Download/x`; `msf:1234` gives the display name `1234`. The picker normally cannot return those as trees, but if it does the name is meaningless. The authority is ignored everywhere, so a `content://other.authority/tree/primary%3APictures%2FMemesBrowser-x` would also trigger the own-folder warning. Acceptable; consider restricting `looksLikeAppOwnFolder` to the externalstorage authority. Verified correct: percent-encoding, `+` literal, Cyrillic (UTF-8), `primary:` and `1234-5678:` roots, trailing slashes, `/document/` suffix, malformed escapes, folder name matches `MediaStoreHelper` (`Pictures/MemesBrowser-$collection`).

11. **`isGranted` URI equality** (AlbumAccess.kt:27). Suspected, low: compares `uri.toString()` with the stored string. Both come from the system so encodings match in practice; a stored string that was ever normalised differently (for example after `Uri.parse(x).toString()` changes case of the escape digits) would read as a lost grant and show the re-pick prompt (no wipe, so harmless). Comparing parsed `Uri` equality (or normalising both sides) would be sturdier. A transient `RuntimeException` also reports "not granted" (PermissionLost), acceptable because it never wipes.

12. **State maps never shrink** (AlbumSyncCoordinator.kt:119-124): `locks`/`states` grow by environment id forever; negligible.

## What is good

- Listing is a single `ContentResolver.query` on the children URI built from `getTreeDocumentId`, cursor closed with `use`, `EXTRA_LOADING` honoured (loading with rows is still Incomplete), missing columns tolerated, directories skipped, non-recursive, no `DocumentFile`. The Android-bound part is a 40-line seam, the decision logic is JVM-testable.
- `SecurityException` maps to PermissionLost, everything else to Incomplete, and CancellationException is rethrown in both the scanner and `scanLocked` (`catch (e: CancellationException) { throw e }` precedes `catch (Exception)`); `runCatching` is not used.
- Ordering for a real change is as required: refused grant changes nothing and does not call the gate; gate before the wipe; wipe and store inside `TrackerRepository.changeAlbum` (wipe first); release only when no other environment stores the tree (and the current environment is excluded); same album never wipes; PermissionLost never reaches `syncScan`; only a Complete listing gets `allowEmpty=true`.
- Lock order is always coordinator lock then TrackerRepository mutex, never the reverse, and `changeAlbum`/`syncScan` are never nested inside each other; no deadlock. The mutex is not held across the slow provider call except the coordinator's per-environment one, so other environments are unaffected.
- Waiters of a cancelled refresh get `Failed(CANCELLED)` instead of inheriting the cancellation, and the `Scanning` state is rolled back with `compareAndSet`.
- `ChangeAlbumPlan` (counts, `wipesData`, `isNoOp`, `needsRegrant`, own-folder flag) is a good A4-facing contract; the spec/CLAUDE.md state the "my call" deviations explicitly.

## Test gaps

- No test for a change-album while a scan holds the lock (the central race of the design): assert the wipe waits, that no rows of the old album exist afterwards, and that a scan queued behind the change reads the NEW album. Also no test of a confirm that follows a just-finished refresh (finding 3).
- No cancellation test for `confirmChangeAlbum` or first selection (finding 1), and no test that `changeAlbum` failure leaves the new grant unreleased.
- `gate.probeAtCancel` only proves rows existed at gate time; there is no assertion that the gate is called before the lock is taken or that a gate failure aborts the wipe.
- Tracker error path (`TRACKER_ERROR`) and DataStore failure are untested; PermissionLost then successful re-grant clearing the state is only partly covered.
- `AlbumFakes` replaces `release` with set removal, so a test cannot distinguish "grant never held" from "released"; `released` list covers it, but the other-environment-shares case with the SAME env calling `selectAlbum` twice is not covered.
- Scanner tests use a fake source: the cursor reading (column index -1, null columns, `getBoolean` on extras, `use` closing) is never executed. `AlbumAccessTest` only covers three ungranted paths: it would pass even if `persist`, the flags or the children-URI building were wrong, and it never runs `ContentResolverAlbumRowSource` (the ungranted scan returns before reaching it). A cheap meaningful addition: an androidTest with a fake `DocumentsProvider` (or `MockContentProvider` answering the children URI) to exercise cursor mapping, EXTRA_LOADING and `SecurityException`.
- `refresh` of two environments shares no state is tested; concurrent refresh + change of two different environments is not.

## Questions for the owner

1. Should a hung provider listing be abortable by "change album" (finding 2), or is waiting acceptable?
2. Is a drop of Y to 0 on an ejected SD card or deleted folder acceptable (finding 5), or should the root be verified before an empty listing is accepted?
3. Will A4 keep the confirm dialog within the picker's task, or should the grant be persisted when the plan is built (finding 8)?
4. Should the own-folder warning be restricted to the externalstorage authority?
