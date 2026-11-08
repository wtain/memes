package com.memebrowser.app.data.tracker

import kotlinx.coroutines.flow.Flow
import javax.inject.Inject
import javax.inject.Singleton

/** Per-environment upload tracker. Rules are in the A1 design spec. */
@Singleton
class TrackerRepository @Inject constructor(
    private val dao: TrackerDao,
    private val settings: TrackerSettingsStore,
) {
    fun observeCounts(environmentId: String): Flow<TrackerCounts> = dao.observeCounts(environmentId)

    fun rejectedFiles(environmentId: String): Flow<List<TrackedFileEntity>> = dao.observeRejected(environmentId)

    /**
     * Reconciles the tracker with a fresh scan: new files become PENDING (or locally REJECTED), existing rows
     * keep their state, and rows the scan did not list are marked absent. Chunked, one transaction per chunk.
     */
    suspend fun syncScan(
        environmentId: String,
        albumTreeUri: String,
        files: List<ScannedFile>,
        scanId: Long = System.currentTimeMillis(),
    ) {
        files.chunked(TrackerDao.SCAN_CHUNK_SIZE).forEach { chunk ->
            dao.applyScanChunk(environmentId, albumTreeUri, chunk, scanId, now = scanId)
        }
        dao.markMissing(environmentId, scanId)
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

    suspend fun markRejected(
        environmentId: String,
        albumTreeUri: String,
        documentId: String,
        reason: String,
        at: Long = System.currentTimeMillis(),
    ) = dao.markRejected(environmentId, albumTreeUri, documentId, reason, at)

    /** Deletes only this environment's rows. Clearing the album setting is the caller's (A2) job. */
    suspend fun resetEnvironment(environmentId: String) = dao.deleteEnvironment(environmentId)

    fun album(environmentId: String): Flow<AlbumSelection?> = settings.album(environmentId)

    suspend fun setAlbum(environmentId: String, treeUri: String, displayName: String) =
        settings.setAlbum(environmentId, treeUri, displayName)

    fun batchSize(): Flow<Int> = settings.batchSize

    suspend fun setBatchSize(size: Int) = settings.setBatchSize(size)
}
