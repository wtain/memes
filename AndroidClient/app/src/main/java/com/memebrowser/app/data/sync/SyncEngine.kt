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
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
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
 */
@Singleton
class SyncEngine(
    private val tracker: TrackerRepository,
    private val settings: SyncSettingsSource,
    private val access: AlbumAccess,
    private val uploader: BatchUploader,
    private val files: AlbumFileSource,
    private val scope: CoroutineScope,
    private val io: CoroutineDispatcher,
    private val clock: () -> Long,
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
    private val states = ConcurrentHashMap<String, MutableStateFlow<SyncState>>()

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
            stateOf(environmentId).value = SyncState.Running(startedAt, 0, 0, SyncSummary())
            val job = scope.launch(start = CoroutineStart.LAZY) { runEnvironment(environmentId, startedAt) }
            jobs[environmentId] = job
            // A job cancelled before its body ever ran (stop right after start) sets no terminal state itself.
            job.invokeOnCompletion {
                synchronized(lock) { if (jobs[environmentId] === job) stateOf(environmentId).update { s ->
                    when (s) {
                        is SyncState.Running -> SyncState.Stopped(s.summary, clock())
                        is SyncState.Waiting -> SyncState.Stopped(s.summary, clock())
                        is SyncState.Stopping -> SyncState.Stopped(s.summary, clock())
                        else -> s
                    }
                } }
            }
            job.start()
        }
        return true
    }

    /** Asks the run to stop: the in-flight request is cancelled, its files stay PENDING. No run: nothing happens. */
    fun stop(environmentId: String) {
        val job = synchronized(lock) { jobs[environmentId]?.takeIf { !it.isCompleted } } ?: return
        stateOf(environmentId).update {
            when (it) {
                is SyncState.Running -> SyncState.Stopping(it.summary)
                is SyncState.Waiting -> SyncState.Stopping(it.summary)
                else -> it
            }
        }
        job.cancel()
    }

    /** [stop] and wait until the run has ended (its terminal state is set). Returns at once when none runs. */
    suspend fun cancelAndAwait(environmentId: String) {
        val job = synchronized(lock) { jobs[environmentId]?.takeIf { !it.isCompleted } } ?: return
        stop(environmentId)
        job.join()
    }

    // ---- the run -----------------------------------------------------------------------------------------

    private class Progress {
        var uploaded = 0
        var duplicates = 0
        var rejected = 0
        fun summary() = SyncSummary(uploaded, duplicates, rejected)
    }

    private suspend fun runEnvironment(env: String, startedAt: Long) {
        val progress = Progress()
        val state = stateOf(env)
        try {
            state.value = runLoop(env, startedAt, progress)
        } catch (e: CancellationException) {
            state.value = SyncState.Stopped(progress.summary(), clock())
            throw e
        } catch (e: Exception) {
            state.value = failed(SyncError.UNEXPECTED, e.message ?: e.javaClass.simpleName, progress)
        }
    }

    private fun failed(error: SyncError, message: String, p: Progress) =
        SyncState.Failed(error, message, p.summary(), clock())

    private suspend fun runLoop(env: String, startedAt: Long, p: Progress): SyncState {
        val state = stateOf(env)
        val album = settings.album(env) ?: return failed(SyncError.NO_ALBUM, "No album selected", p)
        if (!access.isGranted(album.treeUri)) {
            return failed(SyncError.PERMISSION_LOST, "Access to the album was lost; pick the album again", p)
        }
        var batchNumber = 0
        var rateLimited = 0
        var stalls = 0
        while (true) {
            val size = settings.batchSize()
            val pending = tracker.nextPending(env, size)
            if (pending.isEmpty()) return SyncState.Finished(p.summary(), clock())

            val sendable = withContext(io) { pending.filter { isReadable(it) } }
            val unreadable = pending - sendable.toSet()
            if (unreadable.isNotEmpty()) {
                if (!access.isGranted(album.treeUri)) {
                    return failed(SyncError.PERMISSION_LOST, "Access to the album was lost; pick the album again", p)
                }
                withContext(NonCancellable) {
                    unreadable.forEach {
                        tracker.markRejected(env, it.albumTreeUri, it.documentId, REASON_FILE_UNAVAILABLE)
                    }
                }
                p.rejected += unreadable.size
            }
            if (sendable.isEmpty()) continue

            batchNumber++
            state.value = SyncState.Running(startedAt, batchNumber, sendable.size, p.summary())
            val request = sendable.map { UploadFile(it.albumTreeUri, it.documentId, it.displayName, it.mimeType) }

            when (val attempt = uploader.upload(env, request)) {
                is UploadAttempt.Response -> {
                    rateLimited = 0
                    val settled = withContext(NonCancellable) { applyResponse(env, sendable, attempt.body, p) }
                    stalls = if (settled == 0) stalls + 1 else 0
                    if (stalls >= MAX_STALLS) {
                        return failed(
                            SyncError.UNEXPECTED,
                            "The server did not report a result for ${sendable.size} files; they stay pending",
                            p,
                        )
                    }
                }
                is UploadAttempt.NetworkError -> return failed(SyncError.NETWORK, attempt.message ?: "Network error", p)
                is UploadAttempt.Unexpected -> return failed(SyncError.UNEXPECTED, attempt.message ?: "Unexpected error", p)
                is UploadAttempt.HttpError -> when (attempt.code) {
                    429 -> {
                        rateLimited++
                        if (rateLimited > MAX_CONSECUTIVE_429) {
                            return failed(SyncError.RATE_LIMITED, "The server keeps answering 'too many requests'", p)
                        }
                        val waitMs = (attempt.retryAfterSeconds?.coerceIn(1, MAX_RETRY_AFTER_SECONDS) ?: DEFAULT_WAIT_SECONDS) * 1000
                        state.value = SyncState.Waiting(WaitReason.RATE_LIMITED, clock() + waitMs, rateLimited, p.summary())
                        delay(waitMs)
                        // The same files are fetched again by nextPending (nothing was marked).
                        batchNumber--
                    }
                    503 -> return failed(
                        SyncError.INBOX_FULL_OR_UNWRITABLE,
                        attempt.detail ?: "The server inbox is full or not writable; run ingestion or fix the server",
                        p,
                    )
                    507 -> return failed(SyncError.DISK_LOW, attempt.detail ?: "The server is low on disk space", p)
                    in 500..599 -> return failed(SyncError.SERVER, attempt.detail ?: "Server error ${attempt.code}", p)
                    else -> return failed(SyncError.UNEXPECTED, "HTTP ${attempt.code}: ${attempt.detail ?: "no details"}", p)
                }
            }
        }
    }

    private fun isReadable(file: TrackedFileEntity): Boolean = try {
        files.open(file.albumTreeUri, file.documentId).close()
        true
    } catch (e: CancellationException) {
        throw e
    } catch (_: Exception) {
        false
    }

    /** Maps the response onto [batch] by `index` and records it. Returns how many files it settled. */
    private suspend fun applyResponse(env: String, batch: List<TrackedFileEntity>, body: UploadResponse, p: Progress): Int {
        val done = HashSet<Int>() // positions in batch
        val uploadedAt = ArrayList<Int>()
        val duplicateAt = HashSet<Int>()
        val usedNames = HashSet<Int>()
        fun position(index: Int?, name: String): Int? {
            if (index != null) return index.takeIf { it in batch.indices }
            // Old backend without `index`: first still unmatched file with this name, in order.
            return batch.indices.firstOrNull { it !in usedNames && batch[it].displayName == name }?.also { usedNames += it }
        }
        for (u in body.uploaded) {
            val status = u.status
            if (status != STATUS_ACCEPTED && status != STATUS_DUPLICATE) continue
            val pos = position(u.index, u.original_filename) ?: continue
            if (!done.add(pos)) continue
            uploadedAt += pos
            if (status == STATUS_DUPLICATE) duplicateAt += pos
        }
        val rejectedAt = ArrayList<Pair<Int, String>>()
        for (f in body.failed) {
            val pos = position(f.index, f.original_filename) ?: continue
            if (!done.add(pos)) continue
            rejectedAt += pos to f.reason.take(MAX_REASON_LENGTH)
        }
        uploadedAt.groupBy({ batch[it].albumTreeUri }, { batch[it].documentId }).forEach { (tree, ids) ->
            tracker.markUploaded(env, tree, ids)
        }
        rejectedAt.forEach { (pos, reason) -> tracker.markRejected(env, batch[pos].albumTreeUri, batch[pos].documentId, reason) }
        p.duplicates += duplicateAt.size
        p.uploaded += uploadedAt.size - duplicateAt.size
        p.rejected += rejectedAt.size
        return done.size
    }

    companion object {
        const val REASON_FILE_UNAVAILABLE = "file_unavailable"
        const val STATUS_ACCEPTED = "accepted"
        const val STATUS_DUPLICATE = "duplicate"
        const val DEFAULT_WAIT_SECONDS = 60L
        const val MAX_RETRY_AFTER_SECONDS = 300L
        const val MAX_CONSECUTIVE_429 = 10
        const val MAX_STALLS = 2
        private const val MAX_REASON_LENGTH = 300
    }
}

/** The real [SyncGate]: an album change stops the environment's sync and waits for it before wiping. */
class EngineSyncGate(private val engine: SyncEngine) : SyncGate {
    override suspend fun cancelAndAwait(environmentId: String) = engine.cancelAndAwait(environmentId)
}
