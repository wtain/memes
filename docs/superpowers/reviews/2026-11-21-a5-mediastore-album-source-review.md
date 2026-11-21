# A5 MediaStore album source: whole-branch review

Spec: docs/superpowers/specs/2026-11-08-android-ingestion-sync/2026-11-21-a5-mediastore-album-source-design.md
Plan: docs/superpowers/plans/2026-11-21-a5-mediastore-album-source.md
Range reviewed: 5ae93650..67444a55 (9 commits), fix wave 36f105db re-reviewed separately.

## Process

Eight tasks, each implemented by a fresh subagent and reviewed task-scoped (spec compliance and quality). Task 7 needed one fix round
(the permission request could launch twice on rotation). One final whole-branch review on the most capable model; one fix wave; one
scoped re-review. The controller re-ran `:app:testDebugUnitTest :app:assembleDebug :app:compileDebugAndroidTestKotlin` on 36f105db:
364 unit tests, 0 failures, 0 errors, exit 0. The emulator test `MediaStoreAlbumTest` and the whole chooser UI were never run
(no emulator or device in the agents' environment).

## Final review result: ready to merge with fixes (0 Critical, 2 Important)

Strengths: the three seams (access, listing, file open) decide by key shape only, so the coordinator, tracker and engine treat album
keys as opaque strings and stayed unchanged. Data safety holds end to end: only FULL access can yield a `Complete` listing,
partial or denied access gives `PermissionLost` before any query, an empty bucket is `Incomplete("album unavailable")`, seconds are
converted to milliseconds in one place, and the engine re-checks the grant before rejecting a `FileNotFoundException` file for good.
The manifest holds exactly the three planned permissions.

Important findings (both fixed in 36f105db, verified by the scoped re-review):

1. "Choose album again" opened the SAF folder picker for a gallery album, which is the picker the feature exists to avoid, and its
   help text said "folder". Both buttons now follow the stored album's key kind (`repickUsesGallery`, `accessLostHelp` in
   `SyncMappers.kt`, JVM-tested); SAF albums keep the old action and wording.
2. The `AndroidClient/CLAUDE.md` note on CI running instrumented tests on API 31 claimed the app had no SDK-level or media-permission
   branches. It now says that no longer holds for album sync and names the uncovered levels (API 29/30, 33, 34+); the tracker's
   NOT-verified line carries the same list.

Also fixed in the wave: "1 images" pluralisation, a unique `RELATIVE_PATH` per run in `MediaStoreAlbumTest`, and two recorded
test deviations in the spec (no coordinator tests with a MediaStore key because the coordinator is key-agnostic; the permission-denied
path has JVM coverage only).

## Deferred minors (not fixed, triaged by the final reviewer)

- Same `BUCKET_ID` with a different name (case-only rename) counts as a different album and asks for a wipe confirmation.
- Same album name and path hint on two volumes (internal and SD card) look identical in the chooser.
- A chooser left on the partial or blocked card stays stale after the user grants access in system settings until they tap again.
- Tapping outside the system permission dialog can be read as "cannot ask again" and shows the open-settings card.
- `MediaStoreAlbumLister` has no `CancellationSignal`.
- `chooser_emptyLoadingAndFailedHaveText` asserts only the Empty text.
- `MediaStoreAlbumAccess` swallows a `RuntimeException` without a log.
- `MSG_PLAN_KEEPS_CHANGING` still says "folder" for a gallery album (found in the re-review, outside the fix diff).

## Not verified (needs the owner's phone)

The real MediaStore query and file open, the chooser and permission cards, and the permission flow on API 29/30, 33 and 34+ were not
run on any device. The owner's checklist is in the spec's Testing section; add "revoke the photo permission, tap Choose album again,
confirm the counts are kept and nothing is wiped" to it.
