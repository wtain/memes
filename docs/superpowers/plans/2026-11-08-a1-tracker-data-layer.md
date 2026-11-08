# A1 Tracker Data Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Room-backed per-environment upload tracker, DataStore settings and a repository for the Android app.

**Architecture:** Pure classification and reconciliation functions, a `TrackerDao` interface implemented by Room, a repository over
the DAO and a settings store. Chunked scans.

**Tech Stack:** Kotlin 2.4.10, Room 2.8.4 (KSP), DataStore, Hilt, JUnit4, coroutines-test, turbine.

**Spec:** docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-a1-tracker-data-layer-design.md

## Global Constraints

- Package `com.memebrowser.app.data.tracker`; touch only data/**, di/** (new module), build files, schemas, tests, CLAUDE.md.
- Chunk size 500; batch size 1..50 default 50; size limit 20 MB; sort `lastModified DESC, documentId ASC`.

## Review Focus

- File that changes size after upload stays UPLOADED (ScanReconciler and repository tests).
- Uploaded file deleted from album stays in X, leaves Y (repository test).
- Re-running markUploaded keeps the first uploadedAt.
- Reset of one environment leaves the others.
- Scan of 5,000 files is chunked and counts are right.

### Task 1: Build setup
- [ ] Add Room catalog entries, `ksp` dependency and schema arg; verify the build resolves. Commit.

### Task 2: Classification and reconciliation (JVM)
- [ ] Tests `SupportedImagesTest`, `ScanReconcilerTest`, run red, implement `SupportedImages.classify(mime, size): String?` and
  `ScanReconciler.reconcile(...)`, run green, commit.

### Task 3: Entity, DAO, database, Hilt module
- [ ] Define entity/DAO/database/module, `TrackerDaoTest` in androidTest (compile only), commit.

### Task 4: Repository
- [ ] `FakeTrackerDao` + `TrackerRepositoryTest` red, implement `TrackerRepository`, green incl. 5,000-file scan, commit.

### Task 5: Settings
- [ ] `TrackerSettingsStoreTest` (temp-file DataStore) red, implement, green, commit.

### Task 6: Docs and final run
- [ ] AndroidClient/CLAUDE.md note; run `:app:testDebugUnitTest` and `assembleDebug`; commit.
