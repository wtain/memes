package com.memebrowser.app.data.tracker

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ScanReconcilerTest {

    private val env = "builtin-general"
    private val album = "content://tree/album"

    private fun scanned(id: String, mime: String = "image/jpeg", size: Long = 100, modified: Long = 1, name: String = "$id.jpg") =
        ScannedFile(id, name, mime, size, modified)

    private fun existing(
        id: String,
        state: TrackedFileState,
        reason: String? = null,
        uploadedAt: Long? = null,
        size: Long = 100,
        present: Boolean = true,
    ) = TrackedFileEntity(
        environmentId = env, albumTreeUri = album, documentId = id, displayName = "$id.jpg",
        mimeType = "image/jpeg", sizeBytes = size, lastModified = 1, state = state,
        rejectionReason = reason, uploadedAt = uploadedAt, updatedAt = 5, presentInAlbum = present, lastSeenScan = 1,
    )

    private fun run(existing: List<TrackedFileEntity>, files: List<ScannedFile>) =
        ScanReconciler.reconcile(env, album, existing, files, scanId = 9, now = 100).associateBy { it.documentId }

    @Test
    fun `new supported file becomes pending and present`() {
        val row = run(emptyList(), listOf(scanned("a")))["a"]!!
        assertEquals(TrackedFileState.PENDING, row.state)
        assertNull(row.rejectionReason)
        assertTrue(row.presentInAlbum)
        assertEquals(9L, row.lastSeenScan)
        assertEquals(100L, row.updatedAt)
    }

    @Test
    fun `new unsupported type is rejected locally`() {
        val row = run(emptyList(), listOf(scanned("a", mime = "image/heic")))["a"]!!
        assertEquals(TrackedFileState.REJECTED, row.state)
        assertEquals(SupportedImages.REASON_UNSUPPORTED_TYPE, row.rejectionReason)
    }

    @Test
    fun `new oversized file is rejected locally`() {
        val row = run(emptyList(), listOf(scanned("a", size = 21L * 1024 * 1024)))["a"]!!
        assertEquals(TrackedFileState.REJECTED, row.state)
        assertEquals(SupportedImages.REASON_TOO_LARGE, row.rejectionReason)
    }

    @Test
    fun `uploaded file whose size and mtime changed stays uploaded with its original uploadedAt`() {
        val row = run(
            listOf(existing("a", TrackedFileState.UPLOADED, uploadedAt = 50)),
            listOf(scanned("a", size = 999, modified = 77))
        )["a"]!!
        assertEquals(TrackedFileState.UPLOADED, row.state)
        assertEquals(50L, row.uploadedAt)
        assertEquals(999L, row.sizeBytes)
        assertEquals(77L, row.lastModified)
    }

    @Test
    fun `rejected row stays rejected even if it now looks supported`() {
        val row = run(
            listOf(existing("a", TrackedFileState.REJECTED, reason = "bad content")),
            listOf(scanned("a"))
        )["a"]!!
        assertEquals(TrackedFileState.REJECTED, row.state)
        assertEquals("bad content", row.rejectionReason)
    }

    @Test
    fun `row that reappears becomes present again`() {
        val row = run(listOf(existing("a", TrackedFileState.PENDING, present = false)), listOf(scanned("a")))["a"]!!
        assertTrue(row.presentInAlbum)
        assertEquals(TrackedFileState.PENDING, row.state)
    }

    @Test
    fun `rename refreshes the display name`() {
        val row = run(listOf(existing("a", TrackedFileState.UPLOADED)), listOf(scanned("a", name = "new.jpg")))["a"]!!
        assertEquals("new.jpg", row.displayName)
    }
}
