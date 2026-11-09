package com.memebrowser.app.data.tracker

/** Pure reconciliation of one chunk of a scan against the rows that already exist for it. */
object ScanReconciler {

    /**
     * Returns the rows to upsert. Existing rows keep state, reason and uploadedAt (D2); new rows are
     * PENDING or locally REJECTED. Every returned row is stamped present with [scanId].
     */
    fun reconcile(
        environmentId: String,
        albumTreeUri: String,
        existing: List<TrackedFileEntity>,
        scanned: List<ScannedFile>,
        scanId: Long,
        now: Long,
    ): List<TrackedFileEntity> {
        val byId = existing.associateBy { it.documentId }
        return scanned.map { file ->
            val old = byId[file.documentId]
            if (old != null) {
                old.copy(
                    displayName = file.displayName,
                    mimeType = file.mimeType,
                    sizeBytes = file.size,
                    lastModified = file.lastModified,
                    presentInAlbum = true,
                    lastSeenScan = scanId,
                    updatedAt = now,
                )
            } else {
                val reason = SupportedImages.classify(file.mimeType, file.size)
                TrackedFileEntity(
                    environmentId = environmentId,
                    albumTreeUri = albumTreeUri,
                    documentId = file.documentId,
                    displayName = file.displayName,
                    mimeType = file.mimeType,
                    sizeBytes = file.size,
                    lastModified = file.lastModified,
                    state = if (reason == null) TrackedFileState.PENDING else TrackedFileState.REJECTED,
                    rejectionReason = reason,
                    uploadedAt = null,
                    updatedAt = now,
                    presentInAlbum = true,
                    lastSeenScan = scanId,
                )
            }
        }
    }
}
