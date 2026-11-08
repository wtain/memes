package com.memebrowser.app.ui.sync

import com.memebrowser.app.data.album.ChangeAlbumPlan
import com.memebrowser.app.data.album.ScanFailure
import com.memebrowser.app.data.album.ScanState
import com.memebrowser.app.data.sync.SyncEngine
import com.memebrowser.app.data.sync.SyncError
import com.memebrowser.app.data.sync.SyncState
import com.memebrowser.app.data.sync.SyncSummary
import com.memebrowser.app.data.sync.WaitReason
import com.memebrowser.app.data.tracker.AlbumSelection
import com.memebrowser.app.data.tracker.RejectionReasons
import com.memebrowser.app.data.tracker.SupportedImages
import com.memebrowser.app.data.tracker.TrackerCounts
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SyncMappersTest {

    private val fmt: TimeFormatter = { "T$it" }
    private val counts = TrackerCounts(uploaded = 4, supported = 10, rejected = 1, pending = 5)
    private val running = SyncState.Running(1, 1, 5, SyncSummary())

    // ---- progress ------------------------------------------------------------------------------------------------

    @Test
    fun `progress fraction is x over y, zero for an empty album and capped at one`() {
        assertEquals(0.4f, progressFraction(counts), 0.0001f)
        assertEquals(0f, progressFraction(TrackerCounts.EMPTY), 0f)
        assertEquals(1f, progressFraction(TrackerCounts(uploaded = 7, supported = 5, rejected = 0, pending = 0)), 0f)
    }

    // ---- enablement ---------------------------------------------------------------------------------------------

    @Test
    fun `sync now needs an album, pending files, a granted album and no run`() {
        assertTrue(syncControls(true, counts, ScanState.Idle, SyncState.Idle, busy = false).canSync)
        assertFalse(syncControls(false, counts, ScanState.Idle, SyncState.Idle, false).canSync)
        assertFalse(syncControls(true, counts.copy(pending = 0), ScanState.Idle, SyncState.Idle, false).canSync)
        assertFalse(syncControls(true, counts, ScanState.PermissionLost, SyncState.Idle, false).canSync)
        assertFalse(syncControls(true, counts, ScanState.Idle, running, false).canSync)
        assertFalse(syncControls(true, counts, ScanState.Idle, SyncState.Idle, busy = true).canSync)
        // A failed or finished earlier run does not block the next one.
        assertTrue(syncControls(true, counts, ScanState.Done(1), SyncState.Finished(SyncSummary(), 1), false).canSync)
    }

    @Test
    fun `stop is offered while running or waiting but not once stopping`() {
        assertTrue(syncControls(true, counts, ScanState.Idle, running, false).canStop)
        val waiting = SyncState.Waiting(WaitReason.PACING, 1, 0, SyncSummary())
        assertTrue(syncControls(true, counts, ScanState.Idle, waiting, false).canStop)
        assertFalse(syncControls(true, counts, ScanState.Idle, SyncState.Stopping(SyncSummary()), false).canStop)
        assertFalse(syncControls(true, counts, ScanState.Idle, SyncState.Idle, false).canStop)
    }

    @Test
    fun `refresh needs an album and no running scan, choose album only without one`() {
        assertTrue(syncControls(true, counts, ScanState.Idle, SyncState.Idle, false).canRefresh)
        assertFalse(syncControls(true, counts, ScanState.Scanning, SyncState.Idle, false).canRefresh)
        assertFalse(syncControls(false, counts, ScanState.Idle, SyncState.Idle, false).canRefresh)
        assertTrue(syncControls(false, counts, ScanState.Idle, SyncState.Idle, false).canChooseAlbum)
        assertFalse(syncControls(true, counts, ScanState.Idle, SyncState.Idle, false).canChooseAlbum)
        assertTrue(syncControls(true, counts, ScanState.Idle, SyncState.Idle, false).canChangeAlbum)
        assertTrue(syncControls(true, counts, ScanState.Idle, running, false).batchSizeAppliesNextBatch)
        assertFalse(syncControls(true, counts, ScanState.Idle, SyncState.Idle, false).batchSizeAppliesNextBatch)
    }

    // ---- scan text -----------------------------------------------------------------------------------------------

    @Test
    fun `scan text covers every state`() {
        assertNull(scanStatusText(ScanState.Idle, fmt))
        assertEquals("Scanning the album...", scanStatusText(ScanState.Scanning, fmt))
        assertEquals("Last refreshed at T42", scanStatusText(ScanState.Done(42), fmt))
        assertTrue(scanStatusText(ScanState.PermissionLost, fmt)!!.startsWith("Access to the album was lost"))
        ScanFailure.values().forEach { assertNotNull(scanStatusText(ScanState.Failed(it), fmt)) }
        assertTrue(scanStatusText(ScanState.Failed(ScanFailure.LISTING_INCOMPLETE), fmt)!!.contains("unavailable"))
        assertTrue(scanIsProblem(ScanState.Failed(ScanFailure.TRACKER_ERROR)))
        assertTrue(scanIsProblem(ScanState.PermissionLost))
        assertFalse(scanIsProblem(ScanState.Done(1)))
    }

    // ---- sync text -----------------------------------------------------------------------------------------------

    @Test
    fun `waiting texts name the reason and the resume time`() {
        val rate = SyncState.Waiting(WaitReason.RATE_LIMITED, 99, 1, SyncSummary())
        assertEquals("Server asked us to wait, resuming at T99", syncStatusText(rate, fmt))
        val pace = SyncState.Waiting(WaitReason.PACING, 7, 0, SyncSummary())
        assertTrue(syncStatusText(pace, fmt)!!.startsWith("Pacing requests to stay under the server limit"))
    }

    @Test
    fun `running finished and stopped texts carry the summary`() {
        assertEquals("Starting...", syncStatusText(SyncState.Running(1, 0, 0, SyncSummary()), fmt))
        assertTrue(syncStatusText(SyncState.Running(1, 2, 50, SyncSummary(uploaded = 50)), fmt)!!.contains("batch 2 (50 files)"))
        assertEquals("Stopping...", syncStatusText(SyncState.Stopping(SyncSummary()), fmt))
        assertEquals(
            "Finished: 3 uploaded, 2 already on the server, 1 rejected",
            syncStatusText(SyncState.Finished(SyncSummary(3, 2, 1), 1), fmt),
        )
        assertTrue(syncStatusText(SyncState.Stopped(SyncSummary(uploaded = 1), 1), fmt)!!.contains("Unsent files stay pending"))
        assertNull(syncStatusText(SyncState.Idle, fmt))
    }

    @Test
    fun `every sync error has a distinct actionable message`() {
        val messages = SyncError.values().map { syncErrorMessage(it) }
        assertEquals(SyncError.values().size, messages.toSet().size)
        messages.forEach { assertTrue(it.isNotBlank()) }
        assertTrue(syncErrorMessage(SyncError.INBOX_FULL_OR_UNWRITABLE).contains("run ingestion or check the server"))
        assertTrue(syncErrorMessage(SyncError.SERVER, SyncEngine.BACKEND_TOO_OLD_MESSAGE).contains("too old"))
        assertFalse(syncErrorMessage(SyncError.SERVER, "HTTP 500").contains("too old"))
        assertTrue(syncErrorMessage(SyncError.UNEXPECTED, "bad body").contains("bad body"))
        val failed = SyncState.Failed(SyncError.DISK_LOW, "x", SyncSummary(), 1)
        assertEquals(syncErrorMessage(SyncError.DISK_LOW), syncStatusText(failed, fmt))
    }

    @Test
    fun `only a lost album asks to pick again`() {
        assertTrue(syncErrorNeedsRepick(SyncError.PERMISSION_LOST))
        assertTrue(syncErrorNeedsRepick(SyncError.NO_ALBUM))
        assertFalse(syncErrorNeedsRepick(SyncError.NETWORK))
    }

    @Test
    fun `terminal states are exactly finished stopped and failed`() {
        assertTrue(SyncState.Finished(SyncSummary(), 1).isTerminal())
        assertTrue(SyncState.Stopped(SyncSummary(), 1).isTerminal())
        assertTrue(SyncState.Failed(SyncError.NETWORK, "", SyncSummary(), 1).isTerminal())
        assertFalse(running.isTerminal())
        assertFalse(SyncState.Idle.isTerminal())
    }

    // ---- rejection reasons ----------------------------------------------------------------------------------------

    @Test
    fun `reason codes map to text and unknown reasons stay raw`() {
        assertEquals("Not a supported image type", rejectionReasonText(SupportedImages.REASON_UNSUPPORTED_TYPE))
        assertEquals("Larger than 20 MB", rejectionReasonText(SupportedImages.REASON_TOO_LARGE))
        assertEquals("The file is no longer available on the phone", rejectionReasonText(SyncEngine.REASON_FILE_UNAVAILABLE))
        assertEquals("The server failed on this file", rejectionReasonText(RejectionReasons.SERVER_ERROR))
        assertEquals("Not a supported image type (server)", rejectionReasonText("Unsupported file type: image/heic"))
        assertEquals("Not a supported image type (server)", rejectionReasonText("Unsupported file extension: .heic"))
        assertEquals("Larger than the server limit", rejectionReasonText("File too large: more than 1 bytes (max 1)"))
        assertEquals("The content is not a valid image", rejectionReasonText("File content does not match a supported image format"))
        assertEquals("Image is 1x1 pixels", rejectionReasonText("Image is 1x1 pixels"))
        assertEquals("Rejected", rejectionReasonText(null))
        assertEquals("Rejected", rejectionReasonText(" "))
    }

    @Test
    fun `only server errors can be retried`() {
        assertTrue(canRetry(RejectionReasons.SERVER_ERROR))
        listOf(null, SupportedImages.REASON_UNSUPPORTED_TYPE, SupportedImages.REASON_TOO_LARGE, SyncEngine.REASON_FILE_UNAVAILABLE, "File too large")
            .forEach { assertFalse(canRetry(it)) }
    }

    // ---- change dialog -------------------------------------------------------------------------------------------

    private fun plan(own: Boolean = false, regrant: Boolean = false) = ChangeAlbumPlan(
        "env", "content://new", "Memes", AlbumSelection("content://old", "Camera"),
        uploaded = 5, pending = 3, rejected = 1, looksLikeAppOwnFolder = own, isSameAlbum = false, needsRegrant = regrant,
    )

    @Test
    fun `dialog states the counts, both names and the optional notes`() {
        val plain = changeAlbumDialogLines(plan(), syncRunning = false)
        assertEquals(2, plain.size)
        assertTrue(plain[0].contains("Camera") && plain[0].contains("Memes"))
        assertTrue(plain[1].contains("5 uploaded") && plain[1].contains("3 pending") && plain[1].contains("1 rejected"))

        val all = changeAlbumDialogLines(plan(own = true, regrant = true), syncRunning = true)
        assertTrue(all.any { it.startsWith(WARNING_PREFIX) })
        assertTrue(all.any { it.contains("granted again") })
        assertTrue(all.any { it.contains("running sync will be stopped") })
    }
}
