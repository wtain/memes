# A3 Sync engine: review

Reviewer: fresh independent Android reviewer (Kotlin, Coroutines, OkHttp/Retrofit, Hilt). Commit `8064dc6a`. Axes: correctness vs owner behaviour
and the B1 contract, lifecycle/concurrency, readability, test quality. Report only; nothing was fixed. A2 code was not reviewed, only how A3 uses it.

Verification: `:app:testDebugUnitTest --tests "com.memebrowser.app.data.sync.*"` was run (no other Gradle/Java process was active): BUILD SUCCESSFUL.
Everything marked "proven" is by reading code (A3, A1, B1 `uploads.py`, installed Starlette 1.7.0); "suspected" is reasoning I could not run.

## Verdict

**Ship after fixes.** The batch loop is sound: a file becomes UPLOADED only through an explicit `accepted`/`duplicate` entry, mapping is by `index`, the
tracker Mutex is never held across I/O, the gate is called outside the coordinator lock, and the error classes match the B1 server. Two items should be
fixed or consciously accepted before A4 builds on it: transient read errors permanently reject files (I1), and non-ASCII (Cyrillic) names are mangled
on the way to the inbox (I2, mostly a B1 fix but only visible through A3). The rest is minor.

## Findings

### Critical

None.

### Important

**I1. A transient read failure permanently rejects a good file (no retry, ever).**
`SyncEngine.kt:177-189`, `isReadable` `:236-243`, `REASON_FILE_UNAVAILABLE` `:281`. Proven by code.
`isReadable` swallows every `Exception` (SecurityException, IOException, a null stream, a provider that is restarting, a cloud-only Samsung Gallery
placeholder, an unmounted volume) as "unreadable", and the engine then calls `markRejected(file_unavailable)`. REJECTED rows are never selected by
`nextPending` and `ScanReconciler` keeps the state of existing rows, so the file is lost until the owner wipes the tracker by changing the album. The
owner's rule "rejected files are not retried" was meant for files the *backend* rejected. Only `grant gone` is guarded (`:180`).
Scenario: one provider hiccup while the 50-file pre-check runs rejects up to 50 memes for good; they show in the rejected list with `file_unavailable`.
Fix: reject locally only on `FileNotFoundException` (the file really is gone); treat any other exception as a run-level failure (new `SyncError`, for
example `ALBUM_UNREADABLE`) leaving the files PENDING. Alternatively let `file_unavailable` rows go back to PENDING on the next successful scan of that
file. Also note the pre-check opens up to 50 streams sequentially on the IO dispatcher; a blocking `openInputStream` is not interruptible, so Stop
can be slow there (minor).

**I2. Non-ASCII file names are mangled by the server; the name preservation in the epic charter does not hold for Cyrillic.**
`BatchUploader.kt:476` (`MultipartBody.Part.createFormData("files", file.displayName, ...)`). Proven by code, not by a live request.
OkHttp writes `filename="..."` with raw UTF-8 bytes (quotes become `%22`, CR/LF `%0D`/`%0A`). Starlette's `_user_safe_decode(options[b"filename"],
self._charset)` has `_charset == ""` (no `_charset_` field) so `.decode("")` raises `LookupError` and falls back to latin-1; python-multipart also
rejects the RFC 5987 `filename*=` form, so the client has no alternative encoding. B1's security review (M8) already recorded "Starlette decodes headers
as latin-1 ... accepted as mojibake names". Result: `мем.jpg` reaches `sanitize_filename` as `Ð¼ÐµÐ¼.jpg` and is saved/echoed that way. `index` mapping is
unaffected, so tracker correctness is intact; only the inbox name (and the echo) are wrong. A name containing `"` is stored with a literal `%22`.
Fix (server, B1): in `uploads.py` try `name.encode("latin-1").decode("utf-8")` and keep the original on failure. A3 then needs no change. Add a client
test that the part header contains the UTF-8 name, and a B1 test with a Cyrillic name.

**I3. A 429 costs a full re-upload of the batch, and nothing paces requests.**
`SyncEngine.kt:211-222`; server `uploads.py:100-103`. Proven by code.
FastAPI parses the multipart body (`files: list[UploadFile] = File(...)`) *before* the handler runs, and the limiter is checked inside the handler, so a
429 is answered only after the whole request body was received. With batch size 1..few, every 10th request is a wasted transfer plus a 60 s wait; at the
default 50 x ~5 MB it can waste hundreds of MB per 429. Behaviour is "correct" (wait, retry, 10 in a row fails) but wasteful and slow.
Fix: a client-side pacer in the uploader or engine that keeps a window of the last 10 request start times and sleeps before the 11th within 60 s
(virtual-time testable), keeping the 429 path as a safety net. Cheap and removes almost all 429s from a single-client sync. Other clients on the same IP
(web UI, the old Upload screen) still share the budget, hence the safety net stays.

### Minor

**M1. `Retry-After` handling is narrower than the spec text.** `BatchUploader.kt:461`: only delta-seconds via `toLongOrNull()`; an HTTP-date, "0" or
junk falls to 60 s (or 1 s for 0/negative through `coerceIn(1, 300)`). The B1 server sends no header, so harmless today, and the cap (300 s) is applied.
Either parse the HTTP date (`okhttp3.internal.http.toHttpDateOrNull` is internal; use `java.time` RFC 1123) or fix the design wording ("seconds only").

**M2. A poison file/batch stalls the sync permanently.** B1 maps any per-file write error to a 500 for the whole request (charter decision). The engine
then ends `Failed(SERVER)` with the same batch first in line next time (ordering is deterministic), so the owner cannot get past it except by lowering
the batch size. Consider a bisect-on-500 (retry the batch as halves) or a "skip this file" affordance in A4. Question for the owner below.

**M3. Stop vs a late terminal result.** `runEnvironment` writes whatever `runLoop` returns (`:151`). If Stop lands while the loop is already about to
return `Failed(...)`/`Finished` without another suspension point, the UI goes Stopping -> Failed/Finished instead of Stopped. Cosmetic, narrow (suspected).

**M4. `stop()` mutates state outside the lock** (`:119-129`): it picks the job under `lock`, then updates the state without it. If that job completes
and a new `start` runs in between, the new run's `Running` is flipped to `Stopping` (the `job.cancel()` hits the finished job) until the next batch
overwrites it. Cosmetic, very narrow (suspected). Do the state update inside the same `synchronized` block.

**M5. `invokeOnCompletion` maps every non-terminal state to `Stopped`** (`:103-112`), including a job that ended with a non-`Exception` Throwable (an `Error`
escapes `catch (e: Exception)` at `:155` and crashes through the handler-less scope, but in tests/other hosts would be reported as "Stopped"). Add a
`CoroutineExceptionHandler` that records `Failed(UNEXPECTED)` or let the handler look at its `cause`.

**M6. Mid-upload grant loss is classified UNEXPECTED, not PERMISSION_LOST.** A `SecurityException` thrown from `writeTo` (`BatchUploader.kt:473`) becomes
`UploadAttempt.Unexpected`. Re-check `access.isGranted` on `Unexpected` before reporting.

**M7. Server memory at batch 50.** `uploads.py` reads each file into `bytes` and keeps all candidates until `store()`, so a 50 x 20 MB batch is ~1 GB on
the backend (B1 matter; A3's default of 50 makes it reachable). Not a client bug; see the question.

**M8. `part()` is outside the try** (`BatchUploader.kt:455`): a malformed tracked `mimeType` makes `toMediaType()` throw `IllegalArgumentException`, which
the engine turns into `Failed(UNEXPECTED)` for the whole run and the same batch fails forever. Unlikely (A2 filters on supported types) but the failure
mode is "stuck", so use `toMediaTypeOrNull()` with an `application/octet-stream` fallback or reject that file locally.

**M9. Foreground-only is not enforced.** Leaving the screen keeps the engine going (by design) but there is no foreground service; Doze/App Standby can
freeze the process or the network mid-batch and the 5 min read timeout then ends in `NETWORK`. Consistent with "manual, foreground only"; document it
for A4 (keep the screen on / warn) and E1.

**M10. Name-based fallback is effectively dead code.** `SyncEngine.kt:251-255`. A pre-B1 backend answers `status: "ok"` (not accepted/duplicate), so no
entry is ever applied and the run fails with the "did not report a result" error anyway. B1 is rolled out first with no compatibility window. Remove the
fallback (and its test) or keep it and accept `ok`; today it only adds a mutating `usedNames` set and the possibility to match a position `done` by index.

**M11. Readability/structure.** `SyncEngine.kt` (295 lines) holds the scope qualifier, `SyncSettingsSource`, its adapter, the engine, a 90-line `runLoop`
with a nested `when`, the response applier and `EngineSyncGate`. Suggested split: `ResponseApplier` (pure: response + batch -> settled/uploaded/
rejected lists, trivially unit-testable), `HttpOutcome -> SyncError` mapper, `EngineSyncGate` into `data/album` or `di`. `REASON_FILE_UNAVAILABLE`
belongs next to the tracker's reason constants (`TrackerSql`) because Y/R counting depends on reason strings. `SyncState.Failed.message` is hard-coded
English including server detail; A4 should map `error` to resources and show `message` as detail only. `Running` carries batch-level info only: for a
50 x 20 MB batch there is no byte progress for minutes (A4 note).

## What is good

- Invariant "UPLOADED only from an explicit accepted/duplicate entry" holds on every path (`:256-263`); unknown status, out-of-range/negative index and
  the same index in `uploaded` and `failed` (first wins) cannot mark anything wrongly.
- Index mapping matches B1 exactly: the client sends only the readable files, in order, and the server `index` is the position among `files` parts.
- Response is applied in `NonCancellable` and this is reachable and correct: after Retrofit's `await` resumes there is no further suspension before
  `withContext(NonCancellable)` is entered (it runs undispatched on the same dispatcher), so a stop landing in that gap does not lose the response. If
  the stop lands earlier (during OkHttp's delivery), prompt cancellation drops the response and the files stay PENDING: the documented, accepted window,
  and harmless because the next run gets `duplicate`. The only other loss window (process death between response and `markUploaded`) is the same case.
- `markUploaded` is one transaction per tree; `markRejected` cannot demote UPLOADED (A1 guard), so a late answer cannot decrement X.
- Zero-progress guard terminates the partial-response case (the uncovered file ends up alone, stalls twice, run fails) and `sendable.isEmpty() -> continue`
  always progresses because each unreadable file is settled.
- Batch size is read per batch; `nextPending` re-queried per batch so scan-added files join the run; empty pending finishes at once.
- Per-environment isolation is real: dedicated client + Retrofit per base URL, so the global base-URL-rewriting interceptor (which would redirect to the
  *selected* environment) is bypassed correctly; separate job/state per environment; simultaneous syncs are tested.
- Deadlock analysis: `AlbumSyncCoordinator.confirmChangeAlbum` calls `gate.cancelAndAwait` *outside* `lockOf(env).withLock` (`AlbumSyncCoordinator.kt:187`
  vs `:188`), and the engine never takes the coordinator lock; the engine only takes the tracker Mutex indirectly and never across network I/O (the
  Mutex-guarded repo methods `syncScan/changeAlbum/clearAlbum/resetEnvironment` are not called by the engine, and `markUploaded/markRejected/nextPending`
  are not under the Mutex at all). No cycle found. `cancelAndAwait` joins a job that finishes after its `NonCancellable` block, so the wipe cannot race
  an in-flight apply.
- Hilt: `@Singleton` engine with the `@Inject` secondary constructor and a `@SyncScope` qualifier; the primary constructor gives a clean test seam
  (dispatcher, clock, scope). `SupervisorJob + Dispatchers.Default`; `CancellationException` is consistently rethrown. `start` returns at once, terminal
  states reset on the next `start`.
- Streaming is real: `RequestBody.writeTo` opens the stream at write time and `use`s it; nothing is held in memory. Unknown length means a chunked request;
  OkHttp's `retryOnConnectionFailure` re-invokes `writeTo`, which re-opens the stream, so the body is replayable. Uvicorn/h11 and Starlette accept
  chunked multipart (no Content-Length requirement in `uploads.py`).
- Cleartext HTTP is allowed (`usesCleartextTraffic="true"`, `AndroidManifest.xml:18`), so the LAN backends work as before. Timeouts (10 s / 2 min per
  write op / 5 min read, no call limit) are adequate: the write timeout is per socket write, not per body; the read timeout covers the server's
  hashing/validation of up to 50 files.
- Error classification matches B1: 429 wait/retry, 503, 507, 500/other 5xx, other 4xx, unreadable 200 body, invalid environment URL, network.

## Test gaps

Tests assert behaviour (tracker rows, requests sent, state types) and use virtual time well; the suite passes. Gaps:

1. **`a received response is recorded even when stop arrives right then`** (`SyncEngineTest.kt:171`) calls `stop()` inside the handler and returns without
   suspending, so it would likely also pass if `withContext(NonCancellable)` were removed (the fake tracker never suspends). It does not pin the
   invariant. Make the fake DAO call `yield()`/`ensureActive()` so removing `NonCancellable` fails the test (suspected, not mutation-tested).
2. No test of real multipart encoding: a Cyrillic name, a name with `"`, CR/LF in a name (OkHttp `%22/%0D/%0A`), and chunked (unknown) length. Only
   `filename="a.jpg"` is asserted (`RetrofitBatchUploaderTest.kt`). Use `MockWebServer` (or write the `MultipartBody` into a `Buffer`) instead of a
   mocked `MemeApiService`: the whole Retrofit/OkHttp path (HttpException conversion, `retryAfter`, chunking, cancellation closing the socket) is only
   mocked.
3. `Retry-After`: only the integer form is tested; HTTP-date, "0", negative, huge (cap 300), blank and non-numeric are not.
4. Cancellation at each point is covered only for "hung upload", "waiting" and "right after start". Missing: stop during the `isReadable` pre-check, stop
   while applying, stop during the first `settings.album` read, and a cancel from `cancelAndAwait` while `Waiting`.
5. Transient vs permanent read failure (I1) has no test; `SecurityException`/null stream are not distinguished from `FileNotFoundException`.
6. No test for a huge batch/backlog (e.g. 10k pending with batch 50: loop count, memory of the `pending - sendable.toSet()` list ops) and none for 50 x
   20 MB streaming (the spec lists it as "not covered").
7. State transitions are asserted at the end, not as a sequence (no `state().toList()`/Turbine style check of Running -> Waiting -> Running ->
   Finished, and that a `Failed` resets to `Running` on restart).
8. `SyncModule` (URL normalisation, per-URL Retrofit cache, invalid URL) and `ContentResolverAlbumFileSource` are untested (needs Robolectric or a seam).
9. A scan racing a sync is tested only through the in-memory fake DAO, not the real Room transaction behaviour (A1's known device-only gap).

## Questions for the owner

1. A deterministic server failure (500 from one bad file) blocks every later batch. Should A3 bisect/skip, or is "lower the batch size" acceptable?
2. Should a file the phone cannot open ever be rejected permanently (I1), or should it return to PENDING when a later scan can read it?
3. Cyrillic album file names: must the inbox keep them? If yes, B1 needs the latin-1 -> UTF-8 re-decode (I2).
4. Is a default batch of 50 safe for the backend host (about 1 GB peak RAM at 50 x 20 MB, M7)? Consider capping by total bytes in the client (for
   example stop filling a batch at 100 MB) instead of only by count.
5. Foreground-only: is it acceptable that a sync dies when Android freezes the app in the background (M9), or should A4 keep the screen awake?
