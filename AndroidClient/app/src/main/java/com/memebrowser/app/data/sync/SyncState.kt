package com.memebrowser.app.data.sync

/** What a run has done so far (or in total, in a terminal state). [uploaded] = accepted, [duplicates] = already on the server. */
data class SyncSummary(val uploaded: Int = 0, val duplicates: Int = 0, val rejected: Int = 0) {
    /** Files the server or the phone settled in this run (X goes up by [uploaded] + [duplicates]). */
    val settled: Int get() = uploaded + duplicates + rejected
}

enum class WaitReason { RATE_LIMITED }

enum class SyncError {
    /** No answer from the backend (offline, timeout, connection lost). Files stay pending. */
    NETWORK,

    /** 5xx other than 503/507 (for example 500: writing into the inbox failed). Files stay pending. */
    SERVER,

    /** 503: the inbox is full, not writable or the duplicate lookup failed; ingestion must be run or the server fixed. */
    INBOX_FULL_OR_UNWRITABLE,

    /** 507: the inbox volume is nearly full. */
    DISK_LOW,

    /** Too many 429 answers in a row. */
    RATE_LIMITED,

    /** Another 4xx, an unreadable 200 body, a server that makes no progress, or a bug. */
    UNEXPECTED,

    /** The environment has no album selected. */
    NO_ALBUM,

    /** The persisted grant for the album is gone: the user must pick the album again. */
    PERMISSION_LOST,
}

sealed interface SyncState {
    /** No run since the process started. */
    data object Idle : SyncState

    /** [batchNumber] counts from 1; [currentBatchSize] is the number of files in the batch being sent. */
    data class Running(
        val startedAt: Long,
        val batchNumber: Int,
        val currentBatchSize: Int,
        val summary: SyncSummary,
    ) : SyncState

    /** HTTP 429: the same batch is retried at [resumeAt] (epoch ms). [attempt] counts consecutive 429s. */
    data class Waiting(
        val reason: WaitReason,
        val resumeAt: Long,
        val attempt: Int,
        val summary: SyncSummary,
    ) : SyncState

    /** A stop was requested; the run is ending. */
    data class Stopping(val summary: SyncSummary) : SyncState

    /** Nothing pending is left. */
    data class Finished(val summary: SyncSummary, val at: Long) : SyncState

    /** The user (or an album change) stopped the run. Unsent files are still pending. */
    data class Stopped(val summary: SyncSummary, val at: Long) : SyncState

    data class Failed(val error: SyncError, val message: String, val summary: SyncSummary, val at: Long) : SyncState
}

/** True while a run owns the environment ([SyncState.Running], [SyncState.Waiting], [SyncState.Stopping]). */
val SyncState.isActive: Boolean
    get() = this is SyncState.Running || this is SyncState.Waiting || this is SyncState.Stopping
