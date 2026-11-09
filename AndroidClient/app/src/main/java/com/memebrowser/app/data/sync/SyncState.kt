package com.memebrowser.app.data.sync

/** What a run has done so far (or in total, in a terminal state). [uploaded] = accepted, [duplicates] = already on the server. */
data class SyncSummary(val uploaded: Int = 0, val duplicates: Int = 0, val rejected: Int = 0) {
    /** Files the server or the phone settled in this run (X goes up by [uploaded] + [duplicates]). */
    val settled: Int get() = uploaded + duplicates + rejected
}

enum class WaitReason {
    /** The server answered HTTP 429; the same batch is retried after the wait. */
    RATE_LIMITED,

    /**
     * The app itself waits so it never exceeds the server's upload limit (see [UploadPacer]); nothing went wrong.
     * [SyncState.Waiting.attempt] is 0 for this reason.
     */
    PACING,
}

enum class SyncError {
    /** No answer from the backend (offline, timeout, connection lost). Files stay pending. */
    NETWORK,

    /**
     * 5xx other than 503/507, three files in a row that each failed with 500 when sent alone, or a backend too old to
     * report the per-file `index` (the message says so). Files that were not themselves rejected stay pending.
     */
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

    /**
     * A file of the album could not be read although it still exists (provider hiccup, cloud-only placeholder, unmounted
     * volume, denied access with the grant intact). Nothing is rejected; the files stay pending and a later run retries.
     */
    FILE_READ_ERROR,
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

    /** Waiting until [resumeAt] (epoch ms), then the run continues. [attempt] counts consecutive 429s (0 for PACING). */
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
