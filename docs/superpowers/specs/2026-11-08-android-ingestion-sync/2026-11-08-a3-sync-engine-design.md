# A3: Sync engine

Status: done
Originates from: docs/superpowers/specs/2026-11-08-android-ingestion-sync/README.md
Plan: docs/superpowers/plans/2026-11-08-a3-sync-engine.md

The coroutine engine behind "Sync now": uploads an environment's PENDING files in batches and records the outcome in the A1 tracker.
No screen (A4 builds it on this API). Code: `data/sync/`, upload seam in `data/repository/BatchUploader.kt`, Hilt in `di/SyncModule.kt`.

## Behaviour (owner's decisions, binding)

Manual, foreground only, no WorkManager. Per environment id: one tracker, one run at a time, independent state. A batch that transfers
successfully marks its files UPLOADED (X goes up per batch). The user can stop a run. A file the backend rejects is marked REJECTED with the
reason and is not retried on later runs.

## Public API (`SyncEngine`, Hilt singleton)

- `fun start(environmentId): Boolean`: launches the run in an engine-owned scope (`@SyncScope`, `SupervisorJob + Default`) and returns at once;
  false (no-op) when the environment already runs. (my call: a non-suspending launcher, so a screen or ViewModel leaving composition does not
  end a sync; the scope dies with the process, and a restart finds unsent files PENDING.)
- `fun stop(environmentId)`: cancels the run, including the in-flight HTTP call. Files of an unfinished batch stay PENDING and are not marked.
- `suspend fun cancelAndAwait(environmentId)`: stop and join (the `SyncGate`, via `EngineSyncGate`, bound in `AlbumModule.provideSyncGate`).
- `fun state(environmentId): StateFlow<SyncState>`, `fun isRunning(environmentId): Boolean`.

`SyncState`: `Idle`; `Running(startedAt, batchNumber, currentBatchSize, summary)`; `Waiting(RATE_LIMITED, resumeAt, attempt, summary)`;
`Stopping(summary)`; `Finished(summary, at)`; `Stopped(summary, at)`; `Failed(error, message, summary, at)`.
`SyncSummary(uploaded, duplicates, rejected)`: `uploaded` = `accepted`, `duplicates` = `duplicate` (both raise X), `rejected` = settled as rejected.
`SyncError`: `NETWORK, SERVER, INBOX_FULL_OR_UNWRITABLE (503), DISK_LOW (507), RATE_LIMITED, UNEXPECTED, NO_ALBUM, PERMISSION_LOST`.
State is in memory only (a new process starts `Idle`); a terminal state stays until the next start. X/Y/R/P come from `AlbumSyncCoordinator.counts`.

## Run loop

1. Needs a stored album and a granted permission, else `Failed(NO_ALBUM | PERMISSION_LOST)`. (my call: no implicit scan; the user refreshes.
   Zero pending files finishes at once with an empty summary.)
2. Each iteration: batch size from settings (1..50, read before every batch, so a change applies from the next one); `nextPending(env, size)`,
   re-queried each time, so files a scan adds during the run join the same run. None left: `Finished`.
3. Files that cannot be opened are not sent: rejected locally with reason `file_unavailable` (my call: reuse REJECTED, not "missing", because
   "missing" is a scan-owned flag). If that happens while the album grant is gone, the run fails `PERMISSION_LOST` and rejects nothing.
4. One multipart POST per batch, files streamed from their document URI (never read into memory, length unknown so the request is chunked),
   part name = tracked display name, content type = tracked mime.
5. The response is applied in `NonCancellable`: `uploaded[]` with status `accepted`/`duplicate` -> one `markUploaded` per tree; `failed[]` ->
   `markRejected(reason)`. Matching is by `index` (position in the request). Without `index` (old backend): first unmatched file with the same
   name, in order. Anything unmatched, an unknown status or an out-of-range index leaves the file PENDING; a file is never UPLOADED without an
   explicit entry. Two batches in a row that settle zero files fail the run (`UNEXPECTED`) to avoid an endless loop.

## Error policy (each pinned by a test)

| Cause | Result | Files |
|---|---|---|
| Network failure, timeout | `Failed(NETWORK)`, no automatic retry | batch stays PENDING |
| 5xx other than 503/507 (incl. 500) | `Failed(SERVER)` | PENDING |
| 503 | `Failed(INBOX_FULL_OR_UNWRITABLE)`: ingestion must be run or the server fixed | PENDING |
| 507 | `Failed(DISK_LOW)` | PENDING |
| 429 | `Waiting(RATE_LIMITED)` 60 s (a `Retry-After` header wins, capped at 300 s), then the same batch is retried; stoppable; 10 in a row (the 11th) -> `Failed(RATE_LIMITED)`; a success resets the count | PENDING |
| other 4xx, unreadable 200 body, invalid environment URL | `Failed(UNEXPECTED)` with the code and body detail | PENDING |
| any other exception in the engine | `Failed(UNEXPECTED)` | PENDING |

`CancellationException` always propagates; stop ends in `Stopped`. Narrow window (documented, accepted): if the stop lands while the response
is still being delivered by OkHttp, the response is dropped; those files stay PENDING and the next run gets `duplicate` for them.

## Concurrency

The engine never holds the tracker Mutex across network I/O (it only calls short repository methods). A scan (`refreshScan`) may run during a
sync: A1's Mutex serialises it with `markUploaded`, and the counts Flow keeps updating. `markRejected` never demotes UPLOADED (A1 guard).
`start` checks `Job.isCompleted`, not `isActive`, so a run that is still unwinding after `stop` blocks a new one.

## Upload transport (my call)

The app-wide OkHttp client rewrites every request to the CURRENTLY SELECTED environment and reads with a 30 s timeout. Using it would send
environment A's files to environment B if the user switched the selector mid-run, and 30 s is too short for the server to check 50 files of up to
20 MB. So `SyncModule` builds a dedicated client (connect 10 s, write 2 min, read 5 min, no call limit) and one Retrofit per environment base URL
(resolved from `EnvironmentStore` for each batch). `UploadViewModel` and the global client are unchanged.

## Tests

`data/sync/`: `SyncEngineTest` (happy path 125 files = 50/50/25, batch 1 and 7, batch size changing mid-run, empty, no album/grant, duplicates,
scan during run, stop, stop while waiting, stop right after start, received response survives stop, start twice, two environments, index
mapping, missing index, partial coverage, stall, unreadable file, every error class, 429 retry/Retry-After/limit), `SyncGateTest` (real engine
and gate with the A2 coordinator: the sync is stopped before the wipe), `RetrofitBatchUploaderTest` (parts, HTTP/IO mapping). Virtual time for the
60 s waits. Not covered: real network, large multipart timings, real SAF streaming, live backends.

## Open for the owner

None blocking. For E1: confirm on the phone that a chunked multipart of a 50 x 20 MB batch passes the real backend and Wi-Fi.
