package com.memebrowser.app.data.sync

import com.memebrowser.app.data.tracker.TrackerCounts
import com.memebrowser.app.ui.sync.syncErrorMessage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SyncNotificationMapperTest {
    private val counts = TrackerCounts(uploaded = 120, supported = 400, rejected = 5, pending = 275)
    private val summary = SyncSummary(uploaded = 400, duplicates = 3, rejected = 0)
    private val running = SyncState.Running(startedAt = 0, batchNumber = 1, currentBatchSize = 50, summary = SyncSummary())

    private fun map(state: SyncState, c: TrackerCounts = counts, now: Long = 1_000) =
        SyncNotificationMapper.map("General", state, c, now)

    @Test
    fun idle_isNull() = assertNull(map(SyncState.Idle))

    @Test
    fun running_showsCountsAndProgress() {
        val m = map(running)!!
        assertEquals("General: syncing", m.title)
        assertEquals("120 of 400 uploaded", m.text)
        assertEquals(Progress(125, 400), m.progress)
        assertTrue(m.ongoing)
        assertTrue(m.canStop)
    }

    @Test
    fun zeroSupported_hasNoProgress() {
        assertNull(map(running, TrackerCounts(0, 0, 0, 0))!!.progress)
    }

    private fun waiting(reason: WaitReason, resumeAt: Long = 0, attempt: Int = 0) =
        map(SyncState.Waiting(reason, resumeAt, attempt, SyncSummary()))!!

    private fun assertWait(text: String, m: SyncNotificationModel) {
        assertEquals("General: syncing", m.title)
        assertEquals(text, m.text)
        assertEquals(Progress(125, 400), m.progress)
        assertTrue(m.ongoing)
        assertTrue(m.canStop)
    }

    @Test
    fun needsWifi() = assertWait("Waiting for Wi-Fi", waiting(WaitReason.NEEDS_WIFI))

    @Test
    fun noNetwork() = assertWait("Waiting for network", waiting(WaitReason.NO_NETWORK))

    @Test
    fun pacing() = assertWait("Pausing to respect the server limit", waiting(WaitReason.PACING))

    @Test
    fun rateLimited() = assertWait("Pausing to respect the server limit", waiting(WaitReason.RATE_LIMITED, 5_000, 1))

    @Test
    fun retryBackoff_roundsUp() =
        assertWait("Retrying in 30 s (attempt 2 of 8)", waiting(WaitReason.RETRY_BACKOFF, 1_000 + 29_001, 2))

    @Test
    fun retryBackoff_clampsAtZero() =
        assertWait("Retrying in 0 s (attempt 1 of 8)", waiting(WaitReason.RETRY_BACKOFF, 500, 1))

    @Test
    fun retryBackoff_usesEngineConstant() {
        val m = waiting(WaitReason.RETRY_BACKOFF, 2_000, 3)
        assertTrue(m.text.endsWith("of ${SyncEngine.MAX_RETRY_ATTEMPTS})"))
    }

    @Test
    fun stopping() {
        val m = map(SyncState.Stopping(SyncSummary()))!!
        assertEquals("General: syncing", m.title)
        assertEquals("Stopping...", m.text)
        assertTrue(m.ongoing)
        assertFalse(m.canStop)
    }

    private fun assertTerminal(m: SyncNotificationModel) {
        assertFalse(m.ongoing)
        assertFalse(m.canStop)
        assertNull(m.progress)
    }

    @Test
    fun finished() {
        val m = map(SyncState.Finished(summary, 0))!!
        assertEquals("General: sync finished, 400 uploaded", m.title)
        assertEquals("120 of 400 uploaded", m.text)
        assertTerminal(m)
    }

    @Test
    fun stopped() {
        val m = map(SyncState.Stopped(summary, 0))!!
        assertEquals("General: sync stopped", m.title)
        assertEquals("120 of 400 uploaded", m.text)
        assertTerminal(m)
    }

    @Test
    fun failed() {
        val m = map(SyncState.Failed(SyncError.DISK_LOW, "x", summary, 0))!!
        assertEquals("General: sync failed", m.title)
        assertEquals(syncErrorMessage(SyncError.DISK_LOW, "x"), m.text)
        assertTerminal(m)
    }
}
