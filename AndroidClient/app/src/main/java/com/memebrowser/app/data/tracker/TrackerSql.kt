package com.memebrowser.app.data.tracker

/**
 * Literals used inside the @Query strings of [TrackerDao] (annotation values must be compile-time constants,
 * so they cannot be read from the enum). TrackerSqlConstantsTest checks them against [TrackedFileState].
 */
internal object TrackerSql {
    const val PENDING = "'PENDING'"
    const val UPLOADED = "'UPLOADED'"
    const val REJECTED = "'REJECTED'"
    const val UNSUPPORTED_TYPE = "'${SupportedImages.REASON_UNSUPPORTED_TYPE}'"
    const val SERVER_ERROR = "'${RejectionReasons.SERVER_ERROR}'"
}

/** Reason codes the tracker itself must know (the others are opaque text to it). SyncEngine writes [SERVER_ERROR]. */
object RejectionReasons {
    /** A file the server failed on when sent alone (HTTP 500); the only reason a user can retry (A4). */
    const val SERVER_ERROR = "server_error"
}
