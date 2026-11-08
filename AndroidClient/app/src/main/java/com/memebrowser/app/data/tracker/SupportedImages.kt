package com.memebrowser.app.data.tracker

/**
 * The one place that decides whether a file can be sent to `/api/uploads`.
 * Mirrors UploadViewModel's ALLOWED_MIME_TYPES and MAX_FILE_SIZE (20 MB).
 */
object SupportedImages {
    const val MAX_FILE_SIZE_BYTES: Long = 20L * 1024 * 1024
    const val REASON_UNSUPPORTED_TYPE = "unsupported_type"
    const val REASON_TOO_LARGE = "too_large"

    val ALLOWED_MIME_TYPES: Set<String> = setOf(
        "image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/tiff"
    )

    /** Returns null when the file is uploadable, otherwise the local rejection reason. */
    fun classify(mimeType: String, sizeBytes: Long): String? = when {
        mimeType.lowercase() !in ALLOWED_MIME_TYPES -> REASON_UNSUPPORTED_TYPE
        sizeBytes > MAX_FILE_SIZE_BYTES -> REASON_TOO_LARGE
        else -> null
    }
}
