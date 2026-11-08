package com.memebrowser.app.data.tracker

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Transaction
import kotlinx.coroutines.flow.Flow

@Dao
abstract class TrackerDao {

    @Query(
        "SELECT * FROM tracked_files WHERE environmentId = :environmentId AND albumTreeUri = :albumTreeUri " +
            "AND documentId IN (:documentIds)"
    )
    abstract suspend fun findByIds(environmentId: String, albumTreeUri: String, documentIds: List<String>): List<TrackedFileEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    abstract suspend fun upsertAll(rows: List<TrackedFileEntity>)

    /** Marks every row of the environment that the scan [scanId] did not stamp as absent. */
    @Query(
        "UPDATE tracked_files SET presentInAlbum = 0 " +
            "WHERE environmentId = :environmentId AND lastSeenScan != :scanId AND presentInAlbum = 1"
    )
    abstract suspend fun markMissing(environmentId: String, scanId: Long)

    @Query(
        "SELECT " +
            "COALESCE(SUM(CASE WHEN state = ${TrackerSql.UPLOADED} THEN 1 ELSE 0 END), 0) AS uploaded, " +
            // COALESCE keeps a REJECTED row with a NULL reason in Y (NOT (x AND NULL) would be NULL, i.e. excluded).
            "COALESCE(SUM(CASE WHEN presentInAlbum = 1 AND NOT (state = ${TrackerSql.REJECTED} " +
            "AND COALESCE(rejectionReason, '') = ${TrackerSql.UNSUPPORTED_TYPE}) " +
            "THEN 1 ELSE 0 END), 0) AS supported, " +
            "COALESCE(SUM(CASE WHEN presentInAlbum = 1 AND state = ${TrackerSql.REJECTED} THEN 1 ELSE 0 END), 0) AS rejected, " +
            "COALESCE(SUM(CASE WHEN presentInAlbum = 1 AND state = ${TrackerSql.PENDING} THEN 1 ELSE 0 END), 0) AS pending " +
            "FROM tracked_files WHERE environmentId = :environmentId"
    )
    abstract fun observeCounts(environmentId: String): Flow<TrackerCounts>

    @Query(
        "SELECT * FROM tracked_files WHERE environmentId = :environmentId AND presentInAlbum = 1 AND state = ${TrackerSql.PENDING} " +
            "ORDER BY lastModified DESC, documentId ASC LIMIT :limit"
    )
    abstract suspend fun nextPending(environmentId: String, limit: Int): List<TrackedFileEntity>

    @Query(
        "UPDATE tracked_files SET state = ${TrackerSql.UPLOADED}, rejectionReason = NULL, " +
            "uploadedAt = COALESCE(uploadedAt, :at), updatedAt = :at " +
            "WHERE environmentId = :environmentId AND albumTreeUri = :albumTreeUri AND documentId IN (:documentIds)"
    )
    abstract suspend fun markUploaded(environmentId: String, albumTreeUri: String, documentIds: List<String>, at: Long)

    /** Never demotes an UPLOADED row (a late answer must not decrement X); a no-op for it. */
    @Query(
        "UPDATE tracked_files SET state = ${TrackerSql.REJECTED}, rejectionReason = :reason, updatedAt = :at " +
            "WHERE environmentId = :environmentId AND albumTreeUri = :albumTreeUri AND documentId = :documentId " +
            "AND state != ${TrackerSql.UPLOADED}"
    )
    abstract suspend fun markRejected(environmentId: String, albumTreeUri: String, documentId: String, reason: String, at: Long)

    @Query(
        "SELECT * FROM tracked_files WHERE environmentId = :environmentId AND presentInAlbum = 1 AND state = ${TrackerSql.REJECTED} " +
            "ORDER BY lastModified DESC, documentId ASC"
    )
    abstract fun observeRejected(environmentId: String): Flow<List<TrackedFileEntity>>

    /** Highest scan id stamped in the environment, null when it has no rows. */
    @Query("SELECT MAX(lastSeenScan) FROM tracked_files WHERE environmentId = :environmentId")
    abstract suspend fun maxScanId(environmentId: String): Long?

    @Query("DELETE FROM tracked_files WHERE environmentId = :environmentId")
    abstract suspend fun deleteEnvironment(environmentId: String)

    /** Reconciles one chunk of a scan (at most [SCAN_CHUNK_SIZE] files) in one transaction. */
    @Transaction
    open suspend fun applyScanChunk(
        environmentId: String,
        albumTreeUri: String,
        files: List<ScannedFile>,
        scanId: Long,
        now: Long,
    ) {
        val existing = findByIds(environmentId, albumTreeUri, files.map { it.documentId })
        upsertAll(ScanReconciler.reconcile(environmentId, albumTreeUri, existing, files, scanId, now))
    }

    /** Marks many files uploaded in one transaction, in id chunks below SQLite's variable limit. */
    @Transaction
    open suspend fun markUploadedAll(environmentId: String, albumTreeUri: String, documentIds: List<String>, at: Long) {
        documentIds.chunked(SCAN_CHUNK_SIZE).forEach { markUploaded(environmentId, albumTreeUri, it, at) }
    }

    companion object {
        const val SCAN_CHUNK_SIZE = 500
    }
}
