# Review: A1 Android tracker data layer

Reviewer: fresh independent reviewer, one iteration. Scope: commits 139282f3, 809d9aef, 0b337257, b748c176 (AndroidClient + A1 spec).
Method: read the spec, epic README, tracker.md (S2, D2), all of `data/tracker/`, `di/TrackerModule.kt`, the JVM tests, `TrackerDaoTest`,
the exported Room schema. SQL checked by hand. The Gradle unit tests were NOT run (build is slow and another agent may hold the
lock); nothing was executed, the findings below are from reading.

## Verdict

**Ship after fixes.** The SQL is correct for every state combination the API can currently produce, the key/index/schema are sound and the
D2 rules hold. There is one real concurrency hole (overlapping scans) and a few latent traps that A2/A3 will hit; all are cheap now.

## Findings

### Important

**I1. Two overlapping scans corrupt presence flags permanently.** `TrackerRepository.kt:21-31`, `TrackerDao.kt:23-27`.
`syncScan` is not serialised and each scan has its own `scanId`. A2 has an automatic Y refresh plus an explicit refresh button.
Scenario: scan 1 (id 100) and scan 2 (id 200) run concurrently. Scan 2 stamps chunk 1 with 200; scan 1 then upserts the same rows
back to 100 (REPLACE of a `findByIds`-based row copy, last writer wins); scan 2 runs `markMissing(env, 200)` and flags every row stamped 100
as `presentInAlbum = 0`. Y, P and `nextPending` then drop real files until the next scan. No data is lost, but the sync silently skips
files. Fix: a `Mutex` in `TrackerRepository` around `syncScan` (and take the same mutex in `resetEnvironment`), or document and enforce
single-flight in A2. Related: `resetEnvironment` during a running scan lets the scan re-insert rows after the wipe (same mutex fixes it;
A2 must also cancel the sync job before changing album).

**I2. The spec says a renamed file keeps its documentId; S2/D2 and the real provider say the opposite.** Spec "D2 on scan" last sentence vs
tracker.md S2/D2 ("renamed file is re-sent and answered duplicate"). ExternalStorageProvider document ids are path-based
(`primary:DCIM/Album/x.jpg`), so a rename or move is a NEW row (PENDING) beside the old UPLOADED/absent row. The code behaves the S2 way
(correct), but the spec sentence is wrong and a reader of the spec will write wrong A2/A4 tests. Consequence to state in the spec: X can
exceed Y after renames, and the renamed file costs one `duplicate` round trip. Fix: correct the spec sentence.

### Minor

**M1. NULL trap in the `supported` aggregate.** `TrackerDao.kt:32`. `NOT (state = 'REJECTED' AND rejectionReason = 'unsupported_type')` is NULL
when `state='REJECTED'` and `rejectionReason IS NULL`, so such a row is silently excluded from Y (CASE falls to ELSE 0), while the Fake
(`FakeTrackerDao.kt:38-40`) counts it. Unreachable today (every REJECTED path supplies a non-null reason; the column is nullable
though). This is exactly where Fake and SQL disagree. Fix: `COALESCE(rejectionReason, '') != 'unsupported_type'` or `IS NOT 'unsupported_type'`
(SQLite null-safe), or make the column NOT NULL for REJECTED via a CHECK.

**M2. Magic strings duplicated in SQL.** `TrackerDao.kt` hard-codes `'PENDING'/'UPLOADED'/'REJECTED'` and `'unsupported_type'` while the
enum and `SupportedImages.REASON_UNSUPPORTED_TYPE` exist. Renaming an enum entry compiles and silently zeroes the counts (Room stores
enums by name). Fix: add a DAO-level test per state (see gaps), or a comment next to the enum "names are persisted and used in SQL".

**M3. `markRejected` can demote an UPLOADED row, `markUploaded` can promote a REJECTED one.** `TrackerDao.kt:46-57`. Neither has a state guard.
Late backend answer for a file already marked uploaded would decrement X. D2 says an uploaded file is never re-sent, so this needs a
buggy caller, but a `WHERE state != 'UPLOADED'` on `markRejected` costs nothing and protects the invariant. Decide and document.

**M4. Room Flows are not de-duplicated.** `observeCounts` / `observeRejected` re-run and re-emit on every table write, so a 5,000-file scan
(10 chunks) re-queries the full rejected list (can be thousands of HEIC rows) ten times, and each `markUploaded` batch again. Add
`.distinctUntilChanged()` in the repository (cheap for `TrackerCounts`, a data class), and consider paging for `rejectedFiles`.

**M5. Repository surface gaps for A2.** `TrackerRepository` exposes `setAlbum` but not `clearAlbum` (only the store has it), and there is no single
`changeAlbum` operation. The wipe and the DataStore write are two stores, not atomic. Correct order is: cancel sync, wipe tracker, then
set the new album (a crash between leaves "old album, empty tracker", which just rescans; the reverse order leaves "new album, stale old
rows"). Provide `changeAlbum(env, uri, name)` implementing that order so A2 cannot get it wrong.

**M6. Entity leaks as the public model; `ScannedFile` lives in `TrackedFileEntity.kt`.** The repository returns `TrackedFileEntity` to UI/A3,
tying them to Room column names. Acceptable for this app size, but move `ScannedFile` to its own file and consider a small
`TrackedFile` domain type before A4 binds screens to it. `upsertAll` is really "replace all" (naming OK, but the
doc should say REPLACE rewrites the row).

**M7. No migration story stated.** Version 1, `exportSchema = true`, no `fallbackToDestructiveMigration`, no migrations dir. A future version bump
without a Migration crashes at open; a destructive fallback would be acceptable here (server answers `duplicate`) but must be a
conscious choice. Add one line to the spec/CLAUDE.md.

**M8. Auto-backup.** Manifest has `allowBackup="true"` with no rules, so `tracker.db` (+WAL) and the DataStore are backed up and restored on a new
phone with a stale tree URI and no SAF grant. Not harmful (A2 revoked-grant handling covers it), but decide: exclude both, or keep
(preserves X). Question for the owner/A2.

**M9. Scan failure modes.** An empty or failed listing (revoked grant, provider error returning no rows) followed by `syncScan(emptyList())`
marks the whole environment absent (Y becomes 0). Not data loss and self-heals on the next scan, but A2 should not call `syncScan` on a
listing error. Worth a KDoc line on `syncScan`.

**M10. `scanId` = `System.currentTimeMillis()` doubles as `now`.** Two scans in the same millisecond share an id (then I1 is moot but
markMissing is wrong the other way), and a clock set backwards is harmless only because the compare is `!=`. A counter or UUID/random Long is cleaner.
Also `TrackerSettingsStoreTest` "restart" test is fine in intent but is not a restart (copy of the file); acceptable, just rename.

## What is good

- Composite PK `(environmentId, albumTreeUri, documentId)` plus the `(environmentId, state)` index match every query (PK prefix serves
  `findByIds`/`markUploaded`; the index serves counts and `nextPending`); no full-table scans beyond one environment.
- Every UPDATE/DELETE is environment-scoped, so wipe of one environment is correct; markMissing is deliberately environment-wide.
- Bind-variable budget: `findByIds` 500+2, `markUploaded` 500+4 (even if `:at` is bound twice), under 999.
- `markUploaded` is idempotent via `COALESCE(uploadedAt, :at)`; unknown ids are no-ops; counts SUMs are COALESCEd so the empty
  environment returns one row of zeros.
- Cancellation-safe: each chunk is a transaction, a cancelled scan leaves a consistent table and only skips markMissing.
- Pure `ScanReconciler` and `SupportedImages` are cleanly separated and easily tested; MIME set and 20 MB limit match `UploadViewModel`
  exactly (`>` 20 MB is too large, `<=` allowed, same as `isValidSize`).
- Singleton DB through Hilt, Room on its own executors (no main-thread query), DataStore clamped on both read and write.
- Schema JSON committed and consistent with the entity (PK order, index name).

## Test gaps

Everything that matters about the SQL is exercised only by one androidTest that cannot run here, and that test is a single scenario.
Missing real-SQL cases:
- supported count with a REJECTED row whose reason is NULL (M1), and with a backend-rejected (free-text) reason being counted in Y.
- `markMissing` leaves other environments alone (the Fake test covers this, the SQL is untested) and is skipped for already-absent rows.
- Reappearance after absence restores the old state (rejected stays rejected) through real REPLACE.
- `nextPending` tie-break on equal `lastModified`, `LIMIT`, absent rows excluded; `observeRejected` ordering.
- `markUploadedAll` with more than 500 ids (the chunk path) and `findByIds` with exactly 500 and 501.
- Two environments with identical album/document ids (key isolation) and `deleteEnvironment` isolation.
- Flow re-emission after a write (invalidation tracking) and the transaction rollback when a chunk throws.
- Concurrent scan vs `markUploaded` (I1).
Fake limitations: `applyScanChunk` in the Fake calls `super`, so transactionality is not modelled, `transactionCount` only counts calls; the
Fake sorts with UTF-16 `String` order while SQLite uses BINARY (UTF-8) order (differs only for supplementary characters in ids);
the Fake has no NULL semantics (M1). The repository tests largely assert behaviour (D2, deleted/reappeared, wipe isolation), not
implementation; the 5,000-file test mainly proves the chunk count. `TrackerSettingsStoreTest` copying the file is a reasonable workaround
for the one-DataStore-per-file rule but proves persistence of the bytes, not that two process lifetimes agree.
Suggestion: since Robolectric is deliberately absent, at least run `TrackerDaoTest` on an emulator once before merge and record it, and
expand it with the cases above (cheap, one file).

## Questions

1. Is X > Y acceptable on screen after renames/moves (old row UPLOADED+absent plus new row UPLOADED+present)? The README says "X of Y"; the
   status screen may want to display it gracefully.
2. Should a too-large local rejection ever be re-evaluated if the file later shrinks (D2 says no; confirm)?
3. Keep or exclude the tracker from Android auto-backup (M8)?
4. Should `markRejected` be allowed to override UPLOADED (M3)?
5. A3 needs to map backend `failed[]` entries (by file name) back to documentIds: is the batch order/name mapping going to be kept by A3
   from `nextPending` entities? Per-file `markRejected` is one transaction each; a batch variant would help.
