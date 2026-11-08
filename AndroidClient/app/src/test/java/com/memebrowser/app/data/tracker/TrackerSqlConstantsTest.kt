package com.memebrowser.app.data.tracker

import org.junit.Assert.assertEquals
import org.junit.Test

/** Fails when an enum entry or reason constant is renamed without updating the literals used in TrackerDao's SQL. */
class TrackerSqlConstantsTest {
    @Test
    fun `sql state literals match the enum names`() {
        assertEquals(TrackerSql.PENDING, "'${TrackedFileState.PENDING.name}'")
        assertEquals(TrackerSql.UPLOADED, "'${TrackedFileState.UPLOADED.name}'")
        assertEquals(TrackerSql.REJECTED, "'${TrackedFileState.REJECTED.name}'")
        assertEquals(
            setOf("PENDING", "UPLOADED", "REJECTED"),
            TrackedFileState.values().map { it.name }.toSet(),
        )
    }

    @Test
    fun `sql unsupported type literal matches the reason constant`() {
        assertEquals(TrackerSql.UNSUPPORTED_TYPE, "'${SupportedImages.REASON_UNSUPPORTED_TYPE}'")
        assertEquals("unsupported_type", SupportedImages.REASON_UNSUPPORTED_TYPE)
    }
}
