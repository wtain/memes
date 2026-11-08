# A3 sync engine: implementation plan

Spec: docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-a3-sync-engine-design.md

Done in this order, tests alongside each step (JVM only):

1. Upload seam (`data/repository/BatchUploader.kt`): `UploadFile`, `AlbumFileSource`, `UploadAttempt`, `BatchUploader`, `RetrofitBatchUploader`
   (HTTP/IO/other mapped to values, Retry-After, `detail`). Test: `RetrofitBatchUploaderTest`.
2. `SyncState` / `SyncSummary` / `SyncError` (`data/sync/SyncState.kt`).
3. `SyncEngine` (per-environment job and state, run loop, error policy, `NonCancellable` writes) and `EngineSyncGate`. Tests: `SyncEngineTest`,
   `SyncGateTest`, fakes in `SyncFakes.kt` (real `TrackerRepository` over `FakeTrackerDao`, test dispatcher, virtual time).
4. Wiring: `ContentResolverAlbumFileSource`, `di/SyncModule` (engine scope, dedicated client per environment URL, settings binding),
   `AlbumModule.provideSyncGate` -> `EngineSyncGate`.
5. `AndroidClient/CLAUDE.md` section, spec status.
6. `:app:testDebugUnitTest` and `assembleDebug` (checks the Hilt graph).
