package com.memebrowser.app.data.tracker

import androidx.room.Entity
import androidx.room.Index

/** A file as listed by the album scan (the SAF listing that produces it is A2). */
data class ScannedFile(
    val documentId: String,
    val displayName: String,
    val mimeType: String,
    val size: Long,
    val lastModified: Long,
)

enum class TrackedFileState { PENDING, UPLOADED, REJECTED }

@Entity(
    tableName = "tracked_files",
    primaryKeys = ["environmentId", "albumTreeUri", "documentId"],
    indices = [Index(value = ["environmentId", "state"])],
)
data class TrackedFileEntity(
    val environmentId: String,
    val albumTreeUri: String,
    val documentId: String,
    val displayName: String,
    val mimeType: String,
    val sizeBytes: Long,
    val lastModified: Long,
    val state: TrackedFileState,
    val rejectionReason: String?,
    val uploadedAt: Long?,
    val updatedAt: Long,
    val presentInAlbum: Boolean,
    val lastSeenScan: Long,
)

/** X = uploaded, Y = supported, R = rejected, P = pending. See the A1 design for exact definitions. */
data class TrackerCounts(
    val uploaded: Int,
    val supported: Int,
    val rejected: Int,
    val pending: Int,
) {
    companion object {
        val EMPTY = TrackerCounts(0, 0, 0, 0)
    }
}
