# A2 Album Selection and Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Data-layer album access, SAF listing and the change-album flow logic for the Android sync epic.

**Architecture:** A row-source seam over `ContentResolver` so listing decisions are JVM-testable; a pure name helper; an
`AlbumSyncCoordinator` that orders gate, wipe, grant and scan per environment over the A1 `TrackerRepository`.

**Tech Stack:** Kotlin, coroutines, Hilt, JUnit4, coroutines-test.

**Spec:** docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-08-a2-album-selection-and-scan-design.md

## Global Constraints

- Touch only `data/album/`, `di/AlbumModule.kt`, tests, the A2 androidTest file, `AndroidClient/CLAUDE.md`, the A2 spec and plan.
- No screens, no upload logic, no Models.kt, no manifest.

## Review Focus

- Incomplete or PermissionLost never reaches `syncScan`; an empty Complete listing does, with `allowEmpty = true`.
- Gate is called before the wipe; the wipe touches only the one environment; the old grant is kept while another environment uses it.
- Overlapping refreshes of one environment run one scan; environments are independent.

### Task 1: Names and scanner (JVM)
- [x] `AlbumNamesTest`, `SafAlbumScannerTest` red; implement `AlbumNames`, `AlbumAccess`, `AlbumScanner`; green.

### Task 2: Coordinator (JVM)
- [x] `AlbumSyncCoordinatorTest` red; implement `SyncGate`, `AlbumSyncCoordinator`; green (includes ordering, isolation, conflation).

### Task 3: Wiring and device test
- [x] `di/AlbumModule`; `assembleDebug`; androidTest `AlbumAccessTest` (compile only here).

### Task 4: Docs
- [x] CLAUDE.md section, spec, plan.
