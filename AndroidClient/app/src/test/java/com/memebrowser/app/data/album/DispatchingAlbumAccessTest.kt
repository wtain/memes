package com.memebrowser.app.data.album

import io.mockk.mockk
import io.mockk.verify
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DispatchingAlbumAccessTest {
    private val saf = mockk<AlbumAccess>(relaxed = true)
    private val media = mockk<AlbumAccess>(relaxed = true)
    private val dispatcher = DispatchingAlbumAccess(saf, media)
    private val mediaKey = AlbumKey.mediaStore(42L, "Memes")
    private val safKey = "content://com.android.externalstorage.documents/tree/primary%3AMemes"

    @Test
    fun mediaKeyGoesToMediaDelegate() {
        dispatcher.persist(mediaKey)
        dispatcher.isGranted(mediaKey)
        dispatcher.release(mediaKey)
        verify(exactly = 1) { media.persist(mediaKey) }
        verify(exactly = 1) { media.isGranted(mediaKey) }
        verify(exactly = 1) { media.release(mediaKey) }
        verify(exactly = 0) { saf.persist(any()) }
        verify(exactly = 0) { saf.isGranted(any()) }
        verify(exactly = 0) { saf.release(any()) }
    }

    @Test
    fun safKeyGoesToSafDelegate() {
        dispatcher.persist(safKey)
        dispatcher.isGranted(safKey)
        dispatcher.release(safKey)
        verify(exactly = 1) { saf.persist(safKey) }
        verify(exactly = 1) { saf.isGranted(safKey) }
        verify(exactly = 1) { saf.release(safKey) }
        verify(exactly = 0) { media.persist(any()) }
        verify(exactly = 0) { media.isGranted(any()) }
        verify(exactly = 0) { media.release(any()) }
    }

    private fun access(a: MediaAccess) = MediaStoreAlbumAccess { a }

    @Test
    fun onlyFullCounts() {
        val key = AlbumKey.mediaStore(1L, "x")
        for (a in listOf(MediaAccess.PARTIAL, MediaAccess.DENIED, MediaAccess.BLOCKED)) {
            assertFalse(access(a).isGranted(key))
            assertFalse(access(a).persist(key))
        }
        assertTrue(access(MediaAccess.FULL).isGranted(key))
        assertTrue(access(MediaAccess.FULL).persist(key))
    }

    @Test
    fun releaseNeverThrows() {
        access(MediaAccess.FULL).release(AlbumKey.mediaStore(1L, "x"))
        access(MediaAccess.DENIED).release("anything")
    }
}
