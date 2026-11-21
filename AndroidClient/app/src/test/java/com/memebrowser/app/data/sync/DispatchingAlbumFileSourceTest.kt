package com.memebrowser.app.data.sync

import android.content.ContentResolver
import com.memebrowser.app.data.album.AlbumKey
import com.memebrowser.app.data.repository.AlbumFileSource
import io.mockk.mockk
import org.junit.Assert.assertSame
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.FileNotFoundException

class DispatchingAlbumFileSourceTest {
    private val safStream = ByteArrayInputStream(byteArrayOf(1))
    private val mediaStream = ByteArrayInputStream(byteArrayOf(2))
    private val saf = AlbumFileSource { _, _ -> safStream }
    private val media = AlbumFileSource { _, _ -> mediaStream }
    private val d = DispatchingAlbumFileSource(saf, media)

    @Test
    fun `media key goes to media`() {
        assertSame(mediaStream, d.open(AlbumKey.mediaStore(1, "X"), "5"))
    }

    @Test
    fun `tree uri goes to saf`() {
        assertSame(safStream, d.open("content://x/tree/a", "doc"))
    }

    @Test
    fun `security exception propagates unchanged`() {
        val boom = SecurityException("revoked")
        val failing = DispatchingAlbumFileSource(saf, AlbumFileSource { _, _ -> throw boom })
        val thrown = assertThrows(SecurityException::class.java) { failing.open(AlbumKey.mediaStore(1, "X"), "5") }
        assertSame(boom, thrown)
    }

    @Test
    fun `media source with non numeric id is file not found`() {
        val source = MediaStoreAlbumFileSource(mockk<ContentResolver>())
        assertThrows(FileNotFoundException::class.java) { source.open(AlbumKey.mediaStore(1, "X"), "abc") }
    }
}
