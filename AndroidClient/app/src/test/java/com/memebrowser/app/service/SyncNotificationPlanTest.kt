package com.memebrowser.app.service

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SyncNotificationPlanTest {

    @Test
    fun notificationId_isStableAndNeverZeroOrPlaceholder() {
        val ids = listOf("builtin-general", "builtin-metal", "builtin-it", "", "x", "custom-123").map {
            SyncNotificationPlan.notificationId(it)
        }
        assertEquals(SyncNotificationPlan.notificationId("builtin-general"), ids[0])
        ids.forEach { assertTrue("id $it", it >= 2) }
        ids.forEach { assertNotEquals(SyncNotificationPlan.PLACEHOLDER_ID, it) }
        assertEquals(ids.size, ids.toSet().size)
    }

    @Test
    fun notificationId_handlesNegativeAndExtremeHashes() {
        // "polygenelubricants".hashCode() == Int.MIN_VALUE
        assertTrue(SyncNotificationPlan.notificationId("polygenelubricants") >= 2)
        val maxHashed = (Int.MAX_VALUE and 0x3FFFFFFF) + 2
        assertTrue(maxHashed > 0)
    }

    @Test
    fun foreground_keepsTheCurrentWhileItRuns() {
        assertEquals("b", SyncNotificationPlan.foreground(listOf("a", "b"), current = "b"))
    }

    @Test
    fun foreground_promotesTheOldestWhenTheCurrentEnded() {
        assertEquals("b", SyncNotificationPlan.foreground(listOf("b", "c"), current = "a"))
        assertEquals("a", SyncNotificationPlan.foreground(listOf("a", "c"), current = null))
    }

    @Test
    fun foreground_isNullWhenNothingRuns() {
        assertNull(SyncNotificationPlan.foreground(emptyList(), current = "a"))
    }

    @Test
    fun order_keepsKnownAndAppendsNewSorted() {
        assertEquals(listOf("m", "a", "z"), SyncNotificationPlan.order(listOf("m"), setOf("z", "m", "a")))
        assertEquals(listOf("a", "b"), SyncNotificationPlan.order(emptyList(), setOf("b", "a")))
        // An environment that left the active set keeps its place until the service removes it itself.
        assertEquals(listOf("m", "a"), SyncNotificationPlan.order(listOf("m", "a"), emptySet()))
    }
}
