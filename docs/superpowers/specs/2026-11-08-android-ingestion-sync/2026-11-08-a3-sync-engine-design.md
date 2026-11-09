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
`SyncError`: `NETWORK, SERVER, INBOX_FULL_OR_UNWRITABLE (503), DISK_LOW (507), RATE_LIMITED, UNEXPECTED, NO_ALBUM, PERMISSION_LOST, FILE_READ_ERROR`.
`Waiting.reason`: `RATE_LIMITED` (server said 429) or `PACING` (the app waits by itself to stay under the server limit; `attempt` = 0).
State is in memory only (a new process starts `Idle`); a terminal state stays until the next start. X/Y/R/P come from `AlbumSyncCoordinator.counts`.

## Run loop

1. Needs a stored album and a granted permission, else `Failed(NO_ALBUM | PERMISSION_LOST)`. (my call: no implicit scan; the user refreshes.
   Zero pending files finishes at once with an empty summary.)
2. Each iteration: batch size from settings (1..50, read before every batch, so a change applies from the next one); `nextPending(env, size)`,
   re-queried each time, so files a scan adds during the run join the same run. None left: `Finished`.
3. Pre-check: each file of the batch is opened once. Only a `FileNotFoundException` (the document is gone) rejects it locally with reason
   `file_unavailable` (my call: reuse REJECTED, not "missing", because "missing" is a scan-owned flag). Any other failure (IOException, null
   stream, SecurityException, cloud placeholder, unmounted volume) is transient: the run fails `FILE_READ_ERROR` and rejects nothing (with the
   grant gone: `PERMISSION_LOST`). A "gone" file with the grant gone also fails `PERMISSION_LOST` and rejects nothing.
4. One multipart POST per batch, files streamed from their document URI (never read into memory, length unknown so the request is chunked),
   part name = tracked display name, content type = tracked mime.
5. The response is applied in `NonCancellable`: `uploaded[]` with status `accepted`/`duplicate` -> one `markUploaded` per tree; `failed[]` ->
   `markRejected(reason)`. Matching is by `index` (position in the request) only. A 200 whose entries lack `index` or carry status `ok` is an old
   backend (it wrote outside the ingestion inbox): the run fails `SERVER` "backend too old: update the backend before syncing" and marks nothing
   (my call; the name-matching fallback was dead code and is removed). Anything unmatched, an unknown status or an out-of-range index leaves the
   file PENDING; a file is never UPLOADED without an explicit entry. Two batches in a row that settle zero files fail the run (`UNEXPECTED`) to avoid an endless loop.

## Error policy (each pinned by a test)

| Cause | Result | Files |
|---|---|---|
| Network failure, timeout | `Failed(NETWORK)`, no automatic retry | batch stays PENDING |
| 500 | the batch's files are sent ONE AT A TIME, each once; a file that gets 500 alone is REJECTED `server_error` (never retried by later syncs) and the run goes on; 3 such files in a row (a success resets) -> `Failed(SERVER)`; a non-500 failure alone follows this table | rest PENDING |
| other 5xx (not 503/507) | `Failed(SERVER)` | PENDING |
| a file cannot be read (not FileNotFound), also mid-upload | `Failed(FILE_READ_ERROR)`; with the grant gone `Failed(PERMISSION_LOST)` | PENDING |
| 503 | `Failed(INBOX_FULL_OR_UNWRITABLE)`: ingestion must be run or the server fixed | PENDING |
| 507 | `Failed(DISK_LOW)` | PENDING |
| 429 | `Waiting(RATE_LIMITED)` 60 s (a `Retry-After` header, as seconds or HTTP-date, wins; capped at 300 s, garbage ignored), then the same batch is retried; stoppable; 10 in a row (the 11th) -> `Failed(RATE_LIMITED)`; a success resets the count | PENDING |
| other 4xx, unreadable 200 body, invalid environment URL | `Failed(UNEXPECTED)` with the code and body detail | PENDING |
| any other exception in the engine | `Failed(UNEXPECTED)` | PENDING |

Pacing: the server allows 10 upload requests per IP per 60 s (`upload_limiter`). `UploadPacer` lets the app start at most 9 per rolling 60 s
per environment id (constants in one place), waiting as `Waiting(PACING, resumeAt)` (stoppable) and re-reading the pending files afterwards;
the 429 path stays as the safety net for other clients on the same IP. Two environments on one host are not coordinated with each other.

State changes: every change goes through one `publish` under the engine lock, ordered against `stop`. After a stop request a run can only end
as `Stopped` (summary kept), even if its result was `Finished`/`Failed`; a non-Exception Throwable ends as `Failed(UNEXPECTED)`.

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
and gate with the A2 coordinator: the sync is stopped before the wipe), `RetrofitBatchUploaderTest` (parts, HTTP/IO mapping, Retry-After parsing, real multipart bytes via OkHttp's `MultipartBody` into an okio `Buffer`,
bad mime, local read failures), `SyncEngineReviewFixesTest` (review fixes, state sequences, cancellation points), `UploadPacerTest`. Virtual time for the
60 s waits. Not covered: real network, large multipart timings, real SAF streaming, live backends.

## Open for the owner

None blocking. For E1: confirm on the phone that a chunked multipart of a 50 x 20 MB batch passes the real backend and Wi-Fi.

## Review outcome (A3 review, one fix iteration)

Report: docs/superpowers/reviews/2026-11-08-a3-sync-engine-review.md.

- I1 fixed: only `FileNotFoundException` rejects (`file_unavailable`); everything else fails the run `FILE_READ_ERROR` with files pending. A null stream
  from the provider is now an IOException (transient), not FileNotFound.
- I2 rejected (my call): the controller reproduced the real multipart path (raw UTF-8 bytes in `filename="..."`) through the installed Starlette/FastAPI and
  the name comes back correct, so no backend change. Instead a test pins what the client sends: Cyrillic names as raw UTF-8, a `"` as `%22`. The server's
  sanitiser turns a literal `%22` into a safe character, and `index` (not the name) maps results, so tracker correctness never depends on names.
  E1 device check: upload a Cyrillic-named file and see it in the inbox with the right name.
- I3 fixed: `UploadPacer` (9 per 60 s, see Error policy); 429 handling kept as the safety net.
- M1 fixed: Retry-After as delta-seconds and HTTP-date (injected clock), cap 300 s, garbage ignored.
- M2 fixed: 500 isolates files one at a time, poison files REJECTED `server_error`, 3 in a row stop the run. Accepted risk (my call): if the server is
  broken for every file, the three files that hit the limit are rejected for good; they show in the rejected list.
- M3/M4/M5 fixed: one `publish` under the lock, stop sets its flag under the same lock, Throwables become `Failed(UNEXPECTED)`, only a real cancellation is Stopped.
- M6 fixed: a SecurityException/read failure while writing the request is `AccessDenied`/`ReadFailed`, mapped to `PERMISSION_LOST` (grant gone) or `FILE_READ_ERROR`.
- M7 skipped (my call): Starlette spools multipart parts to disk; no client change.
- M8 fixed: a malformed stored mime type falls back to `application/octet-stream` for the part (the server then decides).
- M9 skipped, documented (my call): sync is foreground-only by the owner's decision; Android may freeze the app in the background, the run then ends
  `NETWORK` and a restart resumes. A4/E1 should tell the user to keep the app open.
- M10 fixed: fallback removed; old backends are detected and fail the run without marking anything.
- M11 done: `UploadPacer`, `mapResponse` (pure) with the `OutcomeStore` seam, and `parseRetryAfter` are separate and tested. The loop stays in `SyncEngine`.
- Test gaps: the NonCancellable test now has suspending tracker writes (mutation-checked); stop during the pre-check and during apply; state sequences;
  real multipart shape. Gaps 6, 8 and 9 (huge backlogs, `SyncModule`, Room transactions) stay open.
