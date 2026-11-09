package com.memebrowser.app.data.tracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SupportedImagesTest {

    @Test
    fun `allowed types within the limit have no rejection`() {
        listOf("image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/tiff").forEach {
            assertNull(it, SupportedImages.classify(it, 1024))
        }
    }

    @Test
    fun `mime comparison is case-insensitive`() {
        assertNull(SupportedImages.classify("IMAGE/JPEG", 10))
    }

    @Test
    fun `unsupported types are rejected with unsupported_type`() {
        assertEquals(SupportedImages.REASON_UNSUPPORTED_TYPE, SupportedImages.classify("image/heic", 10))
        assertEquals(SupportedImages.REASON_UNSUPPORTED_TYPE, SupportedImages.classify("video/mp4", 10))
        assertEquals(SupportedImages.REASON_UNSUPPORTED_TYPE, SupportedImages.classify("", 10))
    }

    @Test
    fun `exactly 20 MB is allowed and one byte more is too large`() {
        val limit = 20L * 1024 * 1024
        assertNull(SupportedImages.classify("image/png", limit))
        assertEquals(SupportedImages.REASON_TOO_LARGE, SupportedImages.classify("image/png", limit + 1))
    }

    @Test
    fun `type is checked before size`() {
        assertEquals(
            SupportedImages.REASON_UNSUPPORTED_TYPE,
            SupportedImages.classify("image/heic", 50L * 1024 * 1024)
        )
    }
}
