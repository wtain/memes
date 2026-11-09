package com.memebrowser.app.data.sync

import com.memebrowser.app.data.model.UploadResponse
import com.memebrowser.app.data.tracker.TrackerRepository

/** What the engine writes into the tracker; a seam so tests can interleave cancellation with the writes. */
internal interface OutcomeStore {
    suspend fun markUploaded(environmentId: String, treeUri: String, documentIds: List<String>)
    suspend fun markRejected(environmentId: String, treeUri: String, documentId: String, reason: String)
}

internal class TrackerOutcomeStore(private val tracker: TrackerRepository) : OutcomeStore {
    override suspend fun markUploaded(environmentId: String, treeUri: String, documentIds: List<String>) =
        tracker.markUploaded(environmentId, treeUri, documentIds)

    override suspend fun markRejected(environmentId: String, treeUri: String, documentId: String, reason: String) =
        tracker.markRejected(environmentId, treeUri, documentId, reason)
}

internal sealed interface MappedResponse {
    /**
     * The backend predates the per-file `index` (B1): it answers `status: "ok"` or entries without `index`, and it stored
     * the files outside the ingestion inbox. Nothing may be marked uploaded.
     */
    data object BackendTooOld : MappedResponse

    /** Positions in the sent batch: [uploaded] (accepted or duplicate; [duplicates] is the subset) and rejected with reason. */
    class Ok(
        val uploaded: List<Int>,
        val duplicates: Set<Int>,
        val rejected: List<Pair<Int, String>>,
    ) : MappedResponse {
        val settled: Int get() = uploaded.size + rejected.size
    }
}

internal const val STATUS_ACCEPTED = "accepted"
internal const val STATUS_DUPLICATE = "duplicate"
internal const val STATUS_OLD_BACKEND = "ok"
private const val MAX_REASON_LENGTH = 300

/**
 * Maps a 200 body onto a batch of [batchSize] files by the per-file `index` (position in the request). Pure.
 * A file is uploaded only through an explicit `accepted` / `duplicate` entry; an unknown status, an out-of-range index or a
 * second entry for the same index settles nothing (the first entry wins).
 */
internal fun mapResponse(body: UploadResponse, batchSize: Int): MappedResponse {
    if (body.uploaded.any { it.index == null || it.status == STATUS_OLD_BACKEND } || body.failed.any { it.index == null }) {
        return MappedResponse.BackendTooOld
    }
    val done = HashSet<Int>()
    val uploaded = ArrayList<Int>()
    val duplicates = HashSet<Int>()
    for (u in body.uploaded) {
        if (u.status != STATUS_ACCEPTED && u.status != STATUS_DUPLICATE) continue
        val pos = u.index?.takeIf { it in 0 until batchSize } ?: continue
        if (!done.add(pos)) continue
        uploaded += pos
        if (u.status == STATUS_DUPLICATE) duplicates += pos
    }
    val rejected = ArrayList<Pair<Int, String>>()
    for (f in body.failed) {
        val pos = f.index?.takeIf { it in 0 until batchSize } ?: continue
        if (!done.add(pos)) continue
        rejected += pos to f.reason.take(MAX_REASON_LENGTH)
    }
    return MappedResponse.Ok(uploaded, duplicates, rejected)
}
