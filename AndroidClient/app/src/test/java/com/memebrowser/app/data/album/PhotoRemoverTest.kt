package com.memebrowser.app.data.album

import com.memebrowser.app.data.tracker.RemovableFile
import org.junit.Assert.assertEquals
import org.junit.Test

class PhotoRemoverTest {
    private val upload = 5_000L
    private fun file(id: String, at: Long = upload) = RemovableFile(id, at)
    private fun cur(modified: Long, bucket: Long = 7) = CurrentImage(modified, bucket)

    @Test
    fun `an unchanged file in the album is removable`() {
        assertEquals(listOf(1L), RemovalFilter.unchanged(listOf(file("1")), mapOf(1L to cur(upload)), 7))
        assertEquals(listOf(1L), RemovalFilter.unchanged(listOf(file("1")), mapOf(1L to cur(upload - 1)), 7))
    }

    @Test
    fun `a file modified after the upload stays`() {
        assertEquals(emptyList<Long>(), RemovalFilter.unchanged(listOf(file("1")), mapOf(1L to cur(upload + 1)), 7))
    }

    @Test
    fun `a file that vanished, moved to another album or has no date stays out`() {
        val files = listOf(file("1"), file("2"), file("3"))
        val now = mapOf(2L to cur(1, bucket = 8), 3L to cur(0))
        assertEquals(emptyList<Long>(), RemovalFilter.unchanged(files, now, 7))
    }

    @Test
    fun `a non-numeric id is ignored`() {
        assertEquals(emptyList<Long>(), RemovalFilter.unchanged(listOf(file("content://x")), mapOf(1L to cur(1)), 7))
    }
}
