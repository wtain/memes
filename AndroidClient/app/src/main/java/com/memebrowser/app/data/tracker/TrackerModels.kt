package com.memebrowser.app.data.tracker

/** A file as listed by the album scan (the SAF listing that produces it is A2). */
data class ScannedFile(
    val documentId: String,
    val displayName: String,
    val mimeType: String,
    val size: Long,
    val lastModified: Long,
)

/**
 * X = uploaded, Y = supported, R = rejected, P = pending. See the A1 design for exact definitions.
 * A7: [duplicates] and [unclassified] are the parts of X the server answered `duplicate` and the ones uploaded before the
 * answer was recorded (same environment scope as X).
 */
data class TrackerCounts(
    val uploaded: Int,
    val supported: Int,
    val rejected: Int,
    val pending: Int,
    val duplicates: Int = 0,
    val unclassified: Int = 0,
) {
    companion object {
        val EMPTY = TrackerCounts(0, 0, 0, 0)
    }
}
