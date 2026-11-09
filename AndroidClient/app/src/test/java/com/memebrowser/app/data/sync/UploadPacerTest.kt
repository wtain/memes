package com.memebrowser.app.data.sync

import org.junit.Assert.assertEquals
import org.junit.Test

class UploadPacerTest {
    private var now = 0L
    private val pacer = UploadPacer({ now })

    @Test
    fun `nine requests pass at once, the tenth waits for the oldest to leave the window`() {
        repeat(9) { assertEquals(0L, pacer.waitMs()); pacer.record(); now += 100 }
        assertEquals(60_000L - 900, pacer.waitMs())
        now = 60_000
        assertEquals(0L, pacer.waitMs()) // the first request (t = 0) has just left the window
        pacer.record()
        assertEquals(100L, pacer.waitMs()) // next oldest is t = 100
    }

    @Test
    fun `the budget stays one under the server's limit`() {
        assertEquals(UploadPacer.SERVER_MAX_REQUESTS - 1, UploadPacer.MAX_REQUESTS)
    }
}
