# A4 sync screen: implementation plan

Spec: docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-a4-sync-screen-design.md

Test-first where it pays (mappers, retry API, ViewModel); the Compose layer is thin and stateless.

1. Baseline: `:app:testDebugUnitTest` passes (218) before any change.
2. Retry API (data/tracker, additive): `RejectionReasons.SERVER_ERROR` + `TrackerSql.SERVER_ERROR`; DAO `retryRejectedByReason` / `retryRejectedFile`;
   `TrackerRepository.retryServerErrors` / `retryServerError` under the Mutex; `FakeTrackerDao` mirrors both; tests in `TrackerRepositoryTest`
   (only server_error rows move, other reasons/UPLOADED/other environments untouched) and `TrackerSqlConstantsTest`.
3. Pure mappers `ui/sync/SyncMappers.kt` (progress fraction, `syncControls`, scan/sync/error/reason texts, dialog lines) with `SyncMappersTest`.
4. `SyncViewModel` (one `SyncUiState`, `flatMapLatest` on the selected environment, local state for plan/message/busy/dismissal/rejected flag) with
   `SyncViewModelTest` over mockk'd coordinator, engine, tracker and environment repository (all final classes; mockk avoids new interfaces).
5. `SyncScreen` (stateful wrapper: picker, lifecycle refresh, keep-screen-on) over the stateless `SyncContent` + `SyncActions`.
6. Wiring: `NavGraph` `sync` route (the Upload entry opens it; `upload` route kept and reached by "Upload individual files..."), drawer label.
7. `UploadViewModel` uses `SupportedImages` constants (behaviour unchanged).
8. `androidTest/.../ui/sync/SyncScreenTest` for the stateless content (the source set does not compile today because of the unrelated `SearchScreenTest`).
9. Docs: spec status, `AndroidClient/CLAUDE.md` section "Sync screen (A4)".
10. Verify: `:app:testDebugUnitTest --no-daemon`, `assembleDebug --no-daemon`, optional `lintDebug`. Commit own paths only.
