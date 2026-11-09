package com.memebrowser.app.data.sync

import com.memebrowser.app.data.album.AlbumAccess
import com.memebrowser.app.data.album.SyncGate
import com.memebrowser.app.data.model.UploadResponse
import com.memebrowser.app.data.repository.AlbumFileSource
import com.memebrowser.app.data.repository.BatchUploader
import com.memebrowser.app.data.repository.UploadAttempt
import com.memebrowser.app.data.repository.UploadFile
import com.memebrowser.app.data.tracker.AlbumSelection
import com.memebrowser.app.data.tracker.TrackedFileEntity
import com.memebrowser.app.data.tracker.TrackerRepository
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.FileNotFoundException
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Qualifier
import javax.inject.Singleton
import kotlin.coroutines.cancellation.CancellationException

/** The engine-owned scope: survives screens and ViewModels, dies with the process (foreground-only sync). */
@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class SyncScope

/** What the engine reads from settings at run time (the album, and the batch size before every batch). */
interface SyncSettingsSource {
    suspend fun album(environmentId: String): AlbumSelection?
    suspend fun batchSize(): Int
}

class TrackerSyncSettings @Inject constructor(private val tracker: TrackerRepository) : SyncSettingsSource {
    override suspend fun album(environmentId: String): AlbumSelection? = tracker.album(environmentId).first()
    override suspend fun batchSize(): Int = tracker.batchSize().first()
}

/**
 * Uploads an environment's PENDING files in batches (A3 design spec). Per environment id: one run at a time, own state.
 *
 * Runs in an engine-owned scope, not in a screen or ViewModel scope: leaving the screen does not stop a sync, only
 * [stop] / [cancelAndAwait] (or process death) does. A restart finds unsent files still PENDING, nothing is lost.
 *
 * Invariants:
 *  - A file becomes UPLOADED only through an explicit `accepted` / `duplicate` entry for it in a 200 response.
 *  - The tracker's Mutex is never held across network I/O (the engine only calls short repository methods).
 *  - Tracker writes of a received response run in [NonCancellable], so a stop cannot lose what the server accepted.
 *  - [nextPending] is re-queried for every batch, so files a scan adds while the run is going join the same run.
 *  - Every state change goes through [publish] under [lock]; once a stop was requested a run can only end as Stopped.
 */
@Singleton
class SyncEngine internal constructor(
    private val tracker: TrackerRepository,
    private val settings: SyncSettingsSource,
    private val access: AlbumAccess,
    private val uploader: BatchUploader,
    private val files: AlbumFileSource,
    private val scope: CoroutineScope,
    private val io: CoroutineDispatcher,
    private val clock: () -> Long,
    private val outcomes: OutcomeStore = TrackerOutcomeStore(tracker),
) {
    @Inject
    constructor(
        tracker: TrackerRepository,
        settings: SyncSettingsSource,
        access: AlbumAccess,
        uploader: BatchUploader,
        files: AlbumFileSource,
        @SyncScope scope: CoroutineScope,
    ) : this(tracker, settings, access, uploader, files, scope, kotlinx.coroutines.Dispatchers.IO, System::currentTimeMillis)

    private val lock = Any()
    private val jobs = HashMap<String, Job>() // guarded by lock
    private val stopRequested = HashSet<String>() // environments whose current run was asked to stop; guarded by lock
    private val states = ConcurrentHashMap<String, MutableStateFlow<SyncState>>()
    private val pacers = ConcurrentHashMap<String, UploadPacer>()

    private fun stateOf(env: String) = states.computeIfAbsent(env) { MutableStateFlow(SyncState.Idle) }

    fun state(environmentId: String): StateFlow<SyncState> = stateOf(environmentId).asStateFlow()

    fun isRunning(environmentId: String): Boolean = stateOf(environmentId).value.isActive

    /**
     * Starts a run for [environmentId] and returns at once. False (a no-op) when that environment already runs.
     * No implicit scan: the pending files are whatever the last scan found; zero pending finishes with an empty summary.
     */
    fun start(environmentId: String): Boolean {
        val startedAt = clock()
        synchronized(lock) {
            if (jobs[environmentId]?.isCompleted == false) return false
            stopRequested -= environmentId
            stateOf(environmentId).value = SyncState.Running(startedAt, 0, 0, SyncSummary())
            val job = scope.launch(start = CoroutineStart.LAZY) { runEnvironment(environmentId, startedAt) }
            jobs[environmentId] = job
            // Safety net for a job that ended without a terminal state: cancelled before its body ran, or failed outside
            // the body's own handling. A real cancellation is Stopped, anything else Failed(UNEXPECTED).
            job.invokeOnCompletion { cause ->
                synchronized(lock) {
                    if (jobs[environmentId] !== job) return@synchronized
                    val s = stateOf(environmentId).value
                    if (!s.isActive) return@synchronized
                    val summary = summaryOf(s)
                    publish(
                        environmentId,
                        if (cause == null || cause is CancellationException) SyncState.Stopped(summary, clock())
                        else SyncState.Failed(SyncError.UNEXPECTED, cause.message ?: cause.javaClass.simpleName, summary, clock()),
                    )
                }
            }
            job.start()
        }
        return true
    }

    /** Asks the run to stop: the in-flight request is cancelled, its files stay PENDING. No run: nothing happens. */
    fun stop(environmentId: String) {
        val job = synchronized(lock) {
            val j = jobs[environmentId]?.takeIf { !it.isCompleted } ?: return
            stopRequested += environmentId
            val flow = stateOf(environmentId)
            flow.value = stoppingView(flow.value)
            j
        }
        job.cancel()
    }

    /** [stop] and wait until the run has ended (its terminal state is set). Returns at once when none runs. */
    suspend fun cancelAndAwait(environmentId: String) {
        val job = synchronized(lock) { jobs[environmentId]?.takeIf { !it.isCompleted } } ?: return
        stop(environmentId)
        job.join()
    }

    // ---- state -------------------------------------------------------------------------------------------

    private fun summaryOf(s: SyncState): SyncSummary = when (s) {
        is SyncState.Running -> s.summary
        is SyncState.Waiting -> s.summary
        is SyncState.Stopping -> s.summary
        is SyncState.Finished -> s.summary
        is SyncState.Stopped -> s.summary
        is SyncState.Failed -> s.summary
        SyncState.Idle -> SyncSummary()
    }

    private fun stoppingView(s: SyncState): SyncState = when (s) {
        is SyncState.Running -> SyncState.Stopping(s.summary)
        is SyncState.Waiting -> SyncState.Stopping(s.summary)
        else -> s
    }

    /**
     * The only way a run changes its state. Under [lock], so it is ordered against [stop]: after a stop request a
     * progress state becomes Stopping and a Finished/Failed result becomes Stopped (summary kept), deterministically.
     */
    private fun publish(env: String, s: SyncState) = synchronized(lock) {
        stateOf(env).value = if (env in stopRequested) {
            when (s) {
                is SyncState.Finished -> SyncState.Stopped(s.summary, clock())
                is SyncState.Failed -> SyncState.Stopped(s.summary, clock())
                else -> stoppingView(s)
            }
        } else s
    }

    // ---- the run -----------------------------------------------------------------------------------------

    private class Progress {
        var uploaded = 0
        var duplicates = 0
        var rejected = 0
        fun summary() = SyncSummary(uploaded, duplicates, rejected)
    }

    private class Run(val env: String, val album: AlbumSelection, val startedAt: Long, val p: Progress, val pacer: UploadPacer) {
        var batchNumber = 0
        var rateLimited = 0
        var stalls = 0
        var poisonStreak = 0
    }

    private suspend fun runEnvironment(env: String, startedAt: Long) {
        val progress = Progress()
        try {
            publish(env, runLoop(env, startedAt, progress))
        } catch (e: CancellationException) {
            if (currentCoroutineContext().isActive) {
                // Not our job being cancelled: a stray CancellationException from inside, which must not read as a stop.
                publish(env, failed(SyncError.UNEXPECTED, e.message ?: "Unexpected cancellation", progress))
            } else {
                publish(env, SyncState.Stopped(progress.summary(), clock()))
                throw e
            }
        } catch (t: Throwable) {
            publish(env, failed(SyncError.UNEXPECTED, t.message ?: t.javaClass.simpleName, progress))
        }
    }

    private fun failed(error: SyncError, message: String, p: Progress) =
        SyncState.Failed(error, message, p.summary(), clock())

    private suspend fun runLoop(env: String, startedAt: Long, p: Progress): SyncState {
        val album = settings.album(env) ?: return failed(SyncError.NO_ALBUM, "No album selected", p)
        if (!access.isGranted(album.treeUri)) return permissionLost(p)
        val run = Run(env, album, startedAt, p, pacers.getOrPut(env) { UploadPacer(clock) })
        while (true) {
            val size = settings.batchSize()
            val pending = tracker.nextPending(env, size)
            if (pending.isEmpty()) return SyncState.Finished(p.summary(), clock())
            // Wait first and then look at the pending files again, so a long wait never sends a stale batch.
            if (pace(run)) continue

            val check = withContext(io) { precheck(pending) }
            check.unreadable?.let { (file, e) -> return unreadable(run, file, e) }
            if (check.gone.isNotEmpty()) {
                if (!access.isGranted(album.treeUri)) return permissionLost(p)
                withContext(NonCancellable) {
                    check.gone.forEach { outcomes.markRejected(env, it.albumTreeUri, it.documentId, REASON_FILE_UNAVAILABLE) }
                }
                p.rejected += check.gone.size
            }
            val sendable = check.sendable
            if (sendable.isEmpty()) continue

            run.batchNumber++
            publish(env, SyncState.Running(startedAt, run.batchNumber, sendable.size, p.summary()))
            run.pacer.record()
            val terminal = when (val attempt = uploader.upload(env, sendable.map(::toUpload))) {
                is UploadAttempt.Response -> applyBatch(run, sendable, attempt.body)
                is UploadAttempt.HttpError -> when (attempt.code) {
                    429 -> rateLimitWait(run, attempt).also { run.batchNumber-- } // the same files come back from nextPending
                    500 -> if (sendable.size == 1) rejectPoison(run, sendable[0]) else isolate(run, sendable)
                    else -> terminalFor(run, attempt)
                }
                else -> terminalFor(run, attempt)
            }
            if (terminal != null) return terminal
        }
    }

    /** Waits when the pacer says the server's request budget is used up. True = it waited (callers re-check). */
    private suspend fun pace(run: Run): Boolean {
        val wait = run.pacer.waitMs()
        if (wait <= 0) return false
        publish(run.env, SyncState.Waiting(WaitReason.PACING, clock() + wait, 0, run.p.summary()))
        delay(wait)
        return true
    }

    /** 429 safety net: waits (Retry-After wins, 1..300 s, default 60 s) or fails after too many in a row. Null = go on. */
    private suspend fun rateLimitWait(run: Run, attempt: UploadAttempt.HttpError): SyncState.Failed? {
        run.rateLimited++
        if (run.rateLimited > MAX_CONSECUTIVE_429) {
            return failed(SyncError.RATE_LIMITED, "The server keeps answering 'too many requests'", run.p)
        }
        val waitMs = (attempt.retryAfterSeconds?.coerceIn(1, MAX_RETRY_AFTER_SECONDS) ?: DEFAULT_WAIT_SECONDS) * 1000
        publish(run.env, SyncState.Waiting(WaitReason.RATE_LIMITED, clock() + waitMs, run.rateLimited, run.p.summary()))
        delay(waitMs)
        return null
    }

    /**
     * A batch got HTTP 500, which the backend also answers when ONE file cannot be written. Send its files one at a
     * time, each once. A file that gets 500 alone is rejected `server_error` (see [rejectPoison]); any other failure
     * follows the normal policy. Null = the pass is over, the run goes on.
     */
    private suspend fun isolate(run: Run, batch: List<TrackedFileEntity>): SyncState? {
        for (file in batch) {
            while (true) {
                if (pace(run)) continue
                publish(run.env, SyncState.Running(run.startedAt, run.batchNumber, 1, run.p.summary()))
                run.pacer.record()
                val terminal = when (val attempt = uploader.upload(run.env, listOf(toUpload(file)))) {
                    is UploadAttempt.Response -> applyBatch(run, listOf(file), attempt.body)
                    is UploadAttempt.HttpError -> when (attempt.code) {
                        429 -> {
                            rateLimitWait(run, attempt)?.let { return it }
                            continue // same file again
                        }
                        500 -> rejectPoison(run, file)
                        else -> terminalFor(run, attempt)
                    }
                    else -> terminalFor(run, attempt)
                }
                if (terminal != null) return terminal
                break
            }
        }
        return null
    }

    /** A file the server fails on even when sent alone: rejected for good. Too many in a row means the server is broken. */
    private suspend fun rejectPoison(run: Run, file: TrackedFileEntity): SyncState? {
        withContext(NonCancellable) { outcomes.markRejected(run.env, file.albumTreeUri, file.documentId, REASON_SERVER_ERROR) }
        run.p.rejected++
        run.poisonStreak++
        return if (run.poisonStreak >= MAX_POISON_STREAK) {
            failed(SyncError.SERVER, "$MAX_POISON_STREAK files in a row failed with a server error; the server looks broken", run.p)
        } else null
    }

    private fun toUpload(f: TrackedFileEntity) = UploadFile(f.albumTreeUri, f.documentId, f.displayName, f.mimeType)

    private fun permissionLost(p: Progress) =
        failed(SyncError.PERMISSION_LOST, "Access to the album was lost; pick the album again", p)

    private fun unreadable(run: Run, file: TrackedFileEntity, e: Exception): SyncState.Failed =
        if (!access.isGranted(run.album.treeUri)) permissionLost(run.p)
        else failed(
            SyncError.FILE_READ_ERROR,
            "Cannot read ${file.displayName}: ${e.message ?: e.javaClass.simpleName}. The files stay pending; try again",
            run.p,
        )

    /** Failures that end the run. Files stay pending. */
    private fun terminalFor(run: Run, a: UploadAttempt): SyncState.Failed = when (a) {
        is UploadAttempt.Response -> failed(SyncError.UNEXPECTED, "Unexpected response", run.p)
        is UploadAttempt.NetworkError -> failed(SyncError.NETWORK, a.message ?: "Network error", run.p)
        is UploadAttempt.Unexpected ->
            if (!access.isGranted(run.album.treeUri)) permissionLost(run.p)
            else failed(SyncError.UNEXPECTED, a.message ?: "Unexpected error", run.p)
        is UploadAttempt.AccessDenied ->
            if (!access.isGranted(run.album.treeUri)) permissionLost(run.p)
            else failed(SyncError.FILE_READ_ERROR, "Access to a file was denied: ${a.message ?: "no details"}", run.p)
        is UploadAttempt.ReadFailed ->
            if (!access.isGranted(run.album.treeUri)) permissionLost(run.p)
            else failed(SyncError.FILE_READ_ERROR, "Cannot read a file: ${a.message ?: "no details"}", run.p)
        is UploadAttempt.HttpError -> when (a.code) {
            429 -> failed(SyncError.RATE_LIMITED, "The server keeps answering 'too many requests'", run.p)
            503 -> failed(
                SyncError.INBOX_FULL_OR_UNWRITABLE,
                a.detail ?: "The server inbox is full or not writable; run ingestion or fix the server",
                run.p,
            )
            507 -> failed(SyncError.DISK_LOW, a.detail ?: "The server is low on disk space", run.p)
            in 500..599 -> failed(SyncError.SERVER, a.detail ?: "Server error ${a.code}", run.p)
            else -> failed(SyncError.UNEXPECTED, "HTTP ${a.code}: ${a.detail ?: "no details"}", run.p)
        }
    }

    private class PreCheck(
        val sendable: List<TrackedFileEntity>,
        val gone: List<TrackedFileEntity>,
        val unreadable: Pair<TrackedFileEntity, Exception>?,
    )

    /**
     * Opens each file once. Only a [FileNotFoundException] means the document no longer exists (rejected for good); any other
     * failure (provider hiccup, cloud placeholder, unmounted volume, denied access) is transient and fails the run instead.
     */
    private fun CoroutineScope.precheck(pending: List<TrackedFileEntity>): PreCheck {
        val sendable = ArrayList<TrackedFileEntity>()
        val gone = ArrayList<TrackedFileEntity>()
        for (file in pending) {
            ensureActive()
            try {
                files.open(file.albumTreeUri, file.documentId).close()
                sendable += file
            } catch (e: CancellationException) {
                throw e
            } catch (_: FileNotFoundException) {
                gone += file
            } catch (e: Exception) {
                return PreCheck(emptyList(), emptyList(), file to e)
            }
        }
        return PreCheck(sendable, gone, null)
    }

    /** Maps the response onto [batch] by `index` and records it. Null = go on; Failed = the run ends. */
    private suspend fun applyBatch(run: Run, batch: List<TrackedFileEntity>, body: UploadResponse): SyncState.Failed? {
        val mapped = mapResponse(body, batch.size)
        if (mapped !is MappedResponse.Ok) return failed(SyncError.SERVER, BACKEND_TOO_OLD_MESSAGE, run.p)
        run.rateLimited = 0
        run.poisonStreak = 0
        withContext(NonCancellable) {
            mapped.uploaded.groupBy({ batch[it].albumTreeUri }, { batch[it].documentId }).forEach { (tree, ids) ->
                outcomes.markUploaded(run.env, tree, ids)
            }
            mapped.rejected.forEach { (pos, reason) ->
                outcomes.markRejected(run.env, batch[pos].albumTreeUri, batch[pos].documentId, reason)
            }
            run.p.duplicates += mapped.duplicates.size
            run.p.uploaded += mapped.uploaded.size - mapped.duplicates.size
            run.p.rejected += mapped.rejected.size
        }
        run.stalls = if (mapped.settled == 0) run.stalls + 1 else 0
        return if (run.stalls >= MAX_STALLS) {
            failed(SyncError.UNEXPECTED, "The server did not report a result for ${batch.size} files; they stay pending", run.p)
        } else null
    }

    companion object {
        const val REASON_FILE_UNAVAILABLE = "file_unavailable"
        const val REASON_SERVER_ERROR = "server_error"
        const val BACKEND_TOO_OLD_MESSAGE = "backend too old: update the backend before syncing"
        const val DEFAULT_WAIT_SECONDS = 60L
        const val MAX_RETRY_AFTER_SECONDS = 300L
        const val MAX_CONSECUTIVE_429 = 10
        const val MAX_STALLS = 2
        const val MAX_POISON_STREAK = 3
    }
}

/** The real [SyncGate]: an album change stops the environment's sync and waits for it before wiping. */
class EngineSyncGate(private val engine: SyncEngine) : SyncGate {
    override suspend fun cancelAndAwait(environmentId: String) = engine.cancelAndAwait(environmentId)
}
