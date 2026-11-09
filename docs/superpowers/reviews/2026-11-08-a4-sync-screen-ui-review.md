# A4 Sync screen: UI / readability / accessibility review

Reviewer axes: Compose readability, UX and accessibility, consistency with the app. Judged by reading only (commit ef2ff686). Nothing was rendered; no Gradle build or lint was run (the existing lint report under app/build was not trusted as current).

## Verdict: ship after fixes

The screen delivers every item the owner asked for and the stateless/stateful split is clean. No crash-class or data-loss defect found in the Compose layer. The fixes below are accessibility and clarity items, none large.

## Findings

### Important (proven by reading)

1. SyncScreen.kt:395-407 (rejected row) and :405 (Retry). With TalkBack every row has a button just called "Retry"; the user cannot tell which file it applies to, and name/reason are separate focus stops. Fix: `Modifier.semantics(mergeDescendants = true)` on the row and `contentDescription = "Retry ${item.displayName}"` on the button.
2. SyncScreen.kt:401. `maxLines = 2` without `overflow = TextOverflow.Ellipsis`: a long (Cyrillic, space-less) file name is silently clipped with no "...".
3. SyncScreen.kt:376-394. In the rejected sub-view the explanation and "Retry all" button sit outside the LazyColumn in a non-scrolling Column. At 2.0x text or in landscape they can take nearly the whole screen and leave the list a sliver. Fix: make them (and the "No rejected files." text, currently emitted before an empty fillMaxSize LazyColumn) the first `item {}` of the LazyColumn. The list itself is lazy and keyed (`albumTreeUri|documentId`), fine for thousands of rows.
4. Dynamic status changes are not announced: SyncScreen.kt:333-345 (sync status/failure) and :281-291 (scan). A screen-reader user pressing Sync now hears nothing when it fails, finishes or waits for the rate limit. Fix: `semantics { liveRegion = LiveRegionMode.Polite }` on the status container (Assertive for Failed).
5. Duplicate message when access is lost: SyncMappers.kt:64 yields "Access to the album was lost. Choose the album again to continue." shown by StatusCard (SyncScreen.kt:281) AND the dedicated card at :235-244 says "Access to the album was lost." again; a `Failed(PERMISSION_LOST)` adds a third text and a third "Choose album again" (:346), plus the Change album button. Fix: make `scanStatusText(PermissionLost)` return null (the card owns it) and show one re-pick button.
6. Destructive dialog hierarchy, SyncScreen.kt:415-435. "Change and reset" and "Cancel" are equal-weight TextButtons, the destructive one in the default primary colour. The wording is good (counts, "forgotten", server files stay, duplicates note, app-own-folder warning, sync-will-stop) and outside-tap/back means cancel, so an accidental wipe is unlikely; still, give confirm `contentColor = MaterialTheme.colorScheme.error`. The counts paragraph (SyncMappers.kt:145-147) is a dense run-on; one line per count reads better.
7. SyncMappers.kt:81. "Backend too old" is detected by `engineMessage.contains("backend too old")` although the engine exports `SyncEngine.BACKEND_TOO_OLD_MESSAGE` (SyncEngine.kt:411). Use the constant (better, a dedicated SyncError), otherwise rewording the engine message silently turns the actionable text into the generic one.
8. SyncMappers.kt:89 with SyncEngine.kt:121,210,336,352: `UNEXPECTED` appends the raw engine message, which can be `Throwable.message`, a class simple name or "HTTP 400: <server detail>". This is raw exception text shown to the user. Defensible for a single-owner app, but jargon; put it on a smaller "Details:" line or cap its length.

### Minor (proven)

9. SyncMappers.kt:137-153 `WARNING_PREFIX = "Warning:"` is a string protocol between mapper and composable (`startsWith`, SyncScreen.kt:422); fragile and the user sees a warning icon plus the word "Warning:". Return `List<DialogLine(text, isWarning)>`.
10. SyncMappers.kt:127 hard-codes "Larger than 20 MB" while the limit is `SupportedImages.MAX_FILE_SIZE_BYTES`; derive it. `canRetry` (SyncMappers.kt:157) is a generically named public top-level function used once; `isTerminal` sits in the mapper file while its sibling `isActive` lives beside `SyncState`.
11. Plurals: "Batch size: 1 files per request" (SyncScreen.kt:357), "Sending batch 1 (1 files)" (SyncMappers.kt:112). The ViewModel already special-cases plural inline (SyncViewModel.kt:280); add one helper.
12. SyncMappers.kt:24-26 forces `HH:mm:ss`; use `DateFormat.getTimeInstance` to follow locale/12h setting.
13. SyncScreen.kt:269-274 the progress bar's contentDescription repeats the headline right above it, so TalkBack reads "X of Y" twice. Merge or hide one.
14. SyncScreen.kt:277-279 the rejected count is a plain-looking TextButton; nothing says it opens a list (TalkBack: "20 rejected, button"). Add `onClickLabel`/"view". The pending text plus this button probably overflows one row at 2.0x on 360dp (suspected); use FlowRow.
15. SyncScreen.kt:362-364 `steps = 48` draws 49 ticks on a narrow slider (noisy) and the hand-written description duplicates the built-in range info. Suspected: TalkBack increment/decrement may update only `draft` if the Slider does not call `onValueChangeFinished` for accessibility actions, so the batch size would not be saved; check on a device.
16. SyncScreen.kt:116-132 `SyncActions` (about 15 callable references) is rebuilt every recomposition, and `LaunchedEffect(state.message)` (:141) captures `actions` from that composition. Harmless today because one state object recomposes everything anyway; wrap in `remember(viewModel)`, use `rememberUpdatedState`, mark `@Immutable`.
17. SyncScreen.kt:105-106 ON_START effect and `LaunchedEffect(environment.id)` both fire on first open; the ViewModel throttle absorbs it. Say so in the comment or keep one.
18. SyncScreen.kt:164 loading spinner has no label, and there is no error state if the environment never loads (suspected infinite spinner, depends on the ViewModel).
19. An empty album and a not-yet-scanned one look identical ("0 of 0 uploaded", "Nothing to send.", :329). Sync now disabled has a stated reason in only one case.
20. File size: SyncScreen.kt (450 lines, six composables) could shed RejectedList and ChangeAlbumDialog into their own files. Not urgent; comments mostly explain why (keep-screen-on, throttle).
21. No `@Preview` anywhere in ui/, so omitting them is consistent; `SyncContent` is stateless and the cheapest place to start (states, 2.0x font, dark).
22. Localisation: inline English matches the whole app (strings.xml has only app_name). The sync screen adds about 60 strings, built inside pure JVM-tested mappers returning final text, so migrating later needs a UiText-style indirection or passing Resources into the mappers. Consistent now, cost grows.
23. Leading-space icon spacing (`Text("  Stop")`) matches UploadScreen (line 96): convention, not a defect; `ButtonDefaults.IconSpacing` would be cleaner.

### Suspected, device-only
- Clipping at 1.3x/2.0x and narrow widths: the main column scrolls (:167) and buttons use `heightIn(min)`, so unlikely except the rejected view (#3).
- RTL: AutoMirrored back arrow and symmetric layout; nothing obviously wrong.

## Spec coverage
Every row of the state-to-UI table has a distinct rendering. Rejected sub-view (Retry per row, Retry all with count), "Upload individual files...", empty state, lost-access card, batch size 1..50, drawer rename "Album sync" and the NavGraph route match the owner's decisions. Retry gives feedback by snackbar ("will be sent again on the next sync", SyncViewModel.kt:280,289). Touch targets are at least 48dp where checked. Only MaterialTheme colours are used; problems are icon plus text plus colour, never colour alone. Collection uses `collectAsStateWithLifecycle` like the other screens.

## androidTest (SyncScreenTest)
Read against current types: the constructors used (`Running` 4 args, `Failed` 4 args, `ChangeAlbumPlan` 9 args, `AlbumSelection`, `EnvironmentInfo`, `MemeBrowserTheme`) match, so it should compile once the unrelated SearchScreenTest is fixed. Substring matches ("Sync now", "Stop") look safe in the states tested. It covers click wiring and a few labels, but not what matters most: the dismiss X, the rejected sub-view (Retry per row, retry-all), the dialog's wipe wording or Cancel, PermissionLost duplicate text, Waiting/Finished/Stopped texts, Stop disabled while Stopping, or large-font layout. The dialog test only clicks confirm and asserts no counts. Texts are covered on the JVM by SyncMappersTest, the right layer.

## What is good
- Clean stateless `SyncContent` + `SyncActions`; single immutable UI state; pure, tested `syncControls`.
- Correct side-effect placement (`LifecycleEventEffect`, `DisposableEffect` with cleanup for keep-screen-on).
- Error texts name the cause, the action and that unsent files stay pending.
- Lazy keyed rejected list loaded only while open; scrollable main view; BackHandler for the sub-view.
- The dialog states exactly what is wiped and what is not, the app-own-folder risk and that a running sync stops.

## Questions for the owner
1. Should the destructive confirm be red and Cancel emphasised?
2. Is raw engine detail on "unexpected" errors wanted for diagnostics, or hidden behind a Details line?
3. Exactly one "Choose album again" when access is lost, or is repetition fine?
4. String resources for this screen now (cheaper before more screens reuse the mappers), or inline until the whole app is localised?
