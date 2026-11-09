package com.memebrowser.app.data.tracker

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Per-environment upload tracker. Rules are in the A1 design spec.
 *
 * One [Mutex] serialises every operation that rewrites presence flags or wipes rows: [syncScan],
 * [resetEnvironment], [changeAlbum] and [clearAlbum]. Overlapping scans would otherwise flag real files absent
 * (scan 2's `markMissing` sees rows that scan 1 re-stamped), and a wipe could be undone by an in-flight scan.
 * The mutex is not reentrant: never call one of these from inside another.
 *
 * A2 contract: cancel the running sync job (scan and uploads) BEFORE calling [changeAlbum] / [clearAlbum] /
 * [resetEnvironment]. The mutex only orders the calls; a scan that merely waits for the lock would still run
 * against the old album afterwards and re-insert its rows.
 */
@Singleton
class TrackerRepository @Inject constructor(
    private val dao: TrackerDao,
    private val settings: TrackerSettingsStore,
) {
    private val mutex = Mutex()

    fun observeCounts(environmentId: String): Flow<TrackerCounts> = dao.observeCounts(environmentId)

    fun rejectedFiles(environmentId: String): Flow<List<TrackedFileEntity>> = dao.observeRejected(environmentId)

    /**
     * Reconciles the tracker with a fresh, SUCCESSFUL scan: new files become PENDING (or locally REJECTED),
     * existing rows keep their state (a REPLACE rewrites the row, but with the old state copied), and rows the
     * scan did not list are marked absent. Chunked, one transaction per chunk.
     *
     * A2 contract: never call this with the result of a failed listing (revoked grant, provider error). An
     * empty [files] list is rejected with [IllegalArgumentException] unless [allowEmpty] is true, because an
     * empty scan marks the whole environment absent (Y becomes 0). Pass `allowEmpty = true` only when the
     * listing succeeded and the album really is empty.
     *
     * [scanId] is for tests; by default it is `max(now, highest stored scan id + 1)`, computed under the lock,
     * so ids are strictly increasing and two scans in the same millisecond cannot collide.
     */
    suspend fun syncScan(
        environmentId: String,
        albumTreeUri: String,
        files: List<ScannedFile>,
        allowEmpty: Boolean = false,
        scanId: Long? = null,
    ) {
        require(files.isNotEmpty() || allowEmpty) {
            "syncScan called with an empty listing; pass allowEmpty = true only for a genuinely empty album, never for a failed listing"
        }
        mutex.withLock {
            val id = scanId ?: maxOf(System.currentTimeMillis(), (dao.maxScanId(environmentId) ?: 0L) + 1)
            files.chunked(TrackerDao.SCAN_CHUNK_SIZE).forEach { chunk ->
                dao.applyScanChunk(environmentId, albumTreeUri, chunk, id, now = System.currentTimeMillis())
            }
            dao.markMissing(environmentId, id)
        }
    }

    suspend fun nextPending(environmentId: String, limit: Int): List<TrackedFileEntity> =
        dao.nextPending(environmentId, limit)

    suspend fun markUploaded(
        environmentId: String,
        albumTreeUri: String,
        documentIds: List<String>,
        at: Long = System.currentTimeMillis(),
    ) {
        if (documentIds.isNotEmpty()) dao.markUploadedAll(environmentId, albumTreeUri, documentIds, at)
    }

    /** Marks a file rejected by the backend. A no-op for an UPLOADED row (an upload is never demoted). */
    suspend fun markRejected(
        environmentId: String,
        albumTreeUri: String,
        documentId: String,
        reason: String,
        at: Long = System.currentTimeMillis(),
    ) = dao.markRejected(environmentId, albumTreeUri, documentId, reason, at)

    /**
     * A4: puts every file rejected with `server_error` back to PENDING (the next run sends it again); returns how many.
     * Other rejections are permanent and untouched. Under the same mutex as the other writes.
     */
    suspend fun retryServerErrors(environmentId: String, at: Long = System.currentTimeMillis()): Int =
        mutex.withLock { dao.retryRejectedByReason(environmentId, RejectionReasons.SERVER_ERROR, at) }

    /** A4: the same for one file; false when it is not a `server_error` rejection (nothing changed). */
    suspend fun retryServerError(
        environmentId: String,
        albumTreeUri: String,
        documentId: String,
        at: Long = System.currentTimeMillis(),
    ): Boolean = mutex.withLock {
        dao.retryRejectedFile(environmentId, albumTreeUri, documentId, RejectionReasons.SERVER_ERROR, at) > 0
    }

    /** Deletes only this environment's rows. The album setting is untouched; see [changeAlbum] / [clearAlbum]. */
    suspend fun resetEnvironment(environmentId: String) = mutex.withLock { dao.deleteEnvironment(environmentId) }

    /**
     * Switches the environment to a new album: wipes its tracker rows FIRST, then stores the new album. A crash
     * in between leaves "old album, empty tracker", which just rescans; the reverse order could leave "new
     * album, stale rows of the old one". Cancel the sync job before calling (see the class KDoc).
     */
    suspend fun changeAlbum(environmentId: String, treeUri: String, displayName: String) = mutex.withLock {
        dao.deleteEnvironment(environmentId)
        settings.setAlbum(environmentId, treeUri, displayName)
    }

    /** Wipes the environment's tracker rows, then removes its album setting (same order and rules as [changeAlbum]). */
    suspend fun clearAlbum(environmentId: String) = mutex.withLock {
        dao.deleteEnvironment(environmentId)
        settings.clearAlbum(environmentId)
    }

    fun album(environmentId: String): Flow<AlbumSelection?> = settings.album(environmentId)

    /**
     * Stores the first album of an environment WITHOUT touching tracker rows. To replace an existing album use
     * [changeAlbum], which also wipes.
     */
    suspend fun setAlbum(environmentId: String, treeUri: String, displayName: String) =
        settings.setAlbum(environmentId, treeUri, displayName)

    fun batchSize(): Flow<Int> = settings.batchSize

    suspend fun setBatchSize(size: Int) = settings.setBatchSize(size)
}
