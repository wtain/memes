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
}
