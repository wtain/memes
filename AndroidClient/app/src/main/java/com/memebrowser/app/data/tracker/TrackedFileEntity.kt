package com.memebrowser.app.data.tracker

import androidx.room.Entity
import androidx.room.Index

/**
 * Persisted by name and referenced by name in the SQL of [TrackerDao] (see [TrackerSql]); renaming an entry
 * needs a Room migration, and TrackerSqlConstantsTest fails if the names and the SQL constants drift apart.
 */
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
