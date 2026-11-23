package com.memebrowser.app.data.tracker

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.map

/**
 * In-memory stand-in for the Room DAO. It re-implements the SQL semantics by hand, so it proves the
 * repository logic only; the SQL itself is checked by the androidTest TrackerDaoTest.
 */
class FakeTrackerDao : TrackerDao() {
    private val rows = MutableStateFlow<Map<Triple<String, String, String>, TrackedFileEntity>>(emptyMap())
    var transactionCount = 0
        private set

    var beforeChunk: (suspend () -> Unit)? = null

    val all: List<TrackedFileEntity> get() = rows.value.values.toList()

    private fun key(e: TrackedFileEntity) = Triple(e.environmentId, e.albumTreeUri, e.documentId)

    override suspend fun findByIds(environmentId: String, albumTreeUri: String, documentIds: List<String>) =
        documentIds.mapNotNull { rows.value[Triple(environmentId, albumTreeUri, it)] }

    override suspend fun upsertAll(rows: List<TrackedFileEntity>) {
        this.rows.value = this.rows.value + rows.associateBy { key(it) }
    }

    override suspend fun markMissing(environmentId: String, scanId: Long) {
        rows.value = rows.value.mapValues { (_, v) ->
            if (v.environmentId == environmentId && v.lastSeenScan != scanId) v.copy(presentInAlbum = false) else v
        }
    }

    override fun observeCounts(environmentId: String): Flow<TrackerCounts> = rows.map { map ->
        val env = map.values.filter { it.environmentId == environmentId }
        val present = env.filter { it.presentInAlbum }
        TrackerCounts(
            uploaded = env.count { it.state == TrackedFileState.UPLOADED },
            // Mirrors COALESCE(rejectionReason, '') = 'unsupported_type': a NULL reason stays in Y.
            supported = present.count {
                !(it.state == TrackedFileState.REJECTED && (it.rejectionReason ?: "") == SupportedImages.REASON_UNSUPPORTED_TYPE)
            },
            rejected = present.count { it.state == TrackedFileState.REJECTED },
            pending = present.count { it.state == TrackedFileState.PENDING },
            duplicates = env.count { it.state == TrackedFileState.UPLOADED && it.uploadAnswer == TrackerSql.ANSWER_DUPLICATE },
            unclassified = env.count { it.state == TrackedFileState.UPLOADED && it.uploadAnswer == null },
        )
    }

    override suspend fun nextPending(environmentId: String, limit: Int) =
        rows.value.values
            .filter { it.environmentId == environmentId && it.presentInAlbum && it.state == TrackedFileState.PENDING }
            .sortedWith(compareByDescending<TrackedFileEntity> { it.lastModified }.thenBy { it.documentId })
            .take(limit)

    override suspend fun markUploaded(
        environmentId: String, albumTreeUri: String, documentIds: List<String>, answer: String, at: Long,
    ) {
        documentIds.forEach { id ->
            val k = Triple(environmentId, albumTreeUri, id)
            // Mirrors `AND state != 'UPLOADED'`: an uploaded row keeps its first answer.
            rows.value[k]?.takeIf { it.state != TrackedFileState.UPLOADED }?.let {
                rows.value = rows.value + (k to it.copy(
                    state = TrackedFileState.UPLOADED, rejectionReason = null,
                    uploadedAt = it.uploadedAt ?: at, updatedAt = at, uploadAnswer = answer,
                ))
            }
        }
    }

    override suspend fun markRejected(
        environmentId: String, albumTreeUri: String, documentId: String, reason: String, at: Long,
    ) {
        val k = Triple(environmentId, albumTreeUri, documentId)
        rows.value[k]?.takeIf { it.state != TrackedFileState.UPLOADED }?.let {
            rows.value = rows.value + (k to it.copy(state = TrackedFileState.REJECTED, rejectionReason = reason, updatedAt = at))
        }
    }

    override fun observeRejected(environmentId: String): Flow<List<TrackedFileEntity>> = rows.map { map ->
        map.values
            .filter { it.environmentId == environmentId && it.presentInAlbum && it.state == TrackedFileState.REJECTED }
            .sortedWith(compareByDescending<TrackedFileEntity> { it.lastModified }.thenBy { it.documentId })
    }

    override suspend fun retryRejectedByReason(environmentId: String, reason: String, at: Long): Int {
        var n = 0
        rows.value = rows.value.mapValues { (_, v) ->
            if (v.environmentId == environmentId && v.presentInAlbum && v.state == TrackedFileState.REJECTED && v.rejectionReason == reason) {
                n++
                v.copy(state = TrackedFileState.PENDING, rejectionReason = null, updatedAt = at)
            } else v
        }
        return n
    }

    override suspend fun retryRejectedFile(
        environmentId: String, albumTreeUri: String, documentId: String, reason: String, at: Long,
    ): Int {
        val k = Triple(environmentId, albumTreeUri, documentId)
        val row = rows.value[k]?.takeIf { it.presentInAlbum && it.state == TrackedFileState.REJECTED && it.rejectionReason == reason } ?: return 0
        rows.value = rows.value + (k to row.copy(state = TrackedFileState.PENDING, rejectionReason = null, updatedAt = at))
        return 1
    }

    private fun uploadedHere(environmentId: String, albumTreeUri: String) =
        rows.value.values.filter {
            it.environmentId == environmentId && it.albumTreeUri == albumTreeUri && it.presentInAlbum && it.state == TrackedFileState.UPLOADED
        }

    private fun unchangedSinceUpload(it: TrackedFileEntity) =
        it.uploadedAt != null && it.lastModified > 0 && it.lastModified <= it.uploadedAt

    override suspend fun removableFiles(environmentId: String, albumTreeUri: String): List<RemovableFile> =
        uploadedHere(environmentId, albumTreeUri).filter(::unchangedSinceUpload).map { RemovableFile(it.documentId, it.uploadedAt!!) }

    override suspend fun uploadedButChangedCount(environmentId: String, albumTreeUri: String): Int =
        uploadedHere(environmentId, albumTreeUri).count { !unchangedSinceUpload(it) }

    override suspend fun maxScanId(environmentId: String): Long? =
        rows.value.values.filter { it.environmentId == environmentId }.maxOfOrNull { it.lastSeenScan }

    override suspend fun deleteEnvironment(environmentId: String) {
        rows.value = rows.value.filterValues { it.environmentId != environmentId }
    }

    override suspend fun applyScanChunk(
        environmentId: String, albumTreeUri: String, files: List<ScannedFile>, scanId: Long, now: Long,
    ) {
        require(files.size <= SCAN_CHUNK_SIZE)
        transactionCount++
        // Lets a test interleave coroutines at the point where real Room would suspend on its executor.
        beforeChunk?.invoke()
        super.applyScanChunk(environmentId, albumTreeUri, files, scanId, now)
    }
}
